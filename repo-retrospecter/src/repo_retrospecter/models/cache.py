"""CacheFile model: serialized form of `.retrospect/cache.json` (ADR-0003)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from repo_retrospecter.models.commit import Commit
from repo_retrospecter.models.knowledge import Knowledge
from repo_retrospecter.models.pull_request import PullRequest

CACHE_SCHEMA_VERSION = "1"


class CacheFile(BaseModel):
    """Unified intermediate cache (ADR-0003 一体管理).

    `schema_version` is checked on load (OQ-03): mismatched versions
    surface a warning and trigger re-fetch suggestion. `knowledge` is
    optional because `fetch` runs persist PRs before classification.

    `loose_commits` carries default-branch commits that are NOT associated
    with any merged PR in the same run (PRD F1 + 仕様乖離修正 2026-05-04).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=CACHE_SCHEMA_VERSION)
    generated_at: datetime
    repo: str
    pull_requests: list[PullRequest] = Field(default_factory=list[PullRequest])
    loose_commits: list[Commit] = Field(default_factory=list[Commit])
    knowledge: list[Knowledge] | None = None


__all__ = ["CACHE_SCHEMA_VERSION", "CacheFile"]
