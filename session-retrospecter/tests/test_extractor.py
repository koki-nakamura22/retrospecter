"""TC-EX-01〜15: services.extractor の受け入れテスト."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from session_retrospecter.models.event import Session, SessionEvent
from session_retrospecter.services.extractor import extract
from session_retrospecter.services.fetcher import read_session

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"^session://[A-Za-z0-9_\-]+#L\d+$")
_SESSION_ID = "test-session"

# 1018 chars — contains "because" (pos 72) then "trade-off" (pos 425)
_DECISION_TEXT = (
    "I chose to use middleware for handling redirects because it provides a centralized "
    "location for redirect logic, making it easier to maintain and update across the entire "
    "application. When you handle redirects at the middleware level, every request passes "
    "through a single point of control, which gives you a clear and auditable trail of all "
    "redirect decisions. This is especially important in large applications where multiple "
    "developers might otherwise implement redirects inconsistently across different routes. "
    "The trade-off here is real: adding middleware increases the complexity of your request "
    "pipeline, and there is a small performance overhead for every request, even those that "
    "do not need to be redirected. However, in practice this overhead is negligible compared "
    "to the benefits of having a single source of truth for redirect logic. The alternative, "
    "handling redirects in individual route handlers, leads to duplication and makes it easy "
    "to forget to update all the places when the redirect rules change."
)

# 900 chars — no decision keywords ("because", "since", "だから", "理由", "trade-off")
_LONG_TEXT_NO_KEYWORDS = "The implementation follows a clean, well-organized structure. " * 15

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sessions"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_event(
    etype: str,
    *,
    text: str | None = None,
    content: list[dict[str, Any]] | None = None,
    line_no: int = 1,
    session_id: str = _SESSION_ID,
) -> SessionEvent:
    return SessionEvent(
        type=etype,
        line_no=line_no,
        session_id=session_id,
        text=text,
        content=content,
    )


def _make_session(
    events: list[SessionEvent],
    session_id: str = _SESSION_ID,
) -> Session:
    return Session(
        session_id=session_id,
        source_path=Path(f"/tmp/{session_id}.jsonl"),
        project_dir=Path("/tmp"),
        events=events,
    )


# ---------------------------------------------------------------------------
# TC-EX-01: correction (英語明示否定)
# ---------------------------------------------------------------------------


def test_correction_english_explicit_negation() -> None:
    """TC-EX-01: 'no' + 'don't' を含む user turn が correction を生む."""
    # Arrange
    session = _make_session(
        [
            _make_event("user", text="Add error handling to all functions", line_no=1),
            _make_event("assistant", text="Wrapped every function in try/except.", line_no=2),
            _make_event(
                "user",
                text="no, don't add try/except where it's not needed",
                line_no=3,
            ),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "correction"
    assert c.line_no == 3
    assert c.session_id == _SESSION_ID
    assert c.metadata["lexicon_hit"] == ["no", "don't"]
    assert c.metadata["preceding_assistant_line"] == 2
    assert c.citation == f"session://{_SESSION_ID}#L3"
    assert _CITATION_RE.match(c.citation)
    assert "Wrapped every function" in c.context
    assert "no, don't" in c.context


# ---------------------------------------------------------------------------
# TC-EX-02: correction (日本語明示否定)
# ---------------------------------------------------------------------------


def test_correction_japanese_negation() -> None:
    """TC-EX-02: 'そうじゃ' を含む user turn が correction を生む."""
    # Arrange
    session = _make_session(
        [
            _make_event("assistant", text="route ファイルに redirect を書きました。", line_no=6),
            _make_event("user", text="そうじゃなくて、middlewareを使って", line_no=7),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "correction"
    assert c.line_no == 7
    assert "そうじゃ" in c.metadata["lexicon_hit"]
    assert c.metadata["preceding_assistant_line"] == 6
    assert _CITATION_RE.match(c.citation)


# ---------------------------------------------------------------------------
# TC-EX-03: correction (lexicon 不在 → 抽出されない / 偽陽性回避)
# ---------------------------------------------------------------------------


def test_correction_no_lexicon_hit_returns_empty() -> None:
    """TC-EX-03: correction lexicon を含まない user turn は抽出されない."""
    # Arrange
    session = _make_session(
        [
            _make_event("assistant", text="Added DB layer.", line_no=4),
            _make_event("user", text="Now please add the database layer", line_no=5),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-04: correction (assistant 直前ターン無し → 抽出されない)
# ---------------------------------------------------------------------------


def test_correction_no_preceding_assistant_returns_empty() -> None:
    """TC-EX-04: 会話冒頭 user が直前 assistant 無しのため correction にならない."""
    # Arrange
    session = _make_session(
        [
            _make_event("user", text="don't do X", line_no=1),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-05: validated_pattern (短文承認)
# ---------------------------------------------------------------------------


def test_validated_pattern_short_approval() -> None:
    """TC-EX-05: 80 字以内の approval word + 直前 assistant → validated_pattern."""
    # Arrange
    session = _make_session(
        [
            _make_event(
                "assistant",
                text="Used middleware for redirects (not _redirects).",
                line_no=4,
            ),
            _make_event("user", text="perfect", line_no=5),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "validated_pattern"
    assert c.line_no == 5
    assert "perfect" in c.metadata["lexicon_hit"]
    assert c.metadata["preceding_assistant_line"] == 4
    assert _CITATION_RE.match(c.citation)


# ---------------------------------------------------------------------------
# TC-EX-06: validated_pattern (長文 → 抽出されない)
# ---------------------------------------------------------------------------


def test_validated_pattern_long_text_returns_empty() -> None:
    """TC-EX-06: 80 字超の user turn は validated_pattern にならない."""
    # text は 81 文字 (APPROVAL_MAX_CHARS + 1) — 境界値の外側
    long_approval = "yes " + "x" * 77  # len == 81
    session = _make_session(
        [
            _make_event("assistant", text="Done.", line_no=3),
            _make_event("user", text=long_approval, line_no=4),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


def test_validated_pattern_exactly_80_chars_is_included() -> None:
    """境界値: 80 字ちょうど (APPROVAL_MAX_CHARS) は validated_pattern になる."""
    # text は 80 文字ちょうど — "<= 80" の境界
    text_80 = "yes " + "x" * 76  # len == 80
    session = _make_session(
        [
            _make_event("assistant", text="Done.", line_no=1),
            _make_event("user", text=text_80, line_no=2),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    assert candidates[0].kind == "validated_pattern"


# ---------------------------------------------------------------------------
# TC-EX-07: validated_pattern (assistant 直前無し → 抽出されない)
# ---------------------------------------------------------------------------


def test_validated_pattern_no_preceding_assistant_returns_empty() -> None:
    """TC-EX-07: 会話冒頭の approval は validated_pattern にならない."""
    # Arrange
    session = _make_session(
        [
            _make_event("user", text="yes", line_no=1),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-08: tool_pitfall (is_error → 修正再呼出)
# ---------------------------------------------------------------------------


def test_tool_pitfall_error_with_same_tool_retry() -> None:
    """TC-EX-08: tool_result.is_error 後に同一 tool を異なる input で再呼出 → tool_pitfall."""
    # Arrange
    session = _make_session(
        [
            _make_event(
                "assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "a1",
                        "name": "Bash",
                        "input": {"command": "ls /nonexistent"},
                    }
                ],
                line_no=10,
            ),
            _make_event(
                "user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "a1",
                        "is_error": True,
                        "content": "No such file",
                    }
                ],
                line_no=11,
            ),
            _make_event(
                "assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "a2",
                        "name": "Bash",
                        "input": {"command": "ls /home"},
                    }
                ],
                line_no=12,
            ),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "tool_pitfall"
    assert c.line_no == 10  # 失敗ターン起点
    assert c.metadata["tool"] == "Bash"
    assert c.metadata["failed_input"] == {"command": "ls /nonexistent"}
    assert c.metadata["fixed_input"] == {"command": "ls /home"}
    assert c.citation == f"session://{_SESSION_ID}#L10"
    assert _CITATION_RE.match(c.citation)


# ---------------------------------------------------------------------------
# TC-EX-09: tool_pitfall (is_error あるが再呼出無し → 抽出されない)
# ---------------------------------------------------------------------------


def test_tool_pitfall_error_without_retry_returns_empty() -> None:
    """TC-EX-09: tool error の後続 assistant retry が無ければ tool_pitfall にならない."""
    # Arrange
    session = _make_session(
        [
            _make_event(
                "assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "a1",
                        "name": "Bash",
                        "input": {"command": "ls /nonexistent"},
                    }
                ],
                line_no=10,
            ),
            _make_event(
                "user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "a1",
                        "is_error": True,
                        "content": "No such file",
                    }
                ],
                line_no=11,
            ),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-10: tool_pitfall (異なる tool で再試行 → 抽出されない)
# ---------------------------------------------------------------------------


def test_tool_pitfall_different_tool_retry_returns_empty() -> None:
    """TC-EX-10: 異なる tool 名での再試行は tool_pitfall にならない (同名が要件)."""
    # Arrange
    session = _make_session(
        [
            _make_event(
                "assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "a1",
                        "name": "Bash",
                        "input": {"command": "ls /nonexistent"},
                    }
                ],
                line_no=10,
            ),
            _make_event(
                "user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": "a1",
                        "is_error": True,
                        "content": "No such file",
                    }
                ],
                line_no=11,
            ),
            _make_event(
                "assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "a2",
                        "name": "Read",
                        "input": {"path": "/home"},
                    }
                ],
                line_no=12,
            ),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-11: decision_rationale (長文 + キーワード)
# ---------------------------------------------------------------------------


def test_decision_rationale_long_text_with_keywords() -> None:
    """TC-EX-11: 800 字超かつ 'because'/'trade-off' 含む assistant → decision_rationale."""
    # Arrange
    session = _make_session(
        [
            _make_event("assistant", text=_DECISION_TEXT, line_no=20),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "decision_rationale"
    assert c.line_no == 20
    assert set(c.metadata["keywords_hit"]) >= {"because", "trade-off"}
    assert _CITATION_RE.match(c.citation)


# ---------------------------------------------------------------------------
# TC-EX-12: decision_rationale (長文だがキーワード無し → 抽出されない)
# ---------------------------------------------------------------------------


def test_decision_rationale_long_text_no_keywords_returns_empty() -> None:
    """TC-EX-12: 800 字超でもキーワード無しは decision_rationale にならない."""
    # Arrange
    session = _make_session(
        [
            _make_event("assistant", text=_LONG_TEXT_NO_KEYWORDS, line_no=3),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-13: decision_rationale (キーワードあるが短文 → 抽出されない)
# ---------------------------------------------------------------------------


def test_decision_rationale_short_text_with_keyword_returns_empty() -> None:
    """TC-EX-13: キーワードを含んでも 800 字未満は decision_rationale にならない."""
    # Arrange
    short_text = "Used X because Y."
    session = _make_session(
        [
            _make_event("assistant", text=short_text, line_no=3),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


# ---------------------------------------------------------------------------
# TC-EX-14: 複数種の混在抽出 (順序保証)
# ---------------------------------------------------------------------------


def test_decision_rationale_exactly_800_chars_is_included() -> None:
    """境界値: 800 字ちょうど (DECISION_MIN_CHARS) は decision_rationale になる."""
    # text は 800 文字ちょうど — ">= 800" の境界
    text_800 = "because " + "x" * 792  # len == 800
    session = _make_session(
        [
            _make_event("assistant", text=text_800, line_no=1),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 1
    assert candidates[0].kind == "decision_rationale"
    assert "because" in candidates[0].metadata["keywords_hit"]


def test_correction_preceding_user_not_assistant_returns_empty() -> None:
    """直前ターンが user (assistant でない) の場合は correction にならない."""
    # Arrange
    session = _make_session(
        [
            _make_event("user", text="Can you help?", line_no=1),
            _make_event("user", text="no, don't do that", line_no=2),
        ]
    )

    # Act
    candidates = extract(session)

    # Assert
    assert candidates == []


def test_mixed_extraction_all_kinds_in_line_no_order() -> None:
    """TC-EX-14: 4 種混在 JSONL から 4 件が line_no 昇順 (3 < 5 < 10 < 20) で返る."""
    # Arrange
    session = read_session(_FIXTURES_DIR / "mixed.jsonl")

    # Act
    candidates = extract(session)

    # Assert
    assert len(candidates) == 4
    assert [c.kind for c in candidates] == [
        "correction",
        "validated_pattern",
        "tool_pitfall",
        "decision_rationale",
    ]
    line_nos = [c.line_no for c in candidates]
    assert line_nos == [3, 5, 10, 20]  # 昇順保証
    for c in candidates:
        assert _CITATION_RE.match(c.citation)


# ---------------------------------------------------------------------------
# TC-EX-15: 不正 JSON 行 → 該当行 skip + 警告ログ
# ---------------------------------------------------------------------------


def test_invalid_json_line_skipped_extraction_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-EX-15: 5 行目が不正 JSON でも残り 4 件の decision_rationale が抽出される."""
    # Arrange: 4 valid decision_rationale events + invalid JSON at line 5
    fixture = tmp_path / "with_bad_json.jsonl"
    fixture.write_text(
        "\n".join(
            [
                json.dumps({"type": "assistant", "text": _DECISION_TEXT + " A"}),
                json.dumps({"type": "assistant", "text": _DECISION_TEXT + " B"}),
                json.dumps({"type": "assistant", "text": _DECISION_TEXT + " C"}),
                json.dumps({"type": "assistant", "text": _DECISION_TEXT + " D"}),
                "NOT VALID JSON {{{",  # line 5 — 不正 JSON: fetcher が skip + WARNING
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        session = read_session(fixture)

    # Act
    candidates = extract(session)

    # Assert: 4 件 (5 行目関連は欠落)
    assert len(candidates) == 4
    assert all(c.kind == "decision_rationale" for c in candidates)
    # ログレベル WARNING に該当行記録
    assert len(session.parse_warnings) == 1
    assert "line 5" in session.parse_warnings[0]
    assert any("JSON parse failed" in msg for msg in caplog.messages)
    # line_no 昇順
    line_nos = [c.line_no for c in candidates]
    assert line_nos == sorted(line_nos)
    for c in candidates:
        assert _CITATION_RE.match(c.citation)
