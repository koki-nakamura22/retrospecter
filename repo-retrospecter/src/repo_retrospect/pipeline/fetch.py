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
from datetime import UTC, date, datetime
from pathlib import Path

from repo_retrospect.cache.store import save as save_cache
from repo_retrospect.models.cache import CACHE_SCHEMA_VERSION, CacheFile
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


def run_fetch(
    *,
    repo: str,
    cache_path: Path,
    last: int | None = None,
    since: date | str | None = None,
    include_loose_commits: bool = True,
    timeout: float = GH_TIMEOUT_SEC,
) -> FetchSummary:
    """Fetch merged PRs (and optionally loose commits) from ``gh`` and persist.

    The cache is rewritten in full: previously persisted ``knowledge`` is
    discarded so a subsequent ``generate`` will re-classify against the
    current PR set. Callers that want to preserve cached knowledge should
    invoke ``services.fetcher`` directly.
    """
    logger.info(
        "fetch starting: repo=%s last=%s since=%s cache=%s loose=%s",
        repo,
        last,
        since,
        cache_path,
        include_loose_commits,
    )
    pull_requests = fetch_pull_requests(
        repo, last=last, since=since, timeout=timeout
    )

    loose_commits = []
    if include_loose_commits:
        collected_pr_numbers = {pr.number for pr in pull_requests}
        loose_commits = fetch_loose_commits(
            repo,
            associated_pr_numbers=collected_pr_numbers,
            last=last,
            since=since,
            timeout=timeout,
        )

    cache = CacheFile(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime.now(tz=UTC),
        repo=repo,
        pull_requests=pull_requests,
        loose_commits=loose_commits,
        knowledge=None,
    )
    save_cache(cache_path, cache)
    logger.info(
        "fetch done: repo=%s pr_count=%d loose_commits=%d cache=%s",
        repo,
        len(pull_requests),
        len(loose_commits),
        cache_path,
    )
    return FetchSummary(
        repo=repo,
        cache_path=cache_path,
        pr_count=len(pull_requests),
        loose_commit_count=len(loose_commits),
    )


__all__ = ["FetchSummary", "run_fetch"]
