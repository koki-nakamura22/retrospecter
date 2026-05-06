"""tests.test_pipeline — pipeline orchestrator の統合テスト (TC-PL-01〜05)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_retrospecter.models.cache import Cache
from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import Knowledge
from session_retrospecter.models.target import TargetSpec
from session_retrospecter.pipeline import extract, fetch, generate
from session_retrospecter.pipeline.run import run as run_all

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

_CORRECTION_EVENTS: list[dict[str, str]] = [
    {"type": "assistant", "text": "Let me refactor the entire codebase."},
    {"type": "user", "text": "no don't do that"},
]


def _write_jsonl(path: Path, events: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def _session_spec(session_path: Path) -> TargetSpec:
    return TargetSpec(mode="session", session=session_path)


def _fake_classify(
    candidates: list[ExtractionCandidate],
    *,
    themes: list[str] | None = None,
    cached_citations: set[str] | None = None,
) -> list[Knowledge]:
    """テスト用 stub: LLM を呼ばずに候補ごとに Knowledge を生成する."""
    _cached = cached_citations or set()
    return [
        Knowledge(
            rule=f"rule for {c.citation}",
            anti_pattern="stub anti_pattern",
            example="stub example",
            sources=[c.citation],
            themes=[c.kind],
        )
        for c in candidates
        if c.citation not in _cached
    ]


# ---------------------------------------------------------------------------
# TC-PL-01: fetch のみ → cache に sessions が反映される
# ---------------------------------------------------------------------------


def test_fetch_saves_sessions_to_cache(tmp_path: Path) -> None:
    # Arrange
    session_file = tmp_path / "project" / "abc123.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    spec = _session_spec(session_file)

    # Act
    summary = fetch.run(spec, cache_path=cache_path)

    # Assert — summary
    assert summary.session_count == 1
    assert summary.event_count == len(_CORRECTION_EVENTS)
    assert summary.cache_path == cache_path

    # Assert — cache ファイル内容
    assert cache_path.exists()
    loaded = Cache.model_validate_json(cache_path.read_text(encoding="utf-8"))
    assert len(loaded.sessions) == 1
    assert loaded.sessions[0].session_id == "abc123"
    assert len(loaded.sessions[0].events) == len(_CORRECTION_EVENTS)


# ---------------------------------------------------------------------------
# TC-PL-02: extract → candidates 反映 (LLM 呼ばれない事を assert)
# ---------------------------------------------------------------------------


def test_extract_updates_candidates_without_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess01.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    fetch.run(_session_spec(session_file), cache_path=cache_path)

    def _must_not_call(*args: object, **kwargs: object) -> list[Knowledge]:
        raise AssertionError("LLM should not be called during extract")

    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _must_not_call)

    # Act
    summary = extract.run(cache_path=cache_path)

    # Assert — summary
    assert summary.candidate_count > 0
    assert "correction" in summary.by_kind
    assert summary.by_kind["correction"] >= 1

    # Assert — cache には候補が保存されている
    loaded = Cache.model_validate_json(cache_path.read_text(encoding="utf-8"))
    assert len(loaded.candidates) == summary.candidate_count
    assert loaded.candidates[0].kind == "correction"


# ---------------------------------------------------------------------------
# TC-PL-03: generate → knowledge + md 出力
# ---------------------------------------------------------------------------


def test_generate_produces_knowledge_and_md_output(tmp_path: Path) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess02.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "reports" / "human.md"
    ai_out = tmp_path / "reports" / "ai.md"

    fetch.run(_session_spec(session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)

    # Act
    summary = generate.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        classify_fn=_fake_classify,
    )

    # Assert — summary
    assert summary.candidate_count > 0
    assert summary.knowledge_count > 0
    assert summary.classified == summary.candidate_count  # non-append: 全件 classify
    assert summary.classified > 0  # 候補が 0 件でないことも確認
    assert out in summary.rendered_outputs
    assert ai_out in summary.rendered_outputs

    # Assert — ファイルが作成されている
    assert out.exists()
    assert ai_out.exists()

    # Assert — cache に knowledge が保存されている
    loaded = Cache.model_validate_json(cache_path.read_text(encoding="utf-8"))
    assert loaded.knowledge is not None
    assert len(loaded.knowledge) == summary.knowledge_count


# ---------------------------------------------------------------------------
# TC-PL-04: run → 全 stage 通過
# ---------------------------------------------------------------------------


def test_run_passes_all_stages(tmp_path: Path) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess03.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"
    spec = _session_spec(session_file)

    # Act
    summary = run_all(
        spec,
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        classify_fn=_fake_classify,
    )

    # Assert — 全 stage の summary が揃っている
    assert summary.fetch.session_count == 1
    assert summary.fetch.event_count == len(_CORRECTION_EVENTS)
    assert summary.extract.candidate_count > 0
    assert summary.generate.knowledge_count > 0
    assert summary.generate.knowledge_count >= summary.extract.candidate_count

    # Assert — 出力ファイルが作成されている
    assert out.exists()
    assert ai_out.exists()

    # Assert — cache が存在し、全 stage の成果物が入っている
    loaded = Cache.model_validate_json(cache_path.read_text(encoding="utf-8"))
    assert len(loaded.sessions) == 1
    assert len(loaded.candidates) > 0
    assert loaded.knowledge is not None and len(loaded.knowledge) > 0


# ---------------------------------------------------------------------------
# append モード: 既存 knowledge が保持され、新 knowledge のみ追加される
# ---------------------------------------------------------------------------


def test_generate_append_preserves_existing_knowledge(tmp_path: Path) -> None:
    # Arrange — 1回目: 通常実行で knowledge を生成
    session_file = tmp_path / "project" / "sessA.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    fetch.run(_session_spec(session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)
    first_summary = generate.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        classify_fn=_fake_classify,
    )
    first_knowledge_count = first_summary.knowledge_count

    # Act — 2回目: append=True で同じ candidates を再 generate
    second_summary = generate.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        append=True,
        classify_fn=_fake_classify,
    )

    # Assert — 2回目は cached_citations により新規 classify が 0 件
    assert second_summary.classified == 0
    # Assert — knowledge_count は 1回目と同じ (既存が保持されている)
    assert second_summary.knowledge_count == first_knowledge_count


def test_generate_append_skips_all_cached_citations(tmp_path: Path) -> None:
    # Arrange — 通常実行で全候補を知識化済みにする
    session_file = tmp_path / "project" / "sessB.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    fetch.run(_session_spec(session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)
    generate.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        classify_fn=_fake_classify,
    )
    before_cache = Cache.model_validate_json(cache_path.read_text(encoding="utf-8"))
    before_count = len(before_cache.knowledge or [])

    # Act — append=True で再実行 (全候補が既に cached_citations に含まれる)
    summary = generate.run(
        cache_path=cache_path,
        out=out,
        ai_out=ai_out,
        append=True,
        classify_fn=_fake_classify,
    )

    # Assert — LLM call なし (classified=0) で knowledge 件数は変わらない
    assert summary.classified == 0
    assert summary.knowledge_count == before_count


# ---------------------------------------------------------------------------
# TC-PL-05: 既存 md + --force なし → FileExistsError
# ---------------------------------------------------------------------------


def test_generate_raises_when_output_exists_and_no_force(tmp_path: Path) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess04.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    fetch.run(_session_spec(session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)
    out.write_text("既存コンテンツ", encoding="utf-8")  # 既存ファイルを作成

    # Act + Assert
    with pytest.raises(FileExistsError):
        generate.run(
            cache_path=cache_path,
            out=out,
            ai_out=ai_out,
            append=False,
            force=False,
            classify_fn=_fake_classify,
        )
