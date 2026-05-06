"""tests.test_renderer — services.renderer.human / ai のユニットテスト."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from session_retrospecter.models.cache import Cache
from session_retrospecter.models.knowledge import Knowledge
from session_retrospecter.models.target import TargetSpec
from session_retrospecter.services.renderer import ai, human

_GOLDEN_DIR = Path(__file__).parent / "fixtures" / "expected"

_RULE = "不要な try/except を全関数に追加しない"
_ANTI = "全関数を try/except でラップする防御的プログラミング"
_EXAMPLE = (
    "`Wrapped every function in try/except.` → "
    "ユーザーが「don't add try/except where it's not needed」と訂正 → 必要な箇所だけに残す。"
)
_SOURCE = "session://correction-en-fixture#L3"


@pytest.fixture()
def knowledge_correction() -> Knowledge:
    return Knowledge(
        rule=_RULE,
        anti_pattern=_ANTI,
        example=_EXAMPLE,
        sources=[_SOURCE],
        themes=["correction"],
    )


@pytest.fixture()
def cache_from_correction_en(knowledge_correction: Knowledge) -> Cache:
    return Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[knowledge_correction],
    )


@pytest.fixture()
def empty_cache() -> Cache:
    return Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[],
    )


# ---------------------------------------------------------------------------
# human.render — golden comparison (AC6)
# ---------------------------------------------------------------------------


def test_human_golden(cache_from_correction_en: Cache) -> None:
    expected = (_GOLDEN_DIR / "human_correction.md").read_text(encoding="utf-8")

    # Act
    result = human.render(cache_from_correction_en)

    assert result == expected


# ---------------------------------------------------------------------------
# ai.render — golden comparison (AC6)
# ---------------------------------------------------------------------------


def test_ai_golden(cache_from_correction_en: Cache) -> None:
    expected = (_GOLDEN_DIR / "ai_correction.md").read_text(encoding="utf-8")

    # Act
    result = ai.render(cache_from_correction_en)

    assert result == expected


# ---------------------------------------------------------------------------
# AC4: sources URI が出力にそのまま現れる
# ---------------------------------------------------------------------------


def test_human_sources_appear_verbatim(cache_from_correction_en: Cache) -> None:
    result = human.render(cache_from_correction_en)

    assert _SOURCE in result


def test_ai_sources_appear_verbatim(cache_from_correction_en: Cache) -> None:
    result = ai.render(cache_from_correction_en)

    assert _SOURCE in result


# ---------------------------------------------------------------------------
# AC5: sources が空の Knowledge → ValueError
# ---------------------------------------------------------------------------


def test_human_raises_on_empty_sources(cache_from_correction_en: Cache) -> None:
    k_bad = Knowledge.model_construct(
        rule=_RULE, anti_pattern=_ANTI, example=_EXAMPLE, sources=[], themes=["correction"]
    )
    bad_cache = cache_from_correction_en.model_copy(update={"knowledge": [k_bad]})

    with pytest.raises(ValueError, match="sources が空"):
        human.render(bad_cache)


def test_ai_raises_on_empty_sources(cache_from_correction_en: Cache) -> None:
    k_bad = Knowledge.model_construct(
        rule=_RULE, anti_pattern=_ANTI, example=_EXAMPLE, sources=[], themes=["correction"]
    )
    bad_cache = cache_from_correction_en.model_copy(update={"knowledge": [k_bad]})

    with pytest.raises(ValueError, match="sources が空"):
        ai.render(bad_cache)


# ---------------------------------------------------------------------------
# テーマ別セクション分類 (AC2)
# ---------------------------------------------------------------------------


def test_human_correction_appears_in_correction_section(
    cache_from_correction_en: Cache,
) -> None:
    result = human.render(cache_from_correction_en)

    lines = result.splitlines()
    correction_idx = next(i for i, line in enumerate(lines) if "ユーザーが訂正した判断" in line)
    validated_idx = next(i for i, line in enumerate(lines) if "検証済みパターン" in line)
    rule_idx = next(i for i, line in enumerate(lines) if _RULE in line)

    assert correction_idx < rule_idx < validated_idx


def test_human_empty_sections_show_placeholder(empty_cache: Cache) -> None:
    result = human.render(empty_cache)

    # 4 セクション全てに (該当なし) が出る
    assert result.count("(該当なし)") == 4


def test_human_non_correction_theme_in_correct_section() -> None:
    k = Knowledge(
        rule="テスト rule",
        anti_pattern="テスト anti",
        example="テスト example",
        sources=["session://s#L1"],
        themes=["validated_pattern"],
    )
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[k],
    )

    result = human.render(cache)

    lines = result.splitlines()
    validated_idx = next(i for i, line in enumerate(lines) if "検証済みパターン" in line)
    rule_idx = next(i for i, line in enumerate(lines) if "テスト rule" in line)
    tool_idx = next(i for i, line in enumerate(lines) if "ツール落とし穴" in line)

    assert validated_idx < rule_idx < tool_idx


# ---------------------------------------------------------------------------
# AI renderer — 複数 Knowledge
# ---------------------------------------------------------------------------


def test_ai_multiple_knowledge_items() -> None:
    items = [
        Knowledge(
            rule=f"rule {i}",
            anti_pattern=f"anti {i}",
            example=f"example {i}",
            sources=[f"session://s#L{i}"],
            themes=["correction"],
        )
        for i in range(3)
    ]
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=items,
    )

    result = ai.render(cache)

    assert result.count("## Knowledge:") == 3
    assert "session://s#L0" in result
    assert "session://s#L1" in result
    assert "session://s#L2" in result


# ---------------------------------------------------------------------------
# AC4 補完: 複数 sources がすべて出力に現れる
# ---------------------------------------------------------------------------


def test_human_multiple_sources_all_appear() -> None:
    src1 = "session://session-a#L1"
    src2 = "session://session-b#L99"
    k = Knowledge(
        rule="multi-source rule",
        anti_pattern="anti",
        example="example",
        sources=[src1, src2],
        themes=["correction"],
    )
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[k],
    )

    result = human.render(cache)

    assert src1 in result
    assert src2 in result


def test_ai_multiple_sources_all_appear() -> None:
    src1 = "session://session-a#L1"
    src2 = "session://session-b#L99"
    k = Knowledge(
        rule="multi-source rule",
        anti_pattern="anti",
        example="example",
        sources=[src1, src2],
        themes=["correction"],
    )
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[k],
    )

    result = ai.render(cache)

    assert src1 in result
    assert src2 in result


# ---------------------------------------------------------------------------
# AC2: themes が "other" / 未知テーマの Knowledge は human の固定 4 セクションに出ない
# ---------------------------------------------------------------------------


def test_human_other_theme_not_in_fixed_sections() -> None:
    k = Knowledge(
        rule="other-theme rule",
        anti_pattern="anti",
        example="example",
        sources=["session://s#L1"],
        themes=["other"],
    )
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[k],
    )

    result = human.render(cache)

    assert "other-theme rule" not in result
    assert result.count("(該当なし)") == 4


def test_ai_other_theme_knowledge_still_appears() -> None:
    k = Knowledge(
        rule="other-theme rule",
        anti_pattern="anti",
        example="example",
        sources=["session://s#L1"],
        themes=["other"],
    )
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=[k],
    )

    result = ai.render(cache)

    assert "other-theme rule" in result


# ---------------------------------------------------------------------------
# knowledge=None の場合はエラーなし (cache.knowledge は Optional)
# ---------------------------------------------------------------------------


def test_human_none_knowledge_returns_all_placeholders() -> None:
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=None,
    )

    result = human.render(cache)

    assert result.count("(該当なし)") == 4


def test_ai_none_knowledge_returns_header_only() -> None:
    cache = Cache(
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        target=TargetSpec(mode="all"),
        knowledge=None,
    )

    result = ai.render(cache)

    assert result.strip() == "# Knowledge"
