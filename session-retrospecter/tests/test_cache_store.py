"""TC-CA-01〜05: cache.store の受け入れテスト."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

from session_retrospecter.cache.store import load, merge_append, save
from session_retrospecter.models.cache import CACHE_SCHEMA_VERSION, Cache
from session_retrospecter.models.event import Session
from session_retrospecter.models.extraction import ExtractionCandidate, Kind
from session_retrospecter.models.knowledge import Knowledge
from session_retrospecter.models.target import TargetSpec
from session_retrospecter.services.exceptions import FetchError

# ---------------------------------------------------------------------------
# constants / helpers
# ---------------------------------------------------------------------------

_BASE_DT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_FROZEN_DT_STR = "2026-06-01T00:00:00+00:00"
_FROZEN_DT = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_target() -> TargetSpec:
    return TargetSpec(mode="all")


def _make_cache(
    sessions: list[Session] | None = None,
    candidates: list[ExtractionCandidate] | None = None,
    knowledge: list[Knowledge] | None = None,
    generated_at: datetime = _BASE_DT,
) -> Cache:
    return Cache(
        generated_at=generated_at,
        target=_make_target(),
        sessions=sessions or [],
        candidates=candidates or [],
        knowledge=knowledge,
    )


def _make_session(session_id: str = "s1", source: str | None = None) -> Session:
    return Session(
        session_id=session_id,
        source_path=Path(source or f"/tmp/{session_id}.jsonl"),
        project_dir=Path("/tmp"),
    )


def _make_candidate(
    session_id: str = "s1",
    line_no: int = 1,
    kind: Kind = "correction",
) -> ExtractionCandidate:
    return ExtractionCandidate(
        kind=kind,
        session_id=session_id,
        line_no=line_no,
        context="example context",
        citation=f"session://{session_id}#L{line_no}",
    )


def _make_knowledge(sources: list[str] | None = None) -> Knowledge:
    return Knowledge(
        rule="Use X",
        anti_pattern="Don't use Y",
        example="Example.",
        sources=sources or ["session://s1#L1"],
    )


# ---------------------------------------------------------------------------
# TC-CA-01: save → load round-trip
# ---------------------------------------------------------------------------


def test_round_trip_preserves_cache_data(tmp_path: Path) -> None:
    """TC-CA-01: save → load の round-trip で元の Cache が復元される."""
    # Arrange
    cache = _make_cache(
        sessions=[_make_session("a1")],
        candidates=[_make_candidate("a1", 1, "correction")],
        knowledge=[_make_knowledge(["session://a1#L1"])],
    )
    path = tmp_path / ".retrospect" / "cache.json"

    # Act
    save(cache, path)
    loaded = load(path)

    # Assert
    assert loaded.schema_version == CACHE_SCHEMA_VERSION
    assert loaded.generated_at == cache.generated_at
    assert len(loaded.sessions) == 1
    assert loaded.sessions[0].session_id == "a1"
    assert len(loaded.candidates) == 1
    assert loaded.candidates[0].citation == "session://a1#L1"
    assert loaded.knowledge is not None
    assert loaded.knowledge[0].sources == ["session://a1#L1"]


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    """save は親ディレクトリが存在しなくても自動作成する (AC2)."""
    # Arrange
    cache = _make_cache()
    nested_path = tmp_path / "deep" / "nested" / "cache.json"

    # Act
    save(cache, nested_path)

    # Assert
    assert nested_path.exists()
    loaded = load(nested_path)
    assert loaded.schema_version == CACHE_SCHEMA_VERSION


def test_save_uses_utf8_lf(tmp_path: Path) -> None:
    """save は UTF-8 / LF で書き込む (AC5)."""
    # Arrange
    cache = _make_cache()
    path = tmp_path / "cache.json"

    # Act
    save(cache, path)
    raw = path.read_bytes()

    # Assert: CR (0x0D) が含まれないことで LF のみを確認
    assert b"\r" not in raw
    # UTF-8 BOM なし
    assert not raw.startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# TC-CA-02: schema_version mismatch → FetchError
# ---------------------------------------------------------------------------


def test_load_schema_version_mismatch_raises_fetch_error(tmp_path: Path) -> None:
    """TC-CA-02: schema_version 不一致の JSON を load すると FetchError が上がる."""
    # Arrange
    bad_json = json.dumps(
        {
            "schema_version": "99",
            "generated_at": "2026-01-01T00:00:00Z",
            "target": {"mode": "all", "exclude_projects": []},
            "sessions": [],
            "candidates": [],
            "knowledge": None,
        }
    )
    path = tmp_path / "bad.json"
    path.write_text(bad_json, encoding="utf-8")

    # Act / Assert
    with pytest.raises(FetchError):
        load(path)


def test_load_invalid_json_raises_fetch_error(tmp_path: Path) -> None:
    """壊れた JSON ファイルを load すると FetchError が上がる."""
    # Arrange
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json", encoding="utf-8")

    # Act / Assert
    with pytest.raises(FetchError):
        load(path)


# ---------------------------------------------------------------------------
# TC-CA-03: merge_append — sessions existing 勝ち
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_sessions_existing_wins() -> None:
    """TC-CA-03: merge_append で同一 session_id は existing の Session が残る."""
    # Arrange
    existing_session = _make_session("shared", source="/existing/shared.jsonl")
    new_session = _make_session("shared", source="/other/shared.jsonl")
    extra_new_session = _make_session("new-only")

    existing = _make_cache(sessions=[existing_session])
    new = _make_cache(sessions=[new_session, extra_new_session])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert len(merged.sessions) == 2
    by_id = {s.session_id: s for s in merged.sessions}
    assert str(by_id["shared"].source_path) == "/existing/shared.jsonl"
    assert "new-only" in by_id
    assert merged.generated_at == _FROZEN_DT


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_sessions_disjoint_all_added() -> None:
    """TC-CA-03: session_id が重複しない場合はすべて追加される."""
    # Arrange
    existing = _make_cache(sessions=[_make_session("s1")])
    new = _make_cache(sessions=[_make_session("s2"), _make_session("s3")])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert len(merged.sessions) == 3
    ids = {s.session_id for s in merged.sessions}
    assert ids == {"s1", "s2", "s3"}


# ---------------------------------------------------------------------------
# TC-CA-04: merge_append — candidates (session_id, line_no, kind) dedup
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_candidates_dedup_by_key() -> None:
    """TC-CA-04: merge_append で (session_id, line_no, kind) 重複は 1 件に絞られる."""
    # Arrange
    dup = _make_candidate("s1", 5, "correction")
    unique_in_new = _make_candidate("s1", 10, "correction")

    existing = _make_cache(candidates=[dup])
    new = _make_cache(candidates=[dup, unique_in_new])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert len(merged.candidates) == 2
    citations = {c.citation for c in merged.candidates}
    assert citations == {"session://s1#L5", "session://s1#L10"}


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_candidates_same_position_different_kind_both_kept() -> None:
    """同 session_id・line_no でも kind が違えば別エントリとして残る."""
    # Arrange
    c1 = _make_candidate("s1", 5, "correction")
    c2 = _make_candidate("s1", 5, "decision_rationale")

    existing = _make_cache(candidates=[c1])
    new = _make_cache(candidates=[c2])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert len(merged.candidates) == 2
    kinds = {c.kind for c in merged.candidates}
    assert kinds == {"correction", "decision_rationale"}


# ---------------------------------------------------------------------------
# TC-CA-05: merge_append — knowledge citation (sources) dedup
# ---------------------------------------------------------------------------


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_knowledge_citation_dedup() -> None:
    """TC-CA-05: merge_append で sources が同一の Knowledge は重複除去され existing が残る."""
    # Arrange
    shared_sources = ["session://s1#L1", "session://s1#L2"]
    existing_k = Knowledge(
        rule="Existing rule", anti_pattern="x", example="y", sources=shared_sources
    )
    new_k = Knowledge(
        rule="New rule", anti_pattern="a", example="b", sources=shared_sources
    )
    unique_k = _make_knowledge(["session://s2#L1"])

    existing = _make_cache(knowledge=[existing_k])
    new = _make_cache(knowledge=[new_k, unique_k])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert merged.knowledge is not None
    assert len(merged.knowledge) == 2
    deduped_rules = {k.rule for k in merged.knowledge}
    assert "Existing rule" in deduped_rules
    assert "New rule" not in deduped_rules


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_knowledge_both_none_stays_none() -> None:
    """existing と new 両方 knowledge=None なら merged も None になる."""
    # Arrange
    existing = _make_cache(knowledge=None)
    new = _make_cache(knowledge=None)

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert merged.knowledge is None


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_knowledge_only_new_has_knowledge() -> None:
    """only new が knowledge を持つ場合は new の knowledge が引き継がれる."""
    # Arrange
    k = _make_knowledge(["session://s2#L3"])
    existing = _make_cache(knowledge=None)
    new = _make_cache(knowledge=[k])

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert merged.knowledge is not None
    assert len(merged.knowledge) == 1
    assert merged.knowledge[0].sources == ["session://s2#L3"]


@freeze_time(_FROZEN_DT_STR)
def test_merge_append_knowledge_only_existing_has_knowledge() -> None:
    """existing のみが knowledge を持つ場合は existing の knowledge がそのまま引き継がれる."""
    # Arrange
    k = _make_knowledge(["session://s1#L1"])
    existing = _make_cache(knowledge=[k])
    new = _make_cache(knowledge=None)

    # Act
    merged = merge_append(existing, new)

    # Assert
    assert merged.knowledge is not None
    assert len(merged.knowledge) == 1
    assert merged.knowledge[0].sources == ["session://s1#L1"]
