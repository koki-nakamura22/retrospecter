"""TargetSpec — fetcher の入力指定 (CLI flags を畳み込んだ値オブジェクト)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TargetMode = Literal["session", "project", "all"]


class TargetSpec(BaseModel):
    """fetcher の入力指定. 排他優先順位: session > project > all."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: TargetMode
    projects_root: Path = Field(default=Path("~/.claude/projects").expanduser())
    project: Path | None = None
    session: Path | None = None
    since: date | None = None
    exclude_projects: frozenset[str] = Field(default_factory=frozenset)


__all__ = ["TargetMode", "TargetSpec"]
