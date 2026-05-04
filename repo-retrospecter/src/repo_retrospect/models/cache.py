"""CacheFile model: serialized form of `.retrospect/cache.json` (ADR-0003)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from repo_retrospect.models.knowledge import Knowledge
from repo_retrospect.models.pull_request import PullRequest

CACHE_SCHEMA_VERSION = "1"


class CacheFile(BaseModel):
    """Unified intermediate cache (ADR-0003 一体管理).

    `schema_version` is checked on load (OQ-03): mismatched versions
    surface a warning and trigger re-fetch suggestion. `knowledge` is
    optional because `fetch` runs persist PRs before classification.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=CACHE_SCHEMA_VERSION)
    generated_at: datetime
    repo: str
    pull_requests: list[PullRequest] = Field(default_factory=list[PullRequest])
    knowledge: list[Knowledge] | None = None


__all__ = ["CACHE_SCHEMA_VERSION", "CacheFile"]
