"""Module for interacting with GitHub API
   to retrieve user and commit information.
"""

import datetime
import logging
import os
import random
import time
import warnings
from typing import Dict, Set, Any
from urllib.parse import quote_plus

import requests
from requests import Response

logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)

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


def send_github_query(url: str) -> Response:
    """Send a request to the GitHub API with rate limiting handling.

    Automatically handles GitHub API rate limiting by sleeping until the
    rate limit resets when a 403 status code is received.

    Args:
        url: The GitHub API URL to request.

    Returns:
        Response: The HTTP response from the GitHub API.
    """
    # Build headers with randomly selected GitHub API token
    headers = build_api_headers()
    while True:
        response = requests.get(url, headers=headers)
        if response.status_code == 429:
            logger.warning("Got 429 error, too many requests")
        if response.headers.get("retry-after"):
            sleep_duration = int(response.headers["retry-after"])
        elif response.status_code == 403:
            # Handle the rate limit on GitHub
            if int(response.headers["x-ratelimit-remaining"]) > 0:
                # Error when the remaining limit is not 0,
                # return the error state
                return response
            reset_time_utc_epoch = int(response.headers["x-ratelimit-reset"])
            ts_now_utc = datetime.datetime.now(datetime.timezone.utc)
            ts_now_utc_epoch = ts_now_utc.timestamp()
            sleep_duration = (
                max(reset_time_utc_epoch - ts_now_utc_epoch, 0) + 2.0
            )
        else:
            return response
        wake_time = datetime.datetime.now() + datetime.timedelta(
            seconds=sleep_duration
        )
        logger.warning(
            "GitHub rate limit exceeded, sleeping for "
            f"{int(sleep_duration)} seconds until "
            f"{wake_time}",
        )
        time.sleep(sleep_duration)


gh_login_lookup_cache = dict()


def github_login_lookup(github_login: str) -> Dict[str, str]:
    """Look up GitHub user information by username.

    Args:
        github_login: The GitHub username to look up.

    Returns:
        Dict[str, str]: Dictionary containing user information
        (login, id, name, email, company).

    Raises:
        ValueError: If the GitHub user is not found.
        RuntimeError: If the API request fails.
    """
    try:
        return gh_login_lookup_cache[github_login]
    except KeyError:
        pass
    response = send_github_query(
        f"{GITHUB_API_ENDPOINT}/users/{quote_plus(github_login)}"
    )
    if response.status_code == 404:
        raise ValueError(f"Github user {github_login}, not found")
    if response.status_code == 200:
        gh_login_lookup_cache[github_login] = result = {
            k: response.json()[k]
            for k in ["login", "id", "name", "email", "company"]
        }
        return result
    raise RuntimeError(
        f"Invalid response to the github lookup. "
        f"Code: {response.status_code}"
    )


gh_id_lookup_cache = dict()


def github_id_lookup(github_id: int) -> Dict[str, str]:
    """Look up GitHub user information by user ID.

    Args:
        github_id: The GitHub user ID to look up.

    Returns:
        Dict[str, str]: Dictionary containing user information
        (login, id, name, email, company).

    Raises:
        ValueError: If the GitHub user is not found.
        RuntimeError: If the API request fails.
    """

    # return Bundled User for id -1
    if github_id == -1:
        return {
            "login": "non-github-bundle",
            "id": -1,
            "name": "Non-found emails bundle",
        }
    try:
        return gh_id_lookup_cache[github_id]
    except KeyError:
        pass
    response = send_github_query(
        f"{GITHUB_API_ENDPOINT}/user/{int(github_id)}",
    )
    if response.status_code == 404:
        raise ValueError(f"Github user {github_id}, not found")
    if response.status_code == 200:
        gh_id_lookup_cache[github_id] = result = {
            k: response.json()[k]
            for k in ["login", "id", "name", "email", "company"]
        }
        return result
    raise RuntimeError(
        f"Invalid response to the github lookup. "
        f"Code: {response.status_code}"
    )


def github_commit_author_id_lookup(
    commit_hash: str, owner: str, repo: str
) -> int:
    """Look up the GitHub user ID of a commit's author.

    Args:
        commit_hash: The Git commit hash to look up.
        owner: The GitHub repository owner (default: GITHUB_OWNER).
        repo: The GitHub repository name (default: GITHUB_REPO).

    Returns:
        int: The GitHub user ID of the commit author, or -1 if not found.

    Raises:
        ValueError: If the commit is not found in the repository.
        RuntimeError: If the API request fails.
    """
    response = send_github_query(
        f"{GITHUB_API_ENDPOINT}/repos/{quote_plus(owner)}/{quote_plus(repo)}"
        f"/commits/{quote_plus(commit_hash)}"
    )
    if response.status_code == 404:
        raise ValueError(
            f"Commit {commit_hash} is not found in repo "
            f"{repo}, owned by {owner}"
        )
    if response.status_code == 200:
        try:
            return response.json()["author"]["id"]
        except TypeError:
            return -1
    raise RuntimeError(
        f"Invalid response to the github lookup. "
        f"Code: {response.status_code}"
    )


def github_info_by_emails(emails: Set[str]) -> Dict[str, Any]:
    """Look up GitHub user information by email addresses.

    Parses GitHub noreply emails
    (like 29677895+nikamirrr@users.noreply.github.com)
    to extract GitHub user ID and login information.

    Args:
        emails: Set of email addresses to search for GitHub information.

    Returns:
        Dict[str, Any]: Dictionary containing GitHub user information, or
        empty dict if not found.
    """
    for email in emails:
        local_part, domain = email.split("@")
        if domain == "users.noreply.github.com":
            try:
                id_str, github_login = local_part.split("+")
                return github_id_lookup(int(id_str))
            except ValueError:
                # Try using the entire local part as login
                try:
                    return github_login_lookup(local_part)
                except ValueError:
                    pass

    return {}
