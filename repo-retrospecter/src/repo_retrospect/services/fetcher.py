"""Fetch merged PRs (with comments) by wrapping the ``gh`` CLI.

ADR-0002 / architecture.md §services/fetcher.py:
- ``gh`` is invoked as a subprocess; auth is delegated entirely to ``gh auth``.
- PR list is obtained via ``gh pr list ... --json ...``.
- Top-level conversation, review submissions, and inline review comments
  are obtained via ``gh api`` against the matching REST endpoints.
- Inline-comment bodies are scanned for GitHub suggestion blocks
  (```` ```suggestion ```` ... ```` ``` ````) and surfaced as separate
  ``Comment`` records of kind ``"suggestion"``.

Per architecture.md §セキュリティアーキテクチャ, only the GitHub login is
retained for ``author``; ``email`` and other identifying fields are dropped
before reaching domain models.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, cast

from repo_retrospect.models.comment import Comment, CommentKind
from repo_retrospect.models.pull_request import PullRequest
from repo_retrospect.services.exceptions import AuthError, FetchError, RateLimitError

GH_TIMEOUT_SEC: float = 60.0
DEFAULT_LIMIT: int = 30
SEARCH_LIMIT_DEFAULT: int = 200

PR_LIST_FIELDS: str = "number,title,body,author,mergedAt,url"

_SUGGESTION_RE = re.compile(r"```suggestion\n.*?\n```", re.DOTALL)
_AUTH_PATTERNS = (
    "authentication required",
    "gh auth login",
    "not logged into",
    "no authentication token",
    "http 401",
)
_RATE_LIMIT_PATTERNS = (
    "api rate limit exceeded",
    "secondary rate limit",
    "you have exceeded a secondary rate limit",
    "rate limit",
)


# ---------------------------------------------------------------------------
# subprocess wrapper
# ---------------------------------------------------------------------------


def _run_gh(args: list[str], *, timeout: float = GH_TIMEOUT_SEC) -> str:
    """Run ``gh`` and return stdout, raising typed errors on failure."""
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FetchError(
            "gh CLI not found on PATH. Install from https://cli.github.com/."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(
            f"gh CLI timed out after {timeout:.0f}s while running: gh {' '.join(args)}"
        ) from exc

    if result.returncode == 0:
        return result.stdout

    stderr = (result.stderr or "").strip()
    lowered = stderr.lower()

    if any(p in lowered for p in _AUTH_PATTERNS):
        raise AuthError(
            "gh authentication required. Run `gh auth login` first.\n" + stderr
        )
    if any(p in lowered for p in _RATE_LIMIT_PATTERNS):
        wait_hint = _extract_wait_hint(stderr)
        msg = "GitHub API rate limit exceeded; retry after the cool-down."
        if wait_hint:
            msg += f" gh suggests waiting {wait_hint}."
        raise RateLimitError(msg + "\n" + stderr)

    detail = stderr or (result.stdout or "").strip() or f"exit code {result.returncode}"
    raise FetchError(f"gh failed: {detail}")


def _extract_wait_hint(stderr: str) -> str | None:
    """Pull a human-readable wait hint out of gh's stderr if present."""
    match = re.search(
        r"(?:try again|retry|wait)[^.\n]*?(\d+\s*(?:second|minute|hour)s?)",
        stderr,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    iso = re.search(
        r"(?:reset[s]? at|until)\s+([0-9T:Z\-+\s]+)", stderr, flags=re.IGNORECASE
    )
    return iso.group(1).strip() if iso else None


# ---------------------------------------------------------------------------
# field normalization
# ---------------------------------------------------------------------------


def _author_login(value: Any) -> str:
    """Extract the GitHub login, dropping email and other identifying fields.

    ``gh`` returns either ``{"login": "...", "name": "...", "email": "..."}``
    (for ``gh pr list``'s ``author``) or ``{"login": "...", "id": ...}``
    (for ``gh api`` ``user`` records). We deliberately keep only ``login``
    to satisfy architecture.md §セキュリティアーキテクチャ (PII strip).
    """
    if isinstance(value, dict):
        login = cast(dict[str, Any], value).get("login")
        if isinstance(login, str) and login:
            return login
    if isinstance(value, str) and value:
        return value
    return "ghost"


def _parse_dt(value: str) -> datetime:
    """Parse the ISO 8601 timestamps that ``gh`` / GitHub emit."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------


def _build_pr_list_args(
    repo: str, *, last: int | None, since: date | str | None
) -> list[str]:
    args = ["pr", "list", "--repo", repo, "--json", PR_LIST_FIELDS]
    if since is not None:
        since_str = since.isoformat() if isinstance(since, date) else since
        args += ["--search", f"is:merged merged:>={since_str}"]
        args += ["--limit", str(last if last is not None else SEARCH_LIMIT_DEFAULT)]
    else:
        args += ["--state", "merged"]
        args += ["--limit", str(last if last is not None else DEFAULT_LIMIT)]
    return args


# ---------------------------------------------------------------------------
# comment fetchers
# ---------------------------------------------------------------------------


def _to_comment(
    raw: dict[str, Any],
    *,
    kind: CommentKind,
    id_prefix: str,
    date_field: str,
) -> Comment | None:
    cid = raw.get("id")
    body = raw.get("body")
    dt_str = raw.get(date_field)
    if cid is None or not isinstance(dt_str, str) or not dt_str:
        return None
    if not isinstance(body, str) or not body.strip():
        # GitHub allows empty review bodies (an approve click) and dismissed
        # comments; skip per decision-defaults §null/欠損値.
        return None
    return Comment(
        id=f"{id_prefix}-{cid}",
        author=_author_login(raw.get("user")),
        body=body,
        created_at=_parse_dt(dt_str),
        kind=kind,
    )


def _decode_array(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    decoded = json.loads(raw)
    if not isinstance(decoded, list):
        raise FetchError(f"Expected JSON array from gh api, got: {type(decoded).__name__}")
    return cast(list[dict[str, Any]], decoded)


def _fetch_review_comments(repo: str, number: int, timeout: float) -> list[Comment]:
    """Top-level conversation comments + review submission bodies."""
    issue_raw = _run_gh(
        ["api", f"repos/{repo}/issues/{number}/comments"],
        timeout=timeout,
    )
    review_raw = _run_gh(
        ["api", f"repos/{repo}/pulls/{number}/reviews"],
        timeout=timeout,
    )
    out: list[Comment] = []
    for c in _decode_array(issue_raw):
        comment = _to_comment(c, kind="issue", id_prefix="issue", date_field="created_at")
        if comment is not None:
            out.append(comment)
    for r in _decode_array(review_raw):
        comment = _to_comment(r, kind="review", id_prefix="review", date_field="submitted_at")
        if comment is not None:
            out.append(comment)
    return out


def _fetch_inline_comments(
    repo: str, number: int, timeout: float
) -> tuple[list[Comment], list[Comment]]:
    raw = _run_gh(
        ["api", f"repos/{repo}/pulls/{number}/comments"],
        timeout=timeout,
    )
    inline: list[Comment] = []
    suggestions: list[Comment] = []
    for c in _decode_array(raw):
        comment = _to_comment(c, kind="inline", id_prefix="inline", date_field="created_at")
        if comment is None:
            continue
        inline.append(comment)
        match = _SUGGESTION_RE.search(comment.body)
        if match is not None:
            suggestions.append(
                Comment(
                    id=f"{comment.id}-suggestion",
                    author=comment.author,
                    body=match.group(0),
                    created_at=comment.created_at,
                    kind="suggestion",
                )
            )
    return inline, suggestions


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def fetch_pull_requests(
    repo: str,
    *,
    last: int | None = None,
    since: date | str | None = None,
    timeout: float = GH_TIMEOUT_SEC,
) -> list[PullRequest]:
    """Fetch merged PRs and their comments, normalized to ``PullRequest``.

    Args:
        repo: ``owner/name`` slug passed straight to ``gh --repo``.
        last: Take the most-recently merged ``last`` PRs (PRD F1 ``--last``).
        since: ISO date or ``date``; converted to ``--search 'merged:>=...'``
            (PRD F1 ``--since``). When set, ``last`` caps the result count
            and defaults to ``SEARCH_LIMIT_DEFAULT``.
        timeout: Per-subprocess timeout in seconds (decision-defaults
            §タイムアウト = 60s).
    """
    args = _build_pr_list_args(repo, last=last, since=since)
    raw = _run_gh(args, timeout=timeout)
    pr_dicts = _decode_array(raw)
    return list(_normalize_prs(repo, pr_dicts, timeout=timeout))


def _normalize_prs(
    repo: str, pr_dicts: list[dict[str, Any]], *, timeout: float
) -> Iterable[PullRequest]:
    for pr in pr_dicts:
        number_raw = pr.get("number")
        merged_at_raw = pr.get("mergedAt") or pr.get("merged_at")
        if number_raw is None or not isinstance(merged_at_raw, str) or not merged_at_raw:
            # mergedAt is mandatory for state=merged; skip malformed rows
            # rather than fabricate a timestamp.
            continue
        number = int(number_raw)
        review = _fetch_review_comments(repo, number, timeout)
        inline, suggestions = _fetch_inline_comments(repo, number, timeout)
        yield PullRequest(
            number=number,
            title=str(pr.get("title") or ""),
            body=str(pr.get("body") or ""),
            author=_author_login(pr.get("author")),
            merged_at=_parse_dt(merged_at_raw),
            url=str(pr.get("url") or ""),
            review_comments=review,
            inline_comments=inline + suggestions,
        )


__all__ = [
    "DEFAULT_LIMIT",
    "GH_TIMEOUT_SEC",
    "PR_LIST_FIELDS",
    "SEARCH_LIMIT_DEFAULT",
    "fetch_pull_requests",
]
