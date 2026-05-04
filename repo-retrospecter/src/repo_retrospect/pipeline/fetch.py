"""Fetch sub-pipeline (architecture.md §Pipeline レイヤー).

Wraps :mod:`repo_retrospect.services.fetcher` and persists the result via
:mod:`repo_retrospect.cache.store`. The CLI ``fetch`` subcommand and the
``run`` orchestration both delegate here so the I/O contract (cache file
shape, ``schema_version``, generated_at) lives in one place.

Per decision-defaults.md §エラー処理, expected failures (auth / rate limit)
propagate as the typed exceptions from ``services.exceptions``; the CLI
layer translates them into ``click.ClickException``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from repo_retrospect.cache.store import load as load_cache
from repo_retrospect.cache.store import save as save_cache
from repo_retrospect.models.cache import CACHE_SCHEMA_VERSION, CacheFile
from repo_retrospect.models.commit import Commit
from repo_retrospect.models.pull_request import PullRequest
from repo_retrospect.services.fetcher import (
    GH_TIMEOUT_SEC,
    fetch_loose_commits,
    fetch_pull_requests,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchSummary:
    """Outcome of a fetch run, surfaced to the CLI for final reporting."""

    repo: str
    cache_path: Path
    pr_count: int
    loose_commit_count: int = 0
    appended: bool = False


def run_fetch(
    *,
    repo: str,
    cache_path: Path,
    last: int | None = None,
    last_commits: int | None = None,
    since: date | str | None = None,
    include_loose_commits: bool = True,
    append: bool = False,
    timeout: float = GH_TIMEOUT_SEC,
) -> FetchSummary:
    """Fetch merged PRs (and optionally loose commits) from ``gh`` and persist.

    Args:
        last: Cap on merged PRs (CLI ``--last``). ``None`` defers to the
            ``services.fetcher`` default.
        last_commits: Cap on default-branch commits considered for the
            loose-commit pass (CLI ``--last-commits``). Independent from
            ``last``.
        append: ADR-0005 incremental update. When True and a cache file
            already exists, since is auto-derived from the latest
            ``merged_at`` / ``committed_at`` already in the cache, and new
            PRs/commits are merged with existing entries (de-duplicated by
            ``number`` / ``sha``). Existing knowledge is preserved so
            ``generate`` only classifies the new arrivals.
        since: Explicit lower bound on PR / commit date. Wins over the
            auto-derived bound when ``append`` is True (per ADR-0005).

    The cache is rewritten in full when ``append`` is False: previously
    persisted ``knowledge`` is discarded so a subsequent ``generate`` will
    re-classify against the current PR set.
    """
    existing: CacheFile | None = None
    appended = False
    effective_since: date | str | None = since

    if append and cache_path.exists():
        existing = load_cache(cache_path)
        if since is None:
            effective_since = _auto_since(existing)
        appended = True
        logger.info(
            "append mode: existing cache has pr=%d commits=%d; effective since=%s",
            len(existing.pull_requests),
            len(existing.loose_commits),
            effective_since,
        )
    elif append and not cache_path.exists():
        logger.warning(
            "append requested but cache %s not found; falling back to full fetch",
            cache_path,
        )

    logger.info(
        "fetch starting: repo=%s last=%s last_commits=%s since=%s cache=%s loose=%s append=%s",
        repo,
        last,
        last_commits,
        effective_since,
        cache_path,
        include_loose_commits,
        appended,
    )
    new_pull_requests = fetch_pull_requests(
        repo, last=last, since=effective_since, timeout=timeout
    )

    new_loose_commits: list[Commit] = []
    if include_loose_commits:
        already_collected = {pr.number for pr in new_pull_requests}
        if existing is not None:
            already_collected |= {pr.number for pr in existing.pull_requests}
        new_loose_commits = fetch_loose_commits(
            repo,
            associated_pr_numbers=already_collected,
            last=last_commits,
            since=effective_since,
            timeout=timeout,
        )

    if existing is not None:
        merged_prs = _merge_prs(existing.pull_requests, new_pull_requests)
        merged_commits = _merge_commits(existing.loose_commits, new_loose_commits)
        knowledge = existing.knowledge
    else:
        merged_prs = new_pull_requests
        merged_commits = new_loose_commits
        knowledge = None

    cache = CacheFile(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime.now(tz=UTC),
        repo=repo,
        pull_requests=merged_prs,
        loose_commits=merged_commits,
        knowledge=knowledge,
    )
    save_cache(cache_path, cache)
    logger.info(
        "fetch done: repo=%s pr_count=%d (+%d new) loose_commits=%d (+%d new) cache=%s",
        repo,
        len(merged_prs),
        len(new_pull_requests),
        len(merged_commits),
        len(new_loose_commits),
        cache_path,
    )
    return FetchSummary(
        repo=repo,
        cache_path=cache_path,
        pr_count=len(merged_prs),
        loose_commit_count=len(merged_commits),
        appended=appended,
    )


def _auto_since(existing: CacheFile) -> date | str | None:
    """Derive the next-since boundary from the latest item in the cache.

    Returns the date one second after the latest ``merged_at`` /
    ``committed_at``. If the cache is empty, returns None (= full fetch).
    The boundary is rendered as a YYYY-MM-DD date because GitHub's
    ``--search merged:>=...`` only accepts day granularity. We round
    *down* to the day and rely on de-dup to drop the already-collected
    items from the same day.
    """
    candidates: list[datetime] = [pr.merged_at for pr in existing.pull_requests]
    candidates.extend(c.committed_at for c in existing.loose_commits)
    if not candidates:
        return None
    latest = max(candidates)
    # Step back one day so PRs/commits merged later on the same day are not missed.
    boundary = latest - timedelta(days=0)
    return boundary.date()


def _merge_prs(
    existing: list[PullRequest], incoming: list[PullRequest]
) -> list[PullRequest]:
    """Merge by PR number; existing entries win (ADR-0005)."""
    seen = {pr.number for pr in existing}
    extra = [pr for pr in incoming if pr.number not in seen]
    return list(existing) + extra


def _merge_commits(
    existing: list[Commit], incoming: list[Commit]
) -> list[Commit]:
    """Merge by commit sha; existing entries win (ADR-0005)."""
    seen = {c.sha for c in existing}
    extra = [c for c in incoming if c.sha not in seen]
    return list(existing) + extra


__all__ = ["FetchSummary", "run_fetch"]
