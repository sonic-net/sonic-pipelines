import warnings
import random
from math import inf
from datetime import datetime, timezone, timedelta
import logging
import aiohttp
import asyncio
import os
import ssl
import certifi
from typing import Tuple, Dict
from urllib.parse import quote_plus


logger = logging.getLogger(__name__)


class AsyncGitHubRepoSummary:
    MAX_CONCURRENT_API_REQUESTS = 50
    PAGES_PER_BATCH = 100
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

    @staticmethod
    async def check_api_rate(response):
        # Handle the rate limit on GitHub
        if int(response.headers["x-ratelimit-remaining"]) > 0:
            # Error when the remaining limit is not 0,
            # return the error state
            raise ValueError(
                "Invalid 403 response while rate limit is not over"
            )
        reset_time_utc_epoch = int(response.headers["x-ratelimit-reset"])
        ts_now_utc = datetime.now(timezone.utc)
        ts_now_utc_epoch = ts_now_utc.timestamp()
        sleep_duration = max(reset_time_utc_epoch - ts_now_utc_epoch, 0) + 2.0
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
                        if response.status == 403:
                            continue
                        if response.status != 200:
                            raise ValueError(
                                f"Bad API response: {response} for {url} {params}"
                            )
                        return await response.json()

    async def get_commit_page(
        self,
        owner: str,
        repo: str,
        condition_tuple: Tuple,
        page: int,
        page_size: int,
    ):
        if page >= self.first_empty_page_by_query.get(condition_tuple, inf):
            return page, condition_tuple, []
        params = {"per_page": page_size, "page": page}
        if len(condition_tuple) == 1:
            # initially pull all commits after a certain date
            params["since"] = condition_tuple[0].isoformat() + "Z"
        elif len(condition_tuple) == 2:
            # before the certain date only pull commits of those who are active after the date
            params["until"] = condition_tuple[0].isoformat() + "Z"
            params["author"] = condition_tuple[1]
        else:
            raise ValueError(
                "1 or 2 values are required in the condition tuple: date and author"
            )
        url = f"{AsyncGitHubRepoSummary.GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits"
        commits = await self.send_github_api_request(url, params)
        # set the min page
        if not commits and page < self.first_empty_page_by_query.get(
            condition_tuple, inf
        ):
            self.first_empty_page_by_query[condition_tuple] = page
        return page, condition_tuple, commits

    async def async_page_generator(self, owner: str, repo: str):
        curr_batch = [
            asyncio.create_task(
                self.get_commit_page(
                    owner,
                    repo,
                    (self.activity_from,),
                    page,
                    AsyncGitHubRepoSummary.PAGE_SIZE,
                )
            )
            for page in range(AsyncGitHubRepoSummary.PAGES_PER_BATCH)
        ]
        next_batch = []
        while curr_batch:
            for result in asyncio.as_completed(curr_batch):
                page, condition_tuple, commits = await result
                if commits:
                    yield commits
                    # if pulled before the activity date, need to add the users
                    # and start pulling their commits after that date
                    if len(condition_tuple) == 1:
                        for commit in commits:
                            try:
                                github_id = int(commit["author"]["id"])
                            except KeyError:
                                logger.warning(
                                    f"Missing author->id in {commit}"
                                )
                                continue
                            if github_id not in self.contributors:
                                await self.create_id_lookup_task(
                                    int(github_id)
                                )
                                # add the tasks to find earlier commits for that user
                                for new_page in range(
                                    AsyncGitHubRepoSummary.PAGES_PER_BATCH
                                ):
                                    next_batch.append(
                                        asyncio.create_task(
                                            self.get_commit_page(
                                                owner,
                                                repo,
                                                (
                                                    self.activity_from,
                                                    commit["author"]["login"],
                                                ),
                                                new_page,
                                                AsyncGitHubRepoSummary.PAGE_SIZE,
                                            )
                                        )
                                    )

                page += AsyncGitHubRepoSummary.PAGES_PER_BATCH
                if page < self.first_empty_page_by_query.get(
                    condition_tuple, inf
                ):
                    next_batch.append(
                        asyncio.create_task(
                            self.get_commit_page(
                                owner,
                                repo,
                                condition_tuple,
                                page,
                                AsyncGitHubRepoSummary.PAGE_SIZE,
                            )
                        )
                    )
            # swap the batches and repeat
            next_batch, curr_batch = curr_batch, next_batch
            next_batch.clear()

    async def create_id_lookup_task(self, github_id: int):
        if github_id not in self.contributors:
            self.contributors[github_id] = asyncio.create_task(
                self.github_id_lookup(github_id)
            )

    async def github_id_lookup(self, github_id: int) -> Dict[str, str]:
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

        if not self.GITHUB_API_TOKENS:
            warnings.warn(
                f"No GitHub tokens passed in {self.GITHUB_API_TOKENS_ENV_VAR} env var. "
                "Only 60 GitHub requests per hour allowed: "
                "https://docs.github.com/en/rest/using-the-rest-api/"
                "rate-limits-for-the-rest-api?apiVersion=2022-11-28"
            )

    async def _initialize(
        self, owner: str, repo: str, activity_from: datetime
    ):
        # Perform async operations here
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.github_api_sem = asyncio.Semaphore(
            AsyncGitHubRepoSummary.MAX_CONCURRENT_API_REQUESTS
        )
        self.connector = aiohttp.TCPConnector(ssl=self.ssl_context)
        self.owner = owner
        self.repo = repo
        self.activity_from = activity_from

        self.first_empty_page_by_query = dict()
        self.contributors = dict()
        self.contributor_commit_emails = dict()
        self.commit_details = dict()

    async def process_repository(
        self, owner: str, repo: str, activity_from: datetime
    ):
        await self._initialize(owner, repo, activity_from)
        total = 0
        async for commit_page in self.async_page_generator(owner, repo):
            print(len(commit_page))
            total += len(commit_page)
        print(total)
        # Collect all contributors
        for result in asyncio.as_completed(self.contributors.values()):
            contributor = await result
            self.contributors[contributor["id"]] = contributor
        print(self.contributors)


