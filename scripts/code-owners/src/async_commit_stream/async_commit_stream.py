import shlex
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


logger = logging.getLogger(__name__)


class AsyncGitHubRepoSummary:
    MAX_CONCURRENT_API_REQUESTS = 50
    PAGES_PER_BATCH = 5
    PAGE_SIZE = 100

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
        ):
            self.expo_wait_time <<= 1
            logger.warning(
                f"Doubling the additional wait time to: {self.expo_wait_time}s"
            )

        if response.status == 429:
            logger.warning("Got 429 error, too many requests")
        if response.headers["retry-after"]:
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
            "GitHub rate limit exceeded, sleeping for "
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
            while True:
                if response is not None:
                    # We came back where after 403 error
                    # check if we need to wait for
                    # the API cooldown
                    await self.check_api_rate(response)
                async with self.github_api_sem:
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

    async def get_commit_page(
        self,
        owner: str,
        repo: str,
        page: int,
        page_size: int,
        params: Dict[str, Any],
    ):
        if page >= self.first_empty_page:
            return page, []
        params_copy = params.copy()
        params_copy["per_page"] = page_size
        params_copy["page"] = page
        url = f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits"
        commits = await self.send_github_api_request(url, params_copy)
        # set the min page
        if len(commits) < page_size and page < self.first_empty_page:
            self.first_empty_page = page + 1
        return page, commits

    async def async_page_generator(
        self, owner: str, repo: str, params: Dict[str, str]
    ):
        curr_batch = [
            asyncio.create_task(
                self.get_commit_page(
                    owner, repo, page, AsyncGitHubRepoSummary.PAGE_SIZE, params
                )
            )
            for page in range(AsyncGitHubRepoSummary.PAGES_PER_BATCH)
        ]
        next_batch = []
        while curr_batch:
            for result in asyncio.as_completed(curr_batch):
                page, commits = await result
                if commits:
                    yield commits
                page += AsyncGitHubRepoSummary.PAGES_PER_BATCH
                if page < self.first_empty_page:
                    next_batch.append(
                        asyncio.create_task(
                            self.get_commit_page(
                                owner,
                                repo,
                                page,
                                AsyncGitHubRepoSummary.PAGE_SIZE,
                                params,
                            )
                        )
                    )
            # swap the batches and repeat
            next_batch, curr_batch = curr_batch, next_batch
            next_batch.clear()

    async def create_id_lookup_task(self, github_id: int):
        self.contributors[github_id] = asyncio.create_task(
            self.github_id_lookup(github_id)
        )

    async def github_id_lookup(self, github_id: int) -> Dict[str, Any]:
        if github_id == -1:
            return {
                "login": "non-github-bundle",
                "id": -1,
                "name": "Non-found emails bundle",
            }
        response = await self.send_github_api_request(
            f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}user/{int(github_id)}",
        )
        result = {
            k: response[k] for k in ["login", "id", "name", "email", "company"]
        }
        return result

    def __init__(self):
        self.ssl_context = None
        self.github_api_sem = None
        self.connector = None

        # API wait params if hitting a rate limit
        self.expo_wait_time = 1  # current wait file
        self.expo_wait_last_update = datetime.now(timezone.utc).timestamp()
        self.expo_wait_time_incr_wait = 10  # after 10 s double the wait rime

        self.first_empty_page = 1 << 63

        if not self.GITHUB_API_TOKENS:
            warnings.warn(
                f"No GitHub tokens passed in {self.GITHUB_API_TOKENS_ENV_VAR} env var. "
                "Only 60 GitHub requests per hour allowed: "
                "https://docs.github.com/en/rest/using-the-rest-api/"
                "rate-limits-for-the-rest-api?apiVersion=2022-11-28"
            )

    async def _initialize(
        self, repo_path: str, owner: str, repo: str, activity_from: str
    ):
        # Perform async operations here
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.github_api_sem = asyncio.Semaphore(
            AsyncGitHubRepoSummary.MAX_CONCURRENT_API_REQUESTS
        )
        self.connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        self.repo_path = repo_path
        self.owner = owner
        self.repo = repo
        self.activity_from = activity_from

        # API wait params if hitting a rate limit
        self.expo_wait_time = 1  # current wait file
        self.expo_wait_last_update = datetime.now(timezone.utc).timestamp()
        self.expo_wait_time_incr_wait = 10  # after 10 s double the wait rime

        self.first_empty_page = 1 << 63
        self.contributors = dict()
        await self.create_id_lookup_task(-1)
        self.contributor_commit_emails = {-1: set()}
        self.commit_details = dict()

    async def process_repository(
        self, repo_path: str, owner: str, repo: str, activity_from: str
    ):
        await self._initialize(repo_path, owner, repo, activity_from)
        total = 0
        # collect all commits after the cutoff date
        async for commit_page in self.async_page_generator(
            owner, repo, {"since": self.activity_from}
        ):
            total += len(commit_page)
            for commit in commit_page:
                # for every commit pull the contributor
                await self.process_commit(commit, True)
        print(
            total,
            len(self.commit_details),
            len(self.contributors),
            len(self.contributor_commit_emails),
        )
        # process the rest of the commits
        self.first_empty_page = 1 << 63  # reset the last page to max again
        async for commit_page in self.async_page_generator(
            owner, repo, {"until": self.activity_from}
        ):
            total += len(commit_page)
            for commit in commit_page:
                # for every commit pull the contributor
                await self.process_commit(commit, False)

        print(
            total,
            len(self.commit_details),
            len(self.contributors),
            len(self.contributor_commit_emails),
            sum(map(len, self.contributor_commit_emails.values())),
        )
        # Collect all contributors
        for result in asyncio.as_completed(self.contributors.values()):
            contributor = await result
            self.contributors[contributor["id"]] = contributor
        print(self.contributors)
        # collect all commits
        counts = 0
        for result in asyncio.as_completed(self.commit_details.values()):
            commit_stats = await result
            if commit_stats is not None:
                counts += 1
            # print(commit_stats)
            # print()
        print(f"Got status for {counts} commits")

    async def get_commit_stats(self, commit_sha: str):
        cmd = f"git -C {self.repo_path} log {shlex.quote(commit_sha)} --format='%H;%aI;%aE;%aN' --numstat -n 1"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"Unable to get commit stat: {cmd}. Err {stderr}")
        return stdout

    async def process_commit(self, commit, record_contributor: bool):
        try:
            github_id = commit["author"]["id"]
        except TypeError:
            commit["author"] = {"id": -1}
            github_id = -1
            logger.warning(
                "Missing author->id in commit {}, using -1".format(
                    commit["sha"]
                )
            )
        if record_contributor:
            if github_id not in self.contributors:
                self.contributor_commit_emails[github_id] = set()
                await self.create_id_lookup_task(github_id)
        # check if the contributor either exists or recorded
        # add emails and commits to process
        if github_id in self.contributors:
            try:
                self.contributor_commit_emails[github_id].add(
                    commit["commit"]["author"]["email"]
                )
            except KeyError:
                pass
            self.commit_details[commit["sha"]] = asyncio.create_task(
                self.get_commit_stats(commit["sha"])
            )


