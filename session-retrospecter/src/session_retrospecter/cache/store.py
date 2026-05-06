"""cache.store — Cache JSON round-trip と append merge."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ..models.cache import CACHE_SCHEMA_VERSION, Cache
from ..models.extraction import ExtractionCandidate
from ..models.knowledge import Knowledge
from ..services.exceptions import FetchError

DEFAULT_CACHE_PATH = Path(".retrospect/cache.json")

__all__ = ["DEFAULT_CACHE_PATH", "load", "merge_append", "save"]


def load(path: Path) -> Cache:
    """JSON ファイルから Cache を復元. schema_version 不一致は FetchError."""
    try:
        return Cache.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise FetchError(f"cache parse error: {exc}") from exc


def save(cache: Cache, path: Path) -> None:
    """Cache を pretty JSON で保存 (UTF-8 / LF). 親ディレクトリは自動作成."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.model_dump_json(indent=2), encoding="utf-8", newline="\n")


def merge_append(existing: Cache, new: Cache) -> Cache:
    """既存 cache に new を併合. existing 勝ち / citation dedup."""
    existing_ids = {s.session_id for s in existing.sessions}
    merged_sessions = list(existing.sessions) + [
        s for s in new.sessions if s.session_id not in existing_ids
    ]

    seen_candidates: set[tuple[str, int, str]] = set()
    merged_candidates: list[ExtractionCandidate] = []
    for c in list(existing.candidates) + list(new.candidates):
        key = (c.session_id, c.line_no, c.kind)
        if key not in seen_candidates:
            seen_candidates.add(key)
            merged_candidates.append(c)

    existing_knowledge = existing.knowledge or []
    new_knowledge = new.knowledge or []
    merged_knowledge: list[Knowledge] | None = None
    if existing_knowledge or new_knowledge:
        seen_sources: set[frozenset[str]] = set()
        merged_knowledge = []
        for k in existing_knowledge + new_knowledge:
            key_sources = frozenset(k.sources)
            if key_sources not in seen_sources:
                seen_sources.add(key_sources)
                merged_knowledge.append(k)

    return Cache(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime.now(tz=timezone.utc),
        target=existing.target,
        sessions=merged_sessions,
        candidates=merged_candidates,
        knowledge=merged_knowledge,
    )
