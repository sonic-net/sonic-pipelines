import warnings
import random
from math import inf

import aiohttp
import asyncio
import os

from typing import List, Any
from urllib.parse import quote_plus

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
    headers = build_api_headers()
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False)
    ) as session:
        async with session.get(
            url=f"{GITHUB_API_ENDPOINT}repos/{quote_plus(owner)}/{quote_plus(repo)}"
            "/commits",
            headers=headers,
            params=params,
        ) as response:
            if response.status != 200:
                raise ValueError(
                    f"Bad API response: {response} for page {page}"
                )
            return page, await response.json()


async def async_page_generator(owner: str, repo: str):
    seen_empty_page = [inf]
    next_page = 0
    batch0 = [
        asyncio.create_task(
            get_commit_page(owner, repo, seen_empty_page, page, PAGE_SIZE)
        )
        for page in range(next_page, next_page + PAGES_PER_BATCH)
    ]
    next_page += PAGES_PER_BATCH
    batch1 = [
        asyncio.create_task(
            get_commit_page(owner, repo, seen_empty_page, page, PAGE_SIZE)
        )
        for page in range(next_page, next_page + PAGES_PER_BATCH)
    ]
    while True:
        for result in asyncio.as_completed(batch0):
            page, commits = await result
            print(page, len(commits))
            if not commits:
                seen_empty_page[0] = min(seen_empty_page[0], page)
            else:
                yield commits
        if next_page > seen_empty_page[0]:
            break
        batch0, batch1 = batch1, batch0
        batch1.clear()
        for page in range(
            next_page,
            int(min(seen_empty_page[0], next_page + PAGES_PER_BATCH)),
        ):
            batch1.append(
                asyncio.create_task(
                    get_commit_page(
                        owner,
                        repo,
                        seen_empty_page,
                        page,
                        PAGE_SIZE,
                    )
                )
            )
        next_page += PAGES_PER_BATCH


async def async_commit_generator(commits: List[Any]):
    for commit in commits[:2]:
        yield commit


async def pull_all_pages():
    async for commit_page in async_page_generator("sonic-net", "sonic-mgmt"):
        async for commit in async_commit_generator(commit_page):
            print(commit)


def main():
    asyncio.run(pull_all_pages())
