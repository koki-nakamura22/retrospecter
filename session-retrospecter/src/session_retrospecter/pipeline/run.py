"""pipeline.run — 3 段一気通貫オーケストレーター (fetch → extract → generate)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..cache import store as cache_store
from ..models.cache import Cache
from ..models.extraction import ExtractionCandidate, Kind
from ..models.knowledge import DEFAULT_THEMES, Knowledge
from ..models.redact import RedactOptions
from ..models.summary import ExtractSummary, FetchSummary, RunSummary
from ..models.target import TargetSpec
from ..services import extractor, fetcher, redactor
from . import extract as extract_pipeline
from . import fetch as fetch_pipeline
from . import generate as generate_pipeline

__all__ = ["run"]

logger = logging.getLogger(__name__)


def run(
    spec: TargetSpec,
    *,
    cache_path: Path,
    out: Path,
    ai_out: Path,
    redact_opts: RedactOptions | None = None,
    themes: list[str] | None = None,
    append: bool = False,
    force: bool = False,
    classify_fn: Callable[..., list[Knowledge]] | None = None,
) -> RunSummary:
    """fetch → extract → generate を一気通貫で実行する.

    non-append: 3 pipeline stage を順次委譲.
    append: 既存 cache を保持したまま新 sessions を merge してから generate.
    """
    if themes is None:
        themes = list(DEFAULT_THEMES)

    if not append:
        fetch_sum = fetch_pipeline.run(spec, cache_path=cache_path, redact_opts=redact_opts)
        extract_sum = extract_pipeline.run(cache_path=cache_path)
        gen_sum = generate_pipeline.run(
            cache_path=cache_path,
            out=out,
            ai_out=ai_out,
            themes=themes,
            append=False,
            force=force,
            classify_fn=classify_fn,
        )
        return RunSummary(fetch=fetch_sum, extract=extract_sum, generate=gen_sum)

    # --- append mode: merge 既存 cache + 新 sessions ---
    existing = cache_store.load(cache_path) if cache_path.exists() else None

    sessions = fetcher.read_target(spec)
    if redact_opts is not None:
        sessions = [redactor.redact_session(s, redact_opts) for s in sessions]

    event_count = sum(len(s.events) for s in sessions)

    new_candidates: list[ExtractionCandidate] = []
    for session in sessions:
        try:
            new_candidates.extend(extractor.extract(session))
        except Exception as exc:
            logger.warning("session extract failed, skipping: %s", exc)

    new_cache = Cache(
        generated_at=datetime.now(tz=timezone.utc),
        target=spec,
        sessions=sessions,
        candidates=new_candidates,
    )

    merged = cache_store.merge_append(existing, new_cache) if existing is not None else new_cache
    cache_store.save(merged, cache_path)

    fetch_sum = FetchSummary(
        target=spec,
        session_count=len(sessions),
        event_count=event_count,
        cache_path=cache_path,
    )

    by_kind: dict[Kind, int] = {}
    for c in merged.candidates:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1  # type: ignore[index]

    extract_sum = ExtractSummary(
        candidate_count=len(merged.candidates),
        by_kind=by_kind,
        cache_path=cache_path,
    )

    gen_sum = generate_pipeline.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        themes=themes,
        append=True,
        force=force,
        classify_fn=classify_fn,
    )

    return RunSummary(fetch=fetch_sum, extract=extract_sum, generate=gen_sum)
