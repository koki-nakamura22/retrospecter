"""ドメインモデル (Pydantic v2) の契約テスト.

TC-MO-01〜05 は traceability.md と brief T002 AC4〜6 に紐づく.
追加で frozen / Cache.schema_version 不一致のバリデーションも検証する.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from session_retrospecter.models import (
    CACHE_SCHEMA_VERSION,
    Cache,
    ExtractionCandidate,
    FetchSummary,
    Knowledge,
    RedactOptions,
    Session,
    SessionEvent,
    TargetSpec,
)

# ---------------------------------------------------------------------------
# TC-MO-01: ExtractionCandidate.citation の正規表現強制
# ---------------------------------------------------------------------------


def test_extraction_candidate_accepts_valid_citation() -> None:
    valid_citation = "session://abc-123_XYZ#L42"

    candidate = ExtractionCandidate(
        kind="correction",
        session_id="abc-123_XYZ",
        line_no=42,
        context="ctx",
        citation=valid_citation,
    )

    assert candidate.citation == valid_citation


@pytest.mark.parametrize(
    "invalid_citation",
    [
        "abc-123#L42",  # スキーム欠如
        "session://abc-123",  # #L<n> 欠如
        "session://abc 123#L42",  # 空白混入 (許可文字外)
        "session://abc-123#Labc",  # line_no が数字でない
        "session://abc-123#L",  # line_no 空
        "",  # 空文字
    ],
)
def test_extraction_candidate_rejects_invalid_citation(invalid_citation: str) -> None:
    with pytest.raises(ValidationError):
        ExtractionCandidate(
            kind="correction",
            session_id="abc-123",
            line_no=1,
            context="ctx",
            citation=invalid_citation,
        )


def test_extraction_candidate_metadata_defaults_to_empty_dict() -> None:
    candidate = ExtractionCandidate(
        kind="correction",
        session_id="abc",
        line_no=1,
        context="ctx",
        citation="session://abc#L1",
    )

    assert candidate.metadata == {}


def test_extraction_candidate_rejects_non_positive_line_no() -> None:
    with pytest.raises(ValidationError):
        ExtractionCandidate(
            kind="correction",
            session_id="abc",
            line_no=0,
            context="ctx",
            citation="session://abc#L1",
        )


# ---------------------------------------------------------------------------
# TC-MO-02: Knowledge.sources の min_length=1 強制
# ---------------------------------------------------------------------------


def test_knowledge_rejects_empty_sources() -> None:
    with pytest.raises(ValidationError):
        Knowledge(
            rule="r",
            anti_pattern="ap",
            example="ex",
            sources=[],
        )


def test_knowledge_accepts_single_source() -> None:
    knowledge = Knowledge(
        rule="r",
        anti_pattern="ap",
        example="ex",
        sources=["session://abc#L1"],
    )

    assert knowledge.sources == ["session://abc#L1"]
    assert knowledge.themes == []


def test_knowledge_rejects_empty_string_field() -> None:
    with pytest.raises(ValidationError):
        Knowledge(
            rule="",
            anti_pattern="ap",
            example="ex",
            sources=["session://abc#L1"],
        )


# ---------------------------------------------------------------------------
# TC-MO-03: SessionEvent の extra="allow" / line_no 1-origin
# ---------------------------------------------------------------------------


def test_session_event_allows_unknown_type() -> None:
    event = SessionEvent(
        type="future-unknown-type",
        line_no=1,
        session_id="abc",
    )

    assert event.type == "future-unknown-type"


def test_session_event_retains_extra_fields() -> None:
    event = SessionEvent.model_validate(
        {
            "type": "user",
            "line_no": 1,
            "session_id": "abc",
            "future_field": "kept",
        }
    )
    dumped = event.model_dump()

    assert dumped["future_field"] == "kept"


def test_session_event_rejects_zero_line_no() -> None:
    with pytest.raises(ValidationError):
        SessionEvent(type="user", line_no=0, session_id="abc")


def test_session_event_accepts_one_line_no() -> None:
    event = SessionEvent(type="user", line_no=1, session_id="abc")

    assert event.line_no == 1


# ---------------------------------------------------------------------------
# TC-MO-04 / 05: RedactOptions defaults
# ---------------------------------------------------------------------------


def test_redact_options_default_mask_tokens_is_true() -> None:
    opts = RedactOptions()

    assert opts.mask_tokens is True


def test_redact_options_default_mask_paths_is_false() -> None:
    opts = RedactOptions()

    assert opts.mask_paths is False


def test_redact_options_default_exclude_tools_is_empty() -> None:
    opts = RedactOptions()

    assert opts.exclude_tools == frozenset()


# ---------------------------------------------------------------------------
# AC7: frozen 契約
# ---------------------------------------------------------------------------


def test_extraction_candidate_is_frozen() -> None:
    candidate = ExtractionCandidate(
        kind="correction",
        session_id="abc",
        line_no=1,
        context="ctx",
        citation="session://abc#L1",
    )

    with pytest.raises(ValidationError):
        candidate.kind = "tool_pitfall"  # type: ignore[misc]


def test_redact_options_is_frozen() -> None:
    opts = RedactOptions()

    with pytest.raises(ValidationError):
        opts.mask_tokens = False  # type: ignore[misc]


def test_target_spec_is_frozen() -> None:
    spec = TargetSpec(mode="all")

    with pytest.raises(ValidationError):
        spec.mode = "session"  # type: ignore[misc]


def test_target_spec_defaults_exclude_projects_is_empty() -> None:
    spec = TargetSpec(mode="all")

    assert spec.exclude_projects == frozenset()
    assert spec.project is None
    assert spec.session is None
    assert spec.since is None


def test_fetch_summary_is_frozen() -> None:
    spec = TargetSpec(mode="all")
    summary = FetchSummary(
        target=spec,
        session_count=0,
        event_count=0,
        cache_path=Path(".retrospect/cache.json"),
    )

    with pytest.raises(ValidationError):
        summary.session_count = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC8: Cache.schema_version 不一致は load でエラー
# ---------------------------------------------------------------------------


def test_cache_accepts_matching_schema_version() -> None:
    cache = Cache(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
    )

    assert cache.schema_version == CACHE_SCHEMA_VERSION


def test_cache_rejects_mismatched_schema_version() -> None:
    with pytest.raises(ValidationError):
        Cache(
            schema_version="999",
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            target=TargetSpec(mode="all"),
        )


def test_cache_rejects_mismatched_schema_version_via_validate() -> None:
    payload = {
        "schema_version": "0",
        "generated_at": "2026-01-01T00:00:00Z",
        "target": {"mode": "all"},
    }

    with pytest.raises(ValidationError):
        Cache.model_validate(payload)


# ---------------------------------------------------------------------------
# Session の組成 (smoke) — events を持つ
# ---------------------------------------------------------------------------


def test_session_holds_events() -> None:
    event = SessionEvent(type="user", line_no=1, session_id="abc")
    session = Session(
        session_id="abc",
        source_path=Path("/tmp/abc.jsonl"),
        project_dir=Path("/tmp/projects/encoded"),
        events=[event],
    )

    assert session.events[0].line_no == 1
    assert session.parse_warnings == []
