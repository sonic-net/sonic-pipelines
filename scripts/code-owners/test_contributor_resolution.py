import asyncio
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from async_github_repo_summary import AsyncGitHubRepoSummary, GitHubAPIError
from async_helpers import GitCommitLocal
from contributor import ContributorCollection


class ContributorResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.object(
            AsyncGitHubRepoSummary, "GITHUB_API_TOKENS", ["test-token"]
        ):
            self.summary = AsyncGitHubRepoSummary()
        self.summary.contributors = ContributorCollection("unused.yaml")

    async def test_missing_user_id_is_cached_and_bundled(self):
        self.summary.send_github_api_request = AsyncMock(
            side_effect=GitHubAPIError(404, "not found")
        )
        self.summary.github_commit_author_id_lookup = AsyncMock(
            return_value=123941348
        )
        commit = GitCommitLocal(
            name="Historical author",
            email="author@example.com",
            ts=datetime.now(timezone.utc),
            changes=Counter(),
            commit_hash="abcdef",
        )
        with self.assertLogs("async_github_repo_summary", level="WARNING"):
            contributor = await self.summary.build_contributor(commit)
        self.assertEqual(contributor.github_id, -1)
        self.assertEqual(contributor.github_login, "non-github-bundle")
        self.assertIn(commit.email, contributor.emails)
        self.assertFalse(contributor.available_to_review)
        self.assertEqual(await self.summary.github_id_lookup(123941348), {})
        self.summary.send_github_api_request.assert_awaited_once()

    async def test_missing_login_is_cached(self):
        self.summary.send_github_api_request = AsyncMock(
            side_effect=GitHubAPIError(404, "not found")
        )
        with self.assertLogs("async_github_repo_summary", level="WARNING"):
            self.assertEqual(
                await self.summary.github_info_by_email(
                    "deleted@users.noreply.github.com"
                ),
                {},
            )
        self.assertEqual(await self.summary.github_login_lookup("deleted"), {})
        self.summary.send_github_api_request.assert_awaited_once()

    async def test_other_api_failures_are_not_bundled_or_cached(self):
        for status in (401, 403, 429, 500):
            for lookup, argument in (
                (self.summary.github_id_lookup, 123),
                (self.summary.github_login_lookup, "user"),
            ):
                with self.subTest(status=status, lookup=lookup.__name__):
                    self.summary.send_github_api_request = AsyncMock(
                        side_effect=GitHubAPIError(status, "request failed")
                    )
                    with self.assertRaises(GitHubAPIError):
                        await lookup(argument)
        self.assertEqual(self.summary.gh_id_lookup_cache, {})
        self.assertEqual(self.summary.gh_login_lookup_cache, {})

    async def test_email_api_errors_propagate(self):
        for email in (
            "123+user@users.noreply.github.com",
            "user@users.noreply.github.com",
        ):
            self.summary.send_github_api_request = AsyncMock(
                side_effect=GitHubAPIError(500, "server error")
            )
            with self.assertRaises(GitHubAPIError):
                await self.summary.github_info_by_email(email)

    async def test_malformed_email_is_warned_and_bundled(self):
        self.summary.github_commit_author_id_lookup = AsyncMock(
            return_value=-1
        )
        self.summary.send_github_api_request = AsyncMock()
        for email in ("", "unknown", "@example.com", "user@", "a@b@c"):
            with self.subTest(email=email):
                commit = GitCommitLocal(
                    name="Unknown author",
                    email=email,
                    ts=datetime.now(timezone.utc),
                    changes=Counter(),
                    commit_hash="abcdef",
                )
                with self.assertLogs(
                    "async_github_repo_summary", level="WARNING"
                ):
                    contributor = await self.summary.build_contributor(commit)
                self.assertEqual(contributor.github_id, -1)
        self.summary.send_github_api_request.assert_not_awaited()

    async def test_valid_email_lookup_paths(self):
        self.summary.github_id_lookup = AsyncMock(return_value={"id": 123})
        self.summary.github_login_lookup = AsyncMock(
            return_value={"id": 456}
        )
        self.assertEqual(
            await self.summary.github_info_by_email(
                "123+user@users.noreply.github.com"
            ),
            {"id": 123},
        )
        self.summary.github_id_lookup.assert_awaited_once_with(123)
        self.assertEqual(
            await self.summary.github_info_by_email(
                "user@users.noreply.github.com"
            ),
            {"id": 456},
        )
        self.summary.github_login_lookup.assert_awaited_once_with("user")
        self.assertEqual(
            await self.summary.github_info_by_email("user@example.com"), {}
        )

    async def test_valid_author_is_not_replaced_by_bundle(self):
        self.summary.github_commit_author_id_lookup = AsyncMock(
            return_value=123
        )
        self.summary.send_github_api_request = AsyncMock(
            return_value={
                "id": 123,
                "login": "user",
                "name": "Contributor",
                "email": "other@example.com",
                "company": None,
            }
        )
        commit = GitCommitLocal(
            name="Local name",
            email="author@example.com",
            ts=datetime.now(timezone.utc),
            changes=Counter(),
            commit_hash="abcdef",
        )
        contributor = await self.summary.build_contributor(commit)
        self.assertEqual(contributor.github_id, 123)
        self.assertEqual(contributor.github_login, "user")
        self.assertEqual(contributor.name, "Contributor")
        self.assertEqual(
            contributor.emails, {"author@example.com", "other@example.com"}
        )

    async def test_http_status_is_preserved_by_request_helper(self):
        self.summary.github_api_sem = asyncio.Semaphore(1)
        self.summary.build_api_headers = MagicMock(return_value={})
        response = MagicMock(status=404)
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = request
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "async_github_repo_summary.aiohttp.ClientSession",
            return_value=context,
        ), patch("async_github_repo_summary.aiohttp.TCPConnector"):
            with self.assertRaises(GitHubAPIError) as caught:
                await self.summary.send_github_api_request(
                    "https://api.github.com/user/123"
                )
        self.assertEqual(caught.exception.status, 404)
        self.assertIsInstance(caught.exception, ValueError)


if __name__ == "__main__":
    unittest.main()
