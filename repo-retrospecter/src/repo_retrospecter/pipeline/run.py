"""End-to-end ``run`` sub-pipeline (architecture.md §Pipeline レイヤー).

Chains :func:`pipeline.fetch.run_fetch` and :func:`pipeline.generate.run_generate`
so the CLI ``run`` subcommand is a one-liner. Failures in fetch propagate
and short-circuit ``generate`` — the cache file would not exist or would
be stale, so attempting to generate after a fetch failure would obscure
the underlying error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from repo_retrospecter.pipeline.fetch import FetchSummary, run_fetch
from repo_retrospecter.pipeline.generate import GenerateSummary, run_generate
from repo_retrospecter.services.fetcher import GH_TIMEOUT_SEC

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    """Combined outcome of a ``run`` invocation."""

    fetch: FetchSummary
    generate: GenerateSummary


def run_pipeline(
    *,
    repo: str,
    cache_path: Path,
    last: int | None = None,
    last_commits: int | None = None,
    since: date | str | None = None,
    human_out: Path | None = None,
    ai_out: Path | None = None,
    themes: list[str] | None = None,
    include_loose_commits: bool = True,
    append: bool = False,
    timeout: float = GH_TIMEOUT_SEC,
) -> RunSummary:
    """Fetch then generate in a single call.

    The function is the implementation behind the ``run`` CLI subcommand
    (PRD §CLI). It exists separately from a ``click`` command so tests can
    drive the orchestration without spinning up the click runner.
    """
    fetch_summary = run_fetch(
        repo=repo,
        cache_path=cache_path,
        last=last,
        last_commits=last_commits,
        since=since,
        include_loose_commits=include_loose_commits,
        append=append,
        timeout=timeout,
    )
    generate_summary = run_generate(
        cache_path=cache_path,
        human_out=human_out,
        ai_out=ai_out,
        themes=themes,
    )
    logger.info(
        "run done: pr=%d knowledge=%d outputs=%d",
        generate_summary.pr_count,
        generate_summary.knowledge_count,
        len(generate_summary.rendered_outputs),
    )
    return RunSummary(fetch=fetch_summary, generate=generate_summary)


__all__ = ["RunSummary", "run_pipeline"]
