import warnings
import random
from math import inf
import datetime
import logging
import aiohttp
import asyncio
import os
import ssl
import certifi


logger = logging.getLogger(__name__)

ca_bundle_path = certifi.where()
ssl_context = ssl.create_default_context(cafile=ca_bundle_path)

from typing import List, Any
from urllib.parse import quote_plus

PAGES_PER_BATCH = 100
PAGE_SIZE = 100

MAX_CONCURRENT_API_REQUESTS = 100
github_api_sem = None


GITHUB_API_ENDPOINT = "https://api.github.com/"
GITHUB_API_TOKENS_ENV_VAR = "GITHUB_API_TOKENS"
GITHUB_API_TOKENS = [
    token
    for token in map(
        str.strip, os.environ.get(GITHUB_API_TOKENS_ENV_VAR, "").split(",")
    )
    if token
]
if not GITHUB_API_TOKENS:
    warnings.warn(
        f"No GitHub tokens passed in {GITHUB_API_TOKENS_ENV_VAR} env var. "
        "Only 60 GitHub requests per hour allowed: "
        "https://docs.github.com/en/rest/using-the-rest-api/"
        "rate-limits-for-the-rest-api?apiVersion=2022-11-28"
    )


def build_api_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_API_TOKENS:
        token = random.choice(GITHUB_API_TOKENS)
        headers["Authorization"] = f"token {token}"
    return headers


async def check_api_rate(response):
    # Handle the rate limit on GitHub
    if int(response.headers["x-ratelimit-remaining"]) > 0:
        # Error when the remaining limit is not 0,
        # return the error state
        raise ValueError("Invalid 403 response while rate limit is not over")
    reset_time_utc_epoch = int(response.headers["x-ratelimit-reset"])
    ts_now_utc = datetime.datetime.now(datetime.timezone.utc)
    ts_now_utc_epoch = ts_now_utc.timestamp()
    sleep_duration = max(reset_time_utc_epoch - ts_now_utc_epoch, 0) + 2.0
    wake_time = datetime.datetime.now() + datetime.timedelta(
        seconds=sleep_duration
    )
    logger.warning(
        "GitHub rate limit exceeded, sleeping for "
        f"{int(sleep_duration)} seconds until "
        f"{wake_time}",
    )
    await asyncio.sleep(sleep_duration)


async def send_github_api_request(url: str, params=None):
    # limit the number of requests
    headers = build_api_headers()
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_context)
    ) as session:
        response = None
        while True:
            if response is not None:
                # We came back where after 403 error
                # check if we need to wait for
                # the API cooldown
                await check_api_rate(response)
            async with github_api_sem:
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
    owner: str,
    repo: str,
    seen_empty_page: List[Any],
    page: int,
    page_size: int,
):
    if seen_empty_page and page >= seen_empty_page[0]:
        return page, []
    params = {"per_page": page_size, "page": page}
    url = f"{GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits"
    return page, await send_github_api_request(url, params)


async def async_page_generator(owner: str, repo: str):
    seen_empty_page = [inf]
    start_page = 0
    while start_page <= seen_empty_page[0]:
        for result in asyncio.as_completed(
            tuple(
                asyncio.create_task(
                    get_commit_page(
                        owner, repo, seen_empty_page, page, PAGE_SIZE
                    )
                )
                for page in range(
                    start_page,
                    int(min(seen_empty_page[0], start_page + PAGES_PER_BATCH)),
                )
            )
        ):
            page, commits = await result
            if not commits:
                seen_empty_page[0] = min(seen_empty_page[0], page)
            else:
                yield commits
        start_page += PAGES_PER_BATCH


async def get_commit_details(owner: str, repo: str, commit):
    commit_hash = commit["sha"]
    url = f"{GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}/commits/{quote_plus(commit_hash)}"
    return await send_github_api_request(url)


async def async_commit_generator(
    owner: str, repo: str, commit_page: List[Any]
):
    batch = [
        asyncio.create_task(get_commit_details(owner, repo, commit))
        for commit in commit_page
    ]
    for commit in asyncio.as_completed(batch):
        yield await commit


async def pull_all_detailed_commits(owner: str, repo: str):
    global github_api_sem, commit_detail_sem
    github_api_sem = asyncio.Semaphore(MAX_CONCURRENT_API_REQUESTS)
    total_commits = 0
    async for commit_page in async_page_generator(owner, repo):
        async for commit in async_commit_generator(owner, repo, commit_page):
            print(commit["sha"])
        total_commits += len(commit_page)
        print(len(commit_page))
    print(total_commits)


def main():
    asyncio.run(pull_all_detailed_commits("sonic-net", "sonic-mgmt"))
