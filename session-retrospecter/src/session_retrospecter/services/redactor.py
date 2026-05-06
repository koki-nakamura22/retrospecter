"""services.redactor — token / path / tool 除外で SessionEvent を安全化 (threat T-01〜T-03)."""

from __future__ import annotations

import re
from typing import Any

from session_retrospecter.models.event import Session, SessionEvent
from session_retrospecter.models.redact import RedactOptions

TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-(?:ant|proj)-[A-Za-z0-9+/=_\-]+"),
    re.compile(r"ghp_[A-Za-z0-9]{32,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"/home/[^\s/]+(?:/[^\s]*)?"),
    re.compile(r"/Users/[^\s/]+(?:/[^\s]*)?"),
]

_TOKEN_MASK = "<redacted-token>"
_PATH_MASK = "<path>"


def _redact_text(text: str, opts: RedactOptions) -> str:
    if opts.mask_tokens:
        for pattern in TOKEN_PATTERNS:
            text = pattern.sub(_TOKEN_MASK, text)
    if opts.mask_paths:
        for pattern in PATH_PATTERNS:
            text = pattern.sub(_PATH_MASK, text)
    return text


def _redact_tool_use_block(block: dict[str, Any], opts: RedactOptions) -> dict[str, Any]:
    if block.get("type") != "tool_use":
        return block
    name = block.get("name", "")
    if name in opts.exclude_tools:
        return {**block, "input": f"<excluded:{name}>"}
    return block


def redact_event(ev: SessionEvent, opts: RedactOptions) -> SessionEvent:
    updates: dict[str, Any] = {}

    if ev.text is not None:
        updates["text"] = _redact_text(ev.text, opts)

    if ev.content is not None:
        new_content: list[dict[str, Any]] = []
        for block in ev.content:
            block = _redact_tool_use_block(dict(block), opts)
            if isinstance(block.get("text"), str):
                block = {**block, "text": _redact_text(block["text"], opts)}
            new_content.append(block)
        updates["content"] = new_content

    return ev.model_copy(update=updates)


def redact_session(s: Session, opts: RedactOptions) -> Session:
    return Session(
        session_id=s.session_id,
        source_path=s.source_path,
        project_dir=s.project_dir,
        events=[redact_event(ev, opts) for ev in s.events],
        parse_warnings=list(s.parse_warnings),
    )


__all__ = [
    "TOKEN_PATTERNS",
    "PATH_PATTERNS",
    "_redact_text",
    "_redact_tool_use_block",
    "redact_event",
    "redact_session",
]
