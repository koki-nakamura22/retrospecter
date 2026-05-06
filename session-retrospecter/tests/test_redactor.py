"""TC-RD-01〜12: services.redactor の受け入れテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_retrospecter.models.event import Session, SessionEvent
from session_retrospecter.models.redact import RedactOptions
from session_retrospecter.services.redactor import (
    _redact_text,
    _redact_tool_use_block,
    redact_event,
    redact_session,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    text: str | None = None,
    content: list[dict] | None = None,  # type: ignore[type-arg]
    line_no: int = 1,
    session_id: str = "sess-001",
    etype: str = "assistant",
) -> SessionEvent:
    return SessionEvent(
        type=etype,
        line_no=line_no,
        session_id=session_id,
        text=text,
        content=content,
    )


# ---------------------------------------------------------------------------
# TC-RD-01: token redaction — Anthropic key (sk-ant-)
# ---------------------------------------------------------------------------


def test_redact_text_anthropic_ant_key() -> None:
    text = "My key is sk-ant-abc123XYZ_456 please"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "My key is <redacted-token> please"


# ---------------------------------------------------------------------------
# TC-RD-01b: token redaction — Anthropic key (sk-proj-)
# ---------------------------------------------------------------------------


def test_redact_text_anthropic_proj_key() -> None:
    text = "token=sk-proj-SomeLongTokenValue_here"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "token=<redacted-token>"


# ---------------------------------------------------------------------------
# TC-RD-02: token redaction — GitHub PAT
# ---------------------------------------------------------------------------


def test_redact_text_github_pat() -> None:
    text = "export GH_TOKEN=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "export GH_TOKEN=<redacted-token>"


# ---------------------------------------------------------------------------
# TC-RD-03: token redaction — Bearer header
# ---------------------------------------------------------------------------


def test_redact_text_bearer_token() -> None:
    text = 'curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.xyz"'
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == 'curl -H "Authorization: <redacted-token>"'


# ---------------------------------------------------------------------------
# TC-RD-04: token redaction — AWS access key
# ---------------------------------------------------------------------------


def test_redact_text_aws_access_key() -> None:
    text = "AKIAIOSFODNN7EXAMPLE"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "<redacted-token>"


# ---------------------------------------------------------------------------
# TC-RD-05: 偽陽性回避 — ski-resort は変更されない
# ---------------------------------------------------------------------------


def test_redact_text_no_false_positive_ski_resort() -> None:
    text = "ski-resort is fun"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "ski-resort is fun"


@pytest.mark.parametrize(
    "safe_text",
    [
        "sk-learn is a library",
        "sk-short",
        "sk-",
    ],
)
def test_redact_text_no_false_positive_sk_variants(safe_text: str) -> None:
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(safe_text, opts)

    assert result == safe_text


# ---------------------------------------------------------------------------
# TC-RD-06: path redaction — Linux home
# ---------------------------------------------------------------------------


def test_redact_text_linux_home_path() -> None:
    text = "Wrote to /home/koki-n/dev/private/secret.txt"
    opts = RedactOptions(mask_paths=True)

    result = _redact_text(text, opts)

    assert result == "Wrote to <path>"


# ---------------------------------------------------------------------------
# TC-RD-07: path redaction — macOS home
# ---------------------------------------------------------------------------


def test_redact_text_macos_home_path() -> None:
    text = "See /Users/alice/projects/foo"
    opts = RedactOptions(mask_paths=True)

    result = _redact_text(text, opts)

    assert result == "See <path>"


# ---------------------------------------------------------------------------
# TC-RD-08: path redaction OFF (default) — 変更されない
# ---------------------------------------------------------------------------


def test_redact_text_path_unchanged_by_default() -> None:
    text = "Wrote to /home/koki-n/dev/private/secret.txt"
    opts = RedactOptions()  # mask_paths=False by default

    result = _redact_text(text, opts)

    assert result == text


# ---------------------------------------------------------------------------
# TC-RD-09: tool 除外 — Bash の input が <excluded:Bash> に置換される
# ---------------------------------------------------------------------------


def test_redact_event_excludes_bash_input() -> None:
    ev = _make_event(
        content=[{"type": "tool_use", "name": "Bash", "input": {"command": "ls /etc/passwd"}}]
    )
    opts = RedactOptions(exclude_tools=frozenset({"Bash"}))

    result = redact_event(ev, opts)

    assert result.content is not None
    bash_block = result.content[0]
    assert bash_block["name"] == "Bash"
    assert bash_block["input"] == "<excluded:Bash>"


# ---------------------------------------------------------------------------
# TC-RD-10: tool 除外 — 他 tool (Read) は無変更
# ---------------------------------------------------------------------------


def test_redact_event_excludes_only_specified_tool() -> None:
    ev = _make_event(
        content=[
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}},
        ]
    )
    opts = RedactOptions(exclude_tools=frozenset({"Bash"}))

    result = redact_event(ev, opts)

    assert result.content is not None
    bash_block = result.content[0]
    read_block = result.content[1]
    assert bash_block["input"] == "<excluded:Bash>"
    assert read_block["input"] == {"file_path": "/tmp/x.py"}


# ---------------------------------------------------------------------------
# TC-RD-11: immutability — 元オブジェクトのフィールドは変更されない
# ---------------------------------------------------------------------------


def test_redact_event_does_not_mutate_original() -> None:
    original_text = "My key is sk-ant-abc123XYZ_456 please"
    ev = _make_event(text=original_text)
    opts = RedactOptions(mask_tokens=True)

    redacted = redact_event(ev, opts)

    assert ev.text == original_text
    assert redacted.text != original_text
    assert redacted is not ev


def test_redact_event_content_immutability() -> None:
    original_input = {"command": "echo hello"}
    ev = _make_event(
        content=[{"type": "tool_use", "name": "Bash", "input": dict(original_input)}]
    )
    opts = RedactOptions(exclude_tools=frozenset({"Bash"}))

    redact_event(ev, opts)

    assert ev.content is not None
    assert ev.content[0]["input"] == original_input


# ---------------------------------------------------------------------------
# TC-RD-12: line_no / session_id は redact 前後で保持される
# ---------------------------------------------------------------------------


def test_redact_event_preserves_line_no_and_session_id() -> None:
    ev = _make_event(text="sk-ant-abc123XYZ_456", line_no=42, session_id="sess-abc-999")
    opts = RedactOptions(mask_tokens=True)

    result = redact_event(ev, opts)

    assert result.line_no == 42
    assert result.session_id == "sess-abc-999"


# ---------------------------------------------------------------------------
# 追加: mask_tokens=False — トークンは変更されない
# ---------------------------------------------------------------------------


def test_redact_text_tokens_unchanged_when_mask_off() -> None:
    text = "sk-ant-abc123XYZ_456 and AKIAIOSFODNN7EXAMPLE"
    opts = RedactOptions(mask_tokens=False)

    result = _redact_text(text, opts)

    assert result == text


# ---------------------------------------------------------------------------
# 追加: _redact_tool_use_block — tool_use 以外のブロックは無変更
# ---------------------------------------------------------------------------


def test_redact_tool_use_block_ignores_non_tool_use() -> None:
    block = {"type": "text", "text": "hello sk-ant-abc123XYZ_456"}
    opts = RedactOptions(exclude_tools=frozenset({"Bash"}))

    result = _redact_tool_use_block(block, opts)

    assert result is block


# ---------------------------------------------------------------------------
# 追加: redact_session — すべてのイベントが redact される
# ---------------------------------------------------------------------------


def test_redact_session_redacts_all_events() -> None:
    session = Session(
        session_id="s1",
        source_path=Path("/tmp/s1.jsonl"),
        project_dir=Path("/tmp"),
        events=[
            _make_event(text="key=sk-ant-abc123XYZ_456", line_no=1, session_id="s1"),
            _make_event(text="key=sk-proj-secret999xyz", line_no=2, session_id="s1"),
        ],
    )
    opts = RedactOptions(mask_tokens=True)

    result = redact_session(session, opts)

    assert result.session_id == "s1"
    assert all("<redacted-token>" in (ev.text or "") for ev in result.events)
    assert all(ev.session_id == "s1" for ev in result.events)
    assert all("sk-ant-" not in (ev.text or "") for ev in result.events)


# ---------------------------------------------------------------------------
# 追加: redact_session — 元 Session のイベントは変更されない
# ---------------------------------------------------------------------------


def test_redact_session_does_not_mutate_original() -> None:
    original_text = "key=sk-ant-abc123XYZ_456"
    session = Session(
        session_id="s2",
        source_path=Path("/tmp/s2.jsonl"),
        project_dir=Path("/tmp"),
        events=[_make_event(text=original_text, line_no=1, session_id="s2")],
    )
    opts = RedactOptions(mask_tokens=True)

    redact_session(session, opts)

    assert session.events[0].text == original_text


# ---------------------------------------------------------------------------
# 追加: text=None イベントは text が None のまま返る
# ---------------------------------------------------------------------------


def test_redact_event_none_text_passthrough() -> None:
    ev = _make_event(text=None)
    opts = RedactOptions(mask_tokens=True)

    result = redact_event(ev, opts)

    assert result.text is None


# ---------------------------------------------------------------------------
# 追加: content ブロック内の text フィールドのトークンも除去される
# ---------------------------------------------------------------------------


def test_redact_event_content_block_text_field_redacted() -> None:
    ev = _make_event(
        content=[{"type": "text", "text": "Found key sk-ant-abc123XYZ_456 in logs"}]
    )
    opts = RedactOptions(mask_tokens=True)

    result = redact_event(ev, opts)

    assert result.content is not None
    assert result.content[0]["text"] == "Found key <redacted-token> in logs"


# ---------------------------------------------------------------------------
# 追加: 複数種類のトークンが混在するテキスト — 全件マスクされる
# ---------------------------------------------------------------------------


def test_redact_text_multiple_token_types() -> None:
    text = "key=sk-ant-abc123XYZ_456 token=AKIAIOSFODNN7EXAMPLE"
    opts = RedactOptions(mask_tokens=True)

    result = _redact_text(text, opts)

    assert result == "key=<redacted-token> token=<redacted-token>"