def main():
    # asyncio.run(pull_all_detailed_commits("sonic-net", "sonic-mgmt"))
    repo_summarizer = AsyncGitHubRepoSummary()
    asyncio.run(
        repo_summarizer.process_repository(
            "/Users/nmirin/workspace/repos/sonic-mgmt",
            "sonic-net",
            "sonic-mgmt",
            (
                datetime.combine(
                    datetime.fromisoformat("2023-08-15"),
                    datetime.min.time(),
                    timezone.utc,
                )
            ).isoformat()
            + "Z",
        )
    )


# # if pulled before the activity date, need to add the users
# # and start pulling their commits after that date
# if len(condition_tuple) == 1:
#     for commit in commits:
#         try:
#             github_id = int(commit["author"]["id"])
#         except KeyError:
#             logger.warning(f"Missing author->id in {commit}")
#             continue
#         if github_id not in self.contributors:
#             await self.create_id_lookup_task(int(github_id))
#             # add the tasks to find earlier commits for that user
#             for new_page in range(AsyncGitHubRepoSummary.PAGES_PER_BATCH):
#                 next_batch.append(
#                     asyncio.create_task(
#                         self.get_commit_page(
#                             owner,
#                             repo,
#                             (
#                                 self.activity_from,
#                                 commit["author"]["login"],
#                             ),
#                             new_page,
#                             AsyncGitHubRepoSummary.PAGE_SIZE,
#                         )
#                     )
#                 )
