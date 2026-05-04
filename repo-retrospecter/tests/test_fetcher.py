"""Unit tests for repo_retrospect.services.fetcher."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from repo_retrospect.services import fetcher
from repo_retrospect.services.exceptions import AuthError, FetchError, RateLimitError
from repo_retrospect.services.fetcher import (
    DEFAULT_LIMIT,
    SEARCH_LIMIT_DEFAULT,
    _author_login,
    _build_pr_list_args,
    _extract_wait_hint,
    _parse_dt,
    _run_gh,
    fetch_pull_requests,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=code, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _run_gh
# ---------------------------------------------------------------------------


class TestRunGh:
    def test_success_returns_stdout(self) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="hello", code=0)
            assert _run_gh(["pr", "list"]) == "hello"

    def test_invokes_gh_with_argv_and_default_timeout(self) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="{}", code=0)
            _run_gh(["api", "x"])
            _, kwargs = mock_run.call_args
            args = mock_run.call_args.args[0]
            assert args[0] == "gh"
            assert args[1:] == ["api", "x"]
            assert kwargs["timeout"] == fetcher.GH_TIMEOUT_SEC == 60.0
            assert kwargs["text"] is True
            assert kwargs["capture_output"] is True
            assert kwargs["check"] is False

    def test_custom_timeout_is_forwarded(self) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stdout="", code=0)
            _run_gh(["api", "x"], timeout=12.5)
            assert mock_run.call_args.kwargs["timeout"] == 12.5

    def test_missing_gh_binary_raises_fetch_error(self) -> None:
        with patch(
            "repo_retrospect.services.fetcher.subprocess.run",
            side_effect=FileNotFoundError("no gh"),
        ), pytest.raises(FetchError, match="gh CLI not found"):
            _run_gh(["pr", "list"])

    def test_timeout_raises_fetch_error_with_seconds(self) -> None:
        with patch(
            "repo_retrospect.services.fetcher.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60),
        ), pytest.raises(FetchError, match="timed out after 60s"):
            _run_gh(["pr", "list"])

    @pytest.mark.parametrize(
        "stderr",
        [
            "error: gh authentication required",
            "To authenticate, run: gh auth login",
            "You are not logged into any GitHub hosts",
            "HTTP 401: Bad credentials",
        ],
    )
    def test_auth_failure_raises_auth_error(self, stderr: str) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stderr=stderr, code=4)
            with pytest.raises(AuthError) as exc:
                _run_gh(["pr", "list"])
            assert "gh authentication required" in str(exc.value)

    def test_rate_limit_extracts_wait_hint(self) -> None:
        stderr = "API rate limit exceeded for user. Try again in 5 minutes."
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stderr=stderr, code=4)
            with pytest.raises(RateLimitError) as exc:
                _run_gh(["pr", "list"])
            msg = str(exc.value)
            assert "rate limit" in msg.lower()
            assert "5 minutes" in msg

    def test_rate_limit_without_hint_still_raises(self) -> None:
        stderr = "secondary rate limit triggered"
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stderr=stderr, code=4)
            with pytest.raises(RateLimitError):
                _run_gh(["pr", "list"])

    def test_generic_failure_raises_fetch_error_not_auth(self) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stderr="repo not found", code=1)
            with pytest.raises(FetchError) as exc:
                _run_gh(["pr", "list"])
            assert not isinstance(exc.value, AuthError)
            assert not isinstance(exc.value, RateLimitError)
            assert "repo not found" in str(exc.value)

    def test_failure_without_stderr_falls_back_to_exit_code(self) -> None:
        with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
            mock_run.return_value = _completed(stderr="", stdout="", code=2)
            with pytest.raises(FetchError, match="exit code 2"):
                _run_gh(["pr", "list"])


# ---------------------------------------------------------------------------
# _extract_wait_hint
# ---------------------------------------------------------------------------


class TestExtractWaitHint:
    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            ("Try again in 5 minutes.", "5 minutes"),
            ("retry after 30 seconds", "30 seconds"),
            ("please wait 1 hour before retrying", "1 hour"),
        ],
    )
    def test_extracts_units(self, stderr: str, expected: str) -> None:
        assert _extract_wait_hint(stderr) == expected

    def test_returns_none_when_no_hint_present(self) -> None:
        assert _extract_wait_hint("repository not found") is None


# ---------------------------------------------------------------------------
# _author_login
# ---------------------------------------------------------------------------


class TestAuthorLogin:
    def test_extracts_login_from_dict_and_drops_email(self) -> None:
        raw: dict[str, Any] = {
            "login": "alice",
            "email": "alice@example.com",
            "name": "Alice Doe",
            "id": 123,
        }
        assert _author_login(raw) == "alice"

    def test_falls_back_to_ghost_when_login_missing(self) -> None:
        assert _author_login({"email": "a@b"}) == "ghost"

    def test_falls_back_to_ghost_when_login_empty(self) -> None:
        assert _author_login({"login": ""}) == "ghost"

    def test_falls_back_to_ghost_on_none(self) -> None:
        assert _author_login(None) == "ghost"

    def test_passes_through_string(self) -> None:
        assert _author_login("bob") == "bob"

    def test_falls_back_on_non_string_non_dict(self) -> None:
        assert _author_login(42) == "ghost"


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    def test_z_suffix_becomes_utc(self) -> None:
        assert _parse_dt("2026-05-04T10:00:00Z") == datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)

    def test_explicit_offset_is_preserved(self) -> None:
        result = _parse_dt("2026-05-04T10:00:00+00:00")
        assert result == datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_dt("not-a-date")


# ---------------------------------------------------------------------------
# _build_pr_list_args
# ---------------------------------------------------------------------------


class TestBuildPrListArgs:
    def test_last_only_uses_state_merged_and_default_30(self) -> None:
        args = _build_pr_list_args("o/r", last=None, since=None)
        assert args[:5] == ["pr", "list", "--repo", "o/r", "--json"]
        assert "--state" in args
        assert args[args.index("--state") + 1] == "merged"
        assert args[args.index("--limit") + 1] == str(DEFAULT_LIMIT)
        assert "--search" not in args

    def test_explicit_last_overrides_default(self) -> None:
        args = _build_pr_list_args("o/r", last=50, since=None)
        assert args[args.index("--limit") + 1] == "50"

    def test_since_str_builds_search_query(self) -> None:
        args = _build_pr_list_args("o/r", last=None, since="2026-04-01")
        idx = args.index("--search")
        assert args[idx + 1] == "is:merged merged:>=2026-04-01"
        assert args[args.index("--limit") + 1] == str(SEARCH_LIMIT_DEFAULT)
        assert "--state" not in args

    def test_since_date_object_isoformatted(self) -> None:
        args = _build_pr_list_args("o/r", last=None, since=date(2026, 4, 1))
        assert "is:merged merged:>=2026-04-01" in args

    def test_since_with_last_caps_search_limit(self) -> None:
        args = _build_pr_list_args("o/r", last=10, since="2026-04-01")
        assert args[args.index("--limit") + 1] == "10"
        assert "is:merged merged:>=2026-04-01" in args


# ---------------------------------------------------------------------------
# fetch_pull_requests (integration-style with mocked _run_gh)
# ---------------------------------------------------------------------------


def _pr_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "number": 7,
        "title": "Refactor cache layer",
        "body": "see ADR-0003",
        "author": {"login": "alice", "email": "alice@example.com", "name": "Alice"},
        "mergedAt": "2026-05-03T12:34:56Z",
        "url": "https://github.com/o/r/pull/7",
    }
    base.update(overrides)
    return base


def _make_gh_responder(
    pr_list: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    inline: list[dict[str, Any]] | None = None,
):
    """Build a side_effect callable for _run_gh that routes by argv shape."""

    def responder(args: list[str], *, timeout: float = 60.0) -> str:  # noqa: ARG001
        if args[:2] == ["pr", "list"]:
            return json.dumps(pr_list or [])
        if args[0] == "api" and "/issues/" in args[1] and args[1].endswith("/comments"):
            return json.dumps(issues or [])
        if args[0] == "api" and "/pulls/" in args[1] and args[1].endswith("/reviews"):
            return json.dumps(reviews or [])
        if args[0] == "api" and "/pulls/" in args[1] and args[1].endswith("/comments"):
            return json.dumps(inline or [])
        raise AssertionError(f"unexpected gh argv: {args}")

    return responder


class TestFetchPullRequests:
    def test_normalizes_minimal_pr_with_no_comments(self) -> None:
        responder = _make_gh_responder(pr_list=[_pr_payload()])
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)

        assert len(prs) == 1
        pr = prs[0]
        assert pr.number == 7
        assert pr.title == "Refactor cache layer"
        assert pr.author == "alice"  # email dropped
        assert pr.merged_at == datetime(2026, 5, 3, 12, 34, 56, tzinfo=UTC)
        assert pr.url == "https://github.com/o/r/pull/7"
        assert pr.review_comments == []
        assert pr.inline_comments == []

    def test_drops_email_from_pr_author(self) -> None:
        responder = _make_gh_responder(
            pr_list=[_pr_payload(author={"login": "bob", "email": "bob@example.com"})],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)
        # The serialized PR must not contain the email anywhere.
        dumped = prs[0].model_dump_json()
        assert "bob@example.com" not in dumped
        assert prs[0].author == "bob"

    def test_collects_issue_review_inline_comments(self) -> None:
        responder = _make_gh_responder(
            pr_list=[_pr_payload()],
            issues=[
                {
                    "id": 100,
                    "user": {"login": "carol"},
                    "body": "Top-level discussion",
                    "created_at": "2026-05-03T13:00:00Z",
                }
            ],
            reviews=[
                {
                    "id": 200,
                    "user": {"login": "dave"},
                    "body": "Looks good overall",
                    "submitted_at": "2026-05-03T13:30:00Z",
                }
            ],
            inline=[
                {
                    "id": 300,
                    "user": {"login": "eve"},
                    "body": "rename this var",
                    "created_at": "2026-05-03T13:45:00Z",
                }
            ],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)

        pr = prs[0]
        review_kinds = sorted(c.kind for c in pr.review_comments)
        assert review_kinds == ["issue", "review"]
        assert [c.kind for c in pr.inline_comments] == ["inline"]
        assert pr.inline_comments[0].author == "eve"
        # ID is namespaced so issue/inline can't collide on the same int.
        assert pr.review_comments[0].id.startswith(("issue-", "review-"))

    def test_extracts_suggestion_block_as_separate_comment(self) -> None:
        body_with_suggestion = (
            "Consider using a constant here:\n"
            "```suggestion\n"
            "MAX_RETRIES = 3\n"
            "```\n"
        )
        responder = _make_gh_responder(
            pr_list=[_pr_payload()],
            inline=[
                {
                    "id": 42,
                    "user": {"login": "frank"},
                    "body": body_with_suggestion,
                    "created_at": "2026-05-03T14:00:00Z",
                }
            ],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)

        kinds = [c.kind for c in prs[0].inline_comments]
        assert "inline" in kinds
        assert "suggestion" in kinds
        suggestion = next(c for c in prs[0].inline_comments if c.kind == "suggestion")
        assert "MAX_RETRIES = 3" in suggestion.body
        assert suggestion.body.startswith("```suggestion")
        assert suggestion.body.endswith("```")
        assert suggestion.id.endswith("-suggestion")

    def test_skips_empty_review_body(self) -> None:
        """An approve-click review (empty body) must be dropped, not crash
        Pydantic with an empty-string Comment."""
        responder = _make_gh_responder(
            pr_list=[_pr_payload()],
            reviews=[
                {
                    "id": 1,
                    "user": {"login": "carol"},
                    "body": "",
                    "submitted_at": "2026-05-03T13:00:00Z",
                },
                {
                    "id": 2,
                    "user": {"login": "carol"},
                    "body": "Real feedback",
                    "submitted_at": "2026-05-03T13:05:00Z",
                },
            ],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)

        review_only = [c for c in prs[0].review_comments if c.kind == "review"]
        assert len(review_only) == 1
        assert review_only[0].body == "Real feedback"

    def test_skips_pr_without_merged_at(self) -> None:
        responder = _make_gh_responder(
            pr_list=[
                _pr_payload(number=1, mergedAt=None),
                _pr_payload(number=2),
            ],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=2)
        assert [p.number for p in prs] == [2]

    def test_uses_login_only_when_user_field_includes_email(self) -> None:
        responder = _make_gh_responder(
            pr_list=[_pr_payload()],
            issues=[
                {
                    "id": 1,
                    "user": {
                        "login": "carol",
                        "email": "carol@example.com",
                        "name": "Carol",
                    },
                    "body": "hi",
                    "created_at": "2026-05-03T13:00:00Z",
                }
            ],
        )
        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            prs = fetch_pull_requests("o/r", last=1)

        comment = prs[0].review_comments[0]
        assert comment.author == "carol"
        assert "carol@example.com" not in prs[0].model_dump_json()

    def test_passes_repo_and_limit_to_gh(self) -> None:
        captured: list[list[str]] = []

        def responder(args: list[str], *, timeout: float = 60.0) -> str:  # noqa: ARG001
            captured.append(args)
            if args[:2] == ["pr", "list"]:
                return "[]"
            return "[]"

        with patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder):
            fetch_pull_requests("owner/repo", last=5)

        first = captured[0]
        assert "--repo" in first and first[first.index("--repo") + 1] == "owner/repo"
        assert first[first.index("--limit") + 1] == "5"

    def test_propagates_auth_error_from_pr_list(self) -> None:
        with patch(
            "repo_retrospect.services.fetcher._run_gh",
            side_effect=AuthError("gh authentication required"),
        ), pytest.raises(AuthError):
            fetch_pull_requests("o/r", last=1)

    def test_propagates_rate_limit_error_from_pr_list(self) -> None:
        with patch(
            "repo_retrospect.services.fetcher._run_gh",
            side_effect=RateLimitError("API rate limit exceeded"),
        ), pytest.raises(RateLimitError):
            fetch_pull_requests("o/r", last=1)

    def test_invalid_pr_list_payload_raises_fetch_error(self) -> None:
        responder = MagicMock(return_value=json.dumps({"not": "an array"}))
        with (
            patch("repo_retrospect.services.fetcher._run_gh", side_effect=responder),
            pytest.raises(FetchError, match="JSON array"),
        ):
            fetch_pull_requests("o/r", last=1)
