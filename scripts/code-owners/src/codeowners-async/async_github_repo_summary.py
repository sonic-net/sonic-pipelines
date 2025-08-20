import warnings
import random
from datetime import datetime, timezone, timedelta
import logging
import aiohttp
import asyncio
import os
import ssl
import certifi
from typing import Dict, Any
from urllib.parse import quote_plus

from async_commit_stream.async_helpers import (
    get_all_commit_stats,
    GitCommitLocal,
)
from async_commit_stream.contributor import Contributor
from async_commit_stream.organization import (
    ORGANIZATION,
    organization_by_company,
)

logger = logging.getLogger(__name__)


class AsyncGitHubRepoSummary:
    MAX_CONCURRENT_API_REQUESTS = 1000
    MAX_UNRESOLVED_COMMITS = 1000
    COMMIT_RESOLVE_WORKERS = 64

    GITHUB_API_ENDPOINT = "https://api.github.com/"
    GITHUB_API_TOKENS_ENV_VAR = "GITHUB_API_TOKENS"
    GITHUB_API_TOKENS = [
        token
        for token in map(
            str.strip, os.environ.get(GITHUB_API_TOKENS_ENV_VAR, "").split(",")
        )
        if token
    ]

    def build_api_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.GITHUB_API_TOKENS:
            token = random.choice(self.GITHUB_API_TOKENS)
            headers["Authorization"] = f"token {token}"
        return headers

    async def check_api_rate(self, response):
        # exponentiate the time at every request
        if (
            datetime.now(timezone.utc).timestamp() - self.expo_wait_last_update
            > self.expo_wait_time_incr_wait
            and self.expo_wait_time < 3600
        ):
            self.expo_wait_time <<= 1
            logger.warning(
                f"Doubling the additional wait time to: {self.expo_wait_time}s"
            )

        if response.status == 429:
            logger.warning("Got 429 error, too many requests")
        if response.headers.get("retry-after"):
            sleep_duration = self.expo_wait_time + int(
                response.headers["retry-after"]
            )
        else:
            # Handle the rate limit on GitHub
            if int(response.headers["x-ratelimit-remaining"]) > 0:
                # Error when the remaining limit is not 0,
                # return the error state
                raise ValueError(
                    "Invalid 403 response while rate limit is not over"
                )
            reset_time_utc_epoch = int(response.headers["x-ratelimit-reset"])
            ts_now_utc_epoch = datetime.now(timezone.utc).timestamp()
            sleep_duration = (
                max(reset_time_utc_epoch - ts_now_utc_epoch, 0)
                + self.expo_wait_time
            )
        wake_time = datetime.now() + timedelta(seconds=sleep_duration)

        logger.warning(
            f"{asyncio.current_task().get_name()} GitHub rate limit exceeded, sleeping for "
            f"{int(sleep_duration)} seconds until "
            f"{wake_time}",
        )
        await asyncio.sleep(sleep_duration)

    async def send_github_api_request(self, url: str, params=None):
        # limit the number of requests
        headers = self.build_api_headers()
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=self.ssl_context)
        ) as session:
            response = None
            async with self.github_api_sem:
                while True:
                    if response is not None:
                        # We came back where after 403 error
                        # check if we need to wait for
                        # the API cooldown
                        await self.check_api_rate(response)
                    async with session.get(
                        url=url,
                        headers=headers,
                        params=params,
                    ) as response:
                        if response.status == 403 or response.status == 429:
                            continue
                        if response.status != 200:
                            raise ValueError(
                                f"Bad API response: {response} for {url} {params}"
                            )
                        self.expo_wait_time = 1
                        return await response.json()

    async def github_id_lookup(self, github_id: int) -> Dict[str, Any]:
        if github_id == -1:
            return {
                "login": "non-github-bundle",
                "id": -1,
                "name": "Non-found emails bundle",
                "email": None,
                "company": None,
            }
        try:
            return self.gh_id_lookup_cache[github_id]
        except KeyError:
            pass
        response = await self.send_github_api_request(
            f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}user/{int(github_id)}",
        )
        self.gh_id_lookup_cache[github_id] = result = {
            k: response[k] for k in ["login", "id", "name", "email", "company"]
        }
        return result

    async def github_login_lookup(self, github_login: str) -> Dict[str, Any]:
        try:
            return self.gh_login_lookup_cache[github_login]
        except KeyError:
            pass
        response = await self.send_github_api_request(
            f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}users/{quote_plus(github_login)}",
        )
        self.gh_login_lookup_cache[github_login] = result = {
            k: response[k] for k in ["login", "id", "name", "email", "company"]
        }
        return result

    async def github_commit_author_id_lookup(self, commit_hash: str) -> int:
        """Look up the GitHub user ID of a commit's author.

        Args:
            commit_hash: The Git commit hash to look up.

        Returns:
            int: The GitHub user ID of the commit author, or -1 if not found.

        Raises:
            ValueError: If the commit is not found in the repository.
            RuntimeError: If the API request fails.
        """
        try:
            return (
                await self.send_github_api_request(
                    f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}repos/{quote_plus(self.owner)}/{quote_plus(self.repo)}"
                    f"/commits/{quote_plus(commit_hash)}"
                )
            )["author"]["id"]
        except TypeError:
            return -1

    def __init__(self):

        self.gh_login_lookup_cache = dict()
        self.gh_id_lookup_cache = dict()

        self.ssl_context = None
        self.github_api_sem = None
        self.to_resolve_commit_queue = None
        self.connector = None
        self.resolved_commit_queue = None

        # API wait params if hitting a rate limit
        self.expo_wait_time = 1  # current wait file
        self.expo_wait_last_update = datetime.now(timezone.utc).timestamp()
        self.expo_wait_time_incr_wait = 10  # after 10 s double the wait rime

        if not self.GITHUB_API_TOKENS:
            warnings.warn(
                f"No GitHub tokens passed in {self.GITHUB_API_TOKENS_ENV_VAR} env var. "
                "Only 60 GitHub requests per hour allowed: "
                "https://docs.github.com/en/rest/using-the-rest-api/"
                "rate-limits-for-the-rest-api?apiVersion=2022-11-28"
            )

    async def _initialize(
        self,
        contributors,
        folder_presets,
        repo_path: str,
        owner: str,
        repo: str,
        active_after: str,
    ):
        self.contributors = contributors
        self.folder_presets = folder_presets
        # Perform async operations here
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.github_api_sem = asyncio.Semaphore(
            AsyncGitHubRepoSummary.MAX_CONCURRENT_API_REQUESTS
        )
        self.connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        self.repo_path = repo_path
        self.owner = owner
        self.repo = repo
        self.active_after = active_after

        self.resolved_commit_queue = asyncio.Queue()
        self.to_resolve_commit_queue = asyncio.Queue(
            maxsize=AsyncGitHubRepoSummary.MAX_UNRESOLVED_COMMITS
        )

        # API wait params if hitting a rate limit
        self.expo_wait_time = 1  # current wait file
        self.expo_wait_last_update = datetime.now(timezone.utc).timestamp()
        self.expo_wait_time_incr_wait = 10  # after 10 s double the wait rime

    async def process_repository(
        self,
        contributors,
        folder_presets,
        repo_path: str,
        total_commit_count: int,
        owner: str,
        repo_name: str,
        active_after: str,
    ):
        await self._initialize(
            contributors,
            folder_presets,
            repo_path,
            owner,
            repo_name,
            active_after,
        )
        cnt = 0
        backlog_cnt = 0
        backlog_workers = [
            asyncio.create_task(self.resolve_commit())
            for _ in range(AsyncGitHubRepoSummary.COMMIT_RESOLVE_WORKERS)
        ]
        async for commit in get_all_commit_stats(repo_path):
            while not self.resolved_commit_queue.empty():
                commit, contributor = await self.resolved_commit_queue.get()
                self.resolved_commit_queue.task_done()
                cnt += 1
                logger.debug(
                    f"Processed {cnt} of {total_commit_count} commits"
                )
                if cnt % 1000 == 0:
                    await self.contributors.save_to_file()
                backlog_cnt -= 1
            await self.to_resolve_commit_queue.put(commit)
            backlog_cnt += 1

        while backlog_cnt > 0:
            commit, contributor = await self.resolved_commit_queue.get()
            self.resolved_commit_queue.task_done()
            cnt += 1
            logger.debug(f"Processed {cnt} of {total_commit_count} commits")
            if cnt % 1000 == 0:
                await self.contributors.save_to_file()
            backlog_cnt -= 1
        # send a sentinel to all workers to stop
        for _ in range(AsyncGitHubRepoSummary.COMMIT_RESOLVE_WORKERS):
            await self.to_resolve_commit_queue.put(None)
        await asyncio.gather(*backlog_workers)

    async def resolve_commit(self):
        while True:
            commit = await self.to_resolve_commit_queue.get()
            self.to_resolve_commit_queue.task_done()
            if commit is None:
                # done processing
                return
            contributor = self.contributors.by_email.get(commit.email)
            if not contributor:
                contributor = await self.build_contributor(commit)
            contributor.commit_count += 1
            if (
                contributor.last_commit_ts is None
                or contributor.last_commit_ts < commit.ts
            ):
                contributor.last_commit_ts = commit.ts
            await self.resolved_commit_queue.put((commit, contributor))

    async def build_contributor(self, commit: GitCommitLocal) -> Contributor:
        """Tries to look for the commit information in GitHub
        and build the candidate contributor object
        for later adding to the contributor collection

        Args:
            commit ():  GitCommit instance with local commit info

        Returns:
            Contributor instance
        """
        author_id = await self.github_commit_author_id_lookup(
            commit.commit_hash
        )
        if author_id == -1:
            github_info = await self.github_info_by_email(commit.email)
        else:
            github_info = await self.github_id_lookup(author_id)

        if not github_info:
            logger.warning(
                f"Commit {commit.commit_hash}, {commit.name}, "
                f"{commit.email}, unable to determine authors' GitHub id. "
                "Adding to the bundled contributor with GitHub id = -1"
            )
            github_info = await self.github_id_lookup(author_id)

        emails = {commit.email.lower()}
        if github_info["email"]:
            emails.add(github_info["email"].lower())
        organization = None
        if github_info["company"]:
            organization = organization_by_company(github_info["company"])
            if organization == ORGANIZATION.OTHER:
                organization = None

        contributor = Contributor(
            name=github_info["name"],
            emails=emails,
            organization=organization,
            github_id=github_info["id"],
            github_login=github_info["login"],
        )
        return self.contributors.add_update_contributor(contributor)

    async def github_info_by_email(self, email: str) -> Dict[str, Any]:
        """Look up GitHub user information by email addresses.

        Parses GitHub noreply emails
        (like 29677895+nikamirrr@users.noreply.github.com)
        to extract GitHub user ID and login information.

        Args:
            email: email address to search for GitHub information.

        Returns:
            Dict[str, Any]: Dictionary containing GitHub user information, or
            empty dict if not found.
        """
        local_part, domain = email.split("@")
        if domain == "users.noreply.github.com":
            try:
                id_str, github_login = local_part.split("+")
                return await self.github_id_lookup(int(id_str))
            except ValueError:
                # Try using the entire local part as login
                try:
                    return await self.github_login_lookup(local_part)
                except ValueError:
                    pass
        return {}
