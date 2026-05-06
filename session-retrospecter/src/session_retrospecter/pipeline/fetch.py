"""pipeline.fetch — fetch オーケストレーター (sessions → cache)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..cache import store as cache_store
from ..models.cache import Cache
from ..models.redact import RedactOptions
from ..models.summary import FetchSummary
from ..models.target import TargetSpec
from ..services import fetcher, redactor

__all__ = ["run"]


def run(
    spec: TargetSpec,
    *,
    cache_path: Path,
    redact_opts: RedactOptions | None = None,
) -> FetchSummary:
    """TargetSpec から sessions を取得して cache に保存する.

    redact_opts が指定された場合、保存前に各 session をリダクト処理する.
    cache_path が既存でも上書きする (append は pipeline.run.run 層で制御).
    """
    sessions = fetcher.read_target(spec)

    if redact_opts is not None:
        sessions = [redactor.redact_session(s, redact_opts) for s in sessions]

    event_count = sum(len(s.events) for s in sessions)

    cache = Cache(
        generated_at=datetime.now(tz=timezone.utc),
        target=spec,
        sessions=sessions,
    )
    cache_store.save(cache, cache_path)

    return FetchSummary(
        target=spec,
        session_count=len(sessions),
        event_count=event_count,
        cache_path=cache_path,
    )