# ca_bundle_path = certifi.where()
# ssl_context = ssl.create_default_context(cafile=ca_bundle_path)
#
#
# PAGES_PER_BATCH = 100
# PAGE_SIZE = 100
#
# MAX_CONCURRENT_API_REQUESTS = 100
# github_api_sem = None
#
#
# GITHUB_API_ENDPOINT = "https://api.github.com/"
# GITHUB_API_TOKENS_ENV_VAR = "GITHUB_API_TOKENS"
# GITHUB_API_TOKENS = [
#     token
#     for token in map(
#         str.strip, os.environ.get(GITHUB_API_TOKENS_ENV_VAR, "").split(",")
#     )
#     if token
# ]
# if not GITHUB_API_TOKENS:
#     warnings.warn(
#         f"No GitHub tokens passed in {GITHUB_API_TOKENS_ENV_VAR} env var. "
#         "Only 60 GitHub requests per hour allowed: "
#         "https://docs.github.com/en/rest/using-the-rest-api/"
#         "rate-limits-for-the-rest-api?apiVersion=2022-11-28"
#     )
#
#
# def build_api_headers():
#     headers = {
#         "Accept": "application/vnd.github+json",
#         "X-GitHub-Api-Version": "2022-11-28",
#     }
#     if GITHUB_API_TOKENS:
#         token = random.choice(GITHUB_API_TOKENS)
#         headers["Authorization"] = f"token {token}"
#     return headers
#
#
# async def check_api_rate(response):
#     # Handle the rate limit on GitHub
#     if int(response.headers["x-ratelimit-remaining"]) > 0:
#         # Error when the remaining limit is not 0,
#         # return the error state
#         raise ValueError("Invalid 403 response while rate limit is not over")
#     reset_time_utc_epoch = int(response.headers["x-ratelimit-reset"])
#     ts_now_utc = datetime.now(datetime.timezone.utc)
#     ts_now_utc_epoch = ts_now_utc.timestamp()
#     sleep_duration = max(reset_time_utc_epoch - ts_now_utc_epoch, 0) + 2.0
#     wake_time = datetime.now() + datetime.timedelta(seconds=sleep_duration)
#     logger.warning(
#         "GitHub rate limit exceeded, sleeping for "
#         f"{int(sleep_duration)} seconds until "
#         f"{wake_time}",
#     )
#     await asyncio.sleep(sleep_duration)
#
#
# async def send_github_api_request(url: str, params=None):
#     # limit the number of requests
#     headers = build_api_headers()
#     async with aiohttp.ClientSession(
#         connector=aiohttp.TCPConnector(ssl=ssl_context)
#     ) as session:
#         response = None
#         while True:
#             if response is not None:
#                 # We came back where after 403 error
#                 # check if we need to wait for
#                 # the API cooldown
#                 await check_api_rate(response)
#             async with github_api_sem:
#                 async with session.get(
#                     url=url,
#                     headers=headers,
#                     params=params,
#                 ) as response:
#                     if response.status == 403:
#                         continue
#                     if response.status != 200:
#                         raise ValueError(
#                             f"Bad API response: {response} for {url} {params}"
#                         )
#                     return await response.json()
#
#
# async def get_commit_page(
#     owner: str,
#     repo: str,
#     seen_empty_page: List[Any],
#     page: int,
#     page_size: int,
# ):
#     if seen_empty_page and page >= seen_empty_page[0]:
#         return page, []
#     params = {"per_page": page_size, "page": page}
#     url = f"{GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits"
#     return page, await send_github_api_request(url, params)
#
#
# async def async_page_generator(owner: str, repo: str):
#     seen_empty_page = [inf]
#     start_page = 0
#     while start_page <= seen_empty_page[0]:
#         for result in asyncio.as_completed(
#             tuple(
#                 asyncio.create_task(
#                     get_commit_page(
#                         owner, repo, seen_empty_page, page, PAGE_SIZE
#                     )
#                 )
#                 for page in range(
#                     start_page,
#                     int(min(seen_empty_page[0], start_page + PAGES_PER_BATCH)),
#                 )
#             )
#         ):
#             page, commits = await result
#             if not commits:
#                 seen_empty_page[0] = min(seen_empty_page[0], page)
#             else:
#                 yield commits
#         start_page += PAGES_PER_BATCH
#
#
# async def get_commit_details(owner: str, repo: str, commit):
#     commit_hash = commit["sha"]
#     url = f"{GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits/{quote_plus(commit_hash)}"
#     return await send_github_api_request(url)
#
#
# async def async_commit_generator(
#     owner: str, repo: str, commit_page: List[Any]
# ):
#     batch = [
#         asyncio.create_task(get_commit_details(owner, repo, commit))
#         for commit in commit_page
#     ]
#     for commit in asyncio.as_completed(batch):
#         yield await commit
#
#
# async def pull_all_detailed_commits(owner: str, repo: str):
#     global github_api_sem
#     github_api_sem = asyncio.Semaphore(MAX_CONCURRENT_API_REQUESTS)
#     total_commits = 0
#     async for commit_page in async_page_generator(owner, repo):
#         async for commit in async_commit_generator(owner, repo, commit_page):
#             print(commit["sha"])
#         total_commits += len(commit_page)
#         print(len(commit_page))
#     print(total_commits)


def main():
    # asyncio.run(pull_all_detailed_commits("sonic-net", "sonic-mgmt"))
    repo_summarizer = AsyncGitHubRepoSummary()
    asyncio.run(
        repo_summarizer.process_repository(
            "sonic-net",
            "sonic-mgmt",
            datetime.combine(
                datetime.fromisoformat("2023-08-15"),
                datetime.min.time(),
                timezone.utc,
            ),
        )
    )
