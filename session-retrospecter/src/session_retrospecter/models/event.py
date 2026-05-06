"""SessionEvent / Session — JSONL を正規化した値オブジェクト."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "user",
    "assistant",
    "system",
    "permission-mode",
    "attachment",
    "file-history-snapshot",
    "last-prompt",
    "ai-title",
]


class SessionEvent(BaseModel):
    """JSONL 1 行を正規化した値オブジェクト.

    `extra="allow"` で Claude Code 側のフィールド追加に耐える (OQ-05).
    `text` と `content` は排他的ではない: text-only event は `text` のみ,
    tool_use / tool_result を含む event は `content` (構造化ブロック) を使う.
    """

    model_config = ConfigDict(extra="allow", frozen=False)

    type: EventType | str
    line_no: int = Field(ge=1, description="JSONL 1-origin 行番号 (出典生成に必須)")
    session_id: str = Field(description="親 Session の id (fetcher で注入)")

    text: str | None = None

    content: list[dict[str, Any]] | None = None

    raw: dict[str, Any] | None = None


class Session(BaseModel):
    """1 つの .jsonl ファイル全体."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    source_path: Path
    project_dir: Path = Field(description="encoded-cwd ディレクトリ (~/.claude/projects/<encoded>)")
    events: list[SessionEvent] = Field(default_factory=list[SessionEvent])
    parse_warnings: list[str] = Field(
        default_factory=list,
        description="parse 失敗行などの警告メモ (TC-EX-15)",
    )


__all__ = ["EventType", "Session", "SessionEvent"]
