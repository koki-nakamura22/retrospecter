"""Generate sub-pipeline (architecture.md §Pipeline レイヤー).

Reads the intermediate cache, runs the LLM classifier when knowledge is
absent (ADR-0003 — re-render without re-classify), then drives the
``human`` and ``ai`` renderers.

Partial-failure semantics (decision-defaults.md §エラー処理):
- The classifier itself isolates per-batch failures and returns whatever
  it managed to extract. We treat its return value as authoritative and
  log the resulting count.
- Renderer failures are not silently swallowed — a template error implies
  a programming bug, not a per-PR data issue, so we let it surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from repo_retrospecter.cache.store import load as load_cache
from repo_retrospecter.cache.store import save as save_cache
from repo_retrospecter.models.cache import CacheFile
from repo_retrospecter.models.knowledge import Knowledge
from repo_retrospecter.services.classifier import (
    classify_commits,
    classify_pull_requests,
)
from repo_retrospecter.services.renderer import get_renderer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerateSummary:
    """Outcome of a generate run, surfaced to the CLI for final reporting."""

    cache_path: Path
    pr_count: int
    knowledge_count: int
    classified: bool
    rendered_outputs: tuple[Path, ...] = field(default=())


def run_generate(
    *,
    cache_path: Path,
    human_out: Path | None = None,
    ai_out: Path | None = None,
    themes: list[str] | None = None,
    skip_render: bool = False,
) -> GenerateSummary:
    """Classify (when needed) and render Markdown outputs from a cache file.

    Args:
        cache_path: Existing cache JSON written by :func:`run_fetch`.
        human_out: Destination for the human retrospective Markdown. ``None``
            skips the human renderer.
        ai_out: Destination for the AI-facing Markdown. ``None`` skips the
            AI renderer.
        themes: Allowed theme tags forwarded to the classifier; ``None``
            uses the canonical 5 axes (OQ-02).
        skip_render: TC-F2-01 ``--skip-render``: classify and persist
            updated knowledge but do not write any Markdown output.

    Returns:
        A summary the CLI can print as the final status line.
    """
    cache = load_cache(cache_path)

    classified = False
    if cache.knowledge is None:
        # First run for this cache: classify all PRs and commits.
        pending_prs = list(cache.pull_requests)
        pending_commits = list(cache.loose_commits)
        existing_knowledge: list[Knowledge] = []
        covered_urls: set[str] = set()
        do_classify = True
    else:
        # Subsequent run (typically after `fetch --append`): classify only
        # PRs/commits whose URLs are not yet represented in cached knowledge.
        # Empty knowledge list with no new items means "already attempted,
        # nothing to do" and intentionally short-circuits per existing test.
        existing_knowledge = list(cache.knowledge)
        covered_urls = {url for k in existing_knowledge for url in k.source_urls}
        pending_prs = [pr for pr in cache.pull_requests if pr.url not in covered_urls]
        pending_commits = [c for c in cache.loose_commits if c.url not in covered_urls]
        do_classify = bool(pending_prs or pending_commits)

    if do_classify:
        logger.info(
            "classifying %d PRs + %d loose commits (existing knowledge=%d)",
            len(pending_prs),
            len(pending_commits),
            len(existing_knowledge),
        )
        new_knowledge = classify_pull_requests(
            pending_prs, themes=themes, exclude_urls=covered_urls
        )
        new_knowledge.extend(
            classify_commits(pending_commits, themes=themes, exclude_urls=covered_urls)
        )
        knowledge = existing_knowledge + new_knowledge
        cache = cache.model_copy(
            update={"knowledge": knowledge, "generated_at": datetime.now(tz=UTC)}
        )
        save_cache(cache_path, cache)
        classified = True
        logger.info(
            "classification done: %d new + %d kept = %d total knowledge records",
            len(new_knowledge),
            len(existing_knowledge),
            len(knowledge),
        )
    else:
        logger.info(
            "reusing cached knowledge (%d records); no new items to classify",
            len(existing_knowledge),
        )

    rendered: list[Path] = []
    if not skip_render:
        rendered = _render_outputs(cache, human_out=human_out, ai_out=ai_out)

    return GenerateSummary(
        cache_path=cache_path,
        pr_count=len(cache.pull_requests),
        knowledge_count=len(cache.knowledge or []),
        classified=classified,
        rendered_outputs=tuple(rendered),
    )


def _render_outputs(cache: CacheFile, *, human_out: Path | None, ai_out: Path | None) -> list[Path]:
    written: list[Path] = []
    if human_out is not None:
        get_renderer("human").render(cache, human_out)
        logger.info("rendered human Markdown to %s", human_out)
        written.append(human_out)
    if ai_out is not None:
        get_renderer("ai").render(cache, ai_out)
        logger.info("rendered AI Markdown to %s", ai_out)
        written.append(ai_out)
    return written


__all__ = ["GenerateSummary", "run_generate"]
