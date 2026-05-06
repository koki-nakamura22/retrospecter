"""pipeline.generate — generate オーケストレーター (classify + render)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..cache import store as cache_store
from ..models.knowledge import DEFAULT_THEMES, Knowledge
from ..models.summary import GenerateSummary
from ..services import classifier as classifier_module
from ..services.renderer import ai as ai_renderer
from ..services.renderer import human as human_renderer

__all__ = ["run"]

logger = logging.getLogger(__name__)


def run(
    *,
    cache_path: Path,
    out: Path,
    ai_out: Path,
    themes: list[str] | None = None,
    append: bool = False,
    force: bool = False,
    classify_fn: Callable[..., list[Knowledge]] | None = None,
) -> GenerateSummary:
    """cache から candidates を分類し Markdown 2 系統を出力する.

    AC3: out / ai_out が既存 + force=False + append=False → FileExistsError.
    AC4: append=True 時、既存 knowledge の citations を cached_citations として渡し LLM skip.
    AC5: classify_fn で分類器を DI 可能 (テスト用 fake 受け付け).
    """
    if themes is None:
        themes = list(DEFAULT_THEMES)

    if not append and not force:
        conflicts = [p for p in (out, ai_out) if p.exists()]
        if conflicts:
            paths_str = ", ".join(str(p) for p in conflicts)
            raise FileExistsError(
                "出力ファイルが既に存在します "
                f"(--force または --append を指定してください): {paths_str}"
            )

    cache = cache_store.load(cache_path)
    existing_knowledge: list[Knowledge] = cache.knowledge or []

    cached_citations: set[str] = set()
    if append:
        for k in existing_knowledge:
            cached_citations.update(k.sources)

    candidates = cache.candidates
    new_candidate_count = len([c for c in candidates if c.citation not in cached_citations])

    classify = classify_fn if classify_fn is not None else classifier_module.classify
    new_knowledge = classify(candidates, themes=themes, cached_citations=cached_citations)

    all_knowledge = existing_knowledge + new_knowledge if append else new_knowledge

    updated = cache.model_copy(update={"knowledge": all_knowledge})
    cache_store.save(updated, cache_path)

    rendered_outputs: list[Path] = []

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(human_renderer.render(updated), encoding="utf-8", newline="\n")
    rendered_outputs.append(out)

    ai_out.parent.mkdir(parents=True, exist_ok=True)
    ai_out.write_text(ai_renderer.render(updated), encoding="utf-8", newline="\n")
    rendered_outputs.append(ai_out)

    return GenerateSummary(
        candidate_count=len(candidates),
        knowledge_count=len(all_knowledge),
        classified=new_candidate_count,
        rendered_outputs=rendered_outputs,
    )
