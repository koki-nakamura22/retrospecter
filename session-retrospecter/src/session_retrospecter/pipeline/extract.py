"""pipeline.extract — extract オーケストレーター (cache 内 sessions → candidates)."""

from __future__ import annotations

import logging
from pathlib import Path

from ..cache import store as cache_store
from ..models.extraction import ExtractionCandidate, Kind
from ..models.summary import ExtractSummary
from ..services import extractor

__all__ = ["run"]

logger = logging.getLogger(__name__)


def run(*, cache_path: Path) -> ExtractSummary:
    """既存 cache を読み候補を抽出して再保存する (LLM call なし).

    部分失敗 (1 session の extract エラー) は WARN + 継続 (AC2 / _common.md).
    candidates は (session_id, line_no, kind) で dedup する.
    """
    cache = cache_store.load(cache_path)

    all_candidates: list[ExtractionCandidate] = []
    for session in cache.sessions:
        try:
            all_candidates.extend(extractor.extract(session))
        except Exception as exc:
            logger.warning("session extract failed, skipping: %s", exc)

    seen: set[tuple[str, int, str]] = set()
    deduped: list[ExtractionCandidate] = []
    for c in all_candidates:
        key = (c.session_id, c.line_no, c.kind)
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    updated = cache.model_copy(update={"candidates": deduped})
    cache_store.save(updated, cache_path)

    by_kind: dict[Kind, int] = {}
    for c in deduped:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1  # type: ignore[index]

    return ExtractSummary(
        candidate_count=len(deduped),
        by_kind=by_kind,
        cache_path=cache_path,
    )
