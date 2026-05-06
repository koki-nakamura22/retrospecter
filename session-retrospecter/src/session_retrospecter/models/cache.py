"""Cache — `.retrospect/cache.json` の serialized form."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .event import Session
from .extraction import ExtractionCandidate
from .knowledge import Knowledge
from .target import TargetSpec

CACHE_SCHEMA_VERSION = "1"


class Cache(BaseModel):
    """中間キャッシュ. fetch / extract / generate を跨いで使う統合 cache.

    schema_version 不一致は load 側でエラー (decision-defaults §キャッシュ /
    repo-retrospecter ADR-0003 と同方針).
    `--append` merge は session_id 単位 (existing 勝ち) / knowledge は citation 集合で dedup.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=CACHE_SCHEMA_VERSION)
    generated_at: datetime
    target: TargetSpec
    sessions: list[Session] = Field(default_factory=list[Session])
    candidates: list[ExtractionCandidate] = Field(default_factory=list[ExtractionCandidate])
    knowledge: list[Knowledge] | None = None

    @field_validator("schema_version")
    @classmethod
    def _check_schema_version(cls, v: str) -> str:
        if v != CACHE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version mismatch: expected {CACHE_SCHEMA_VERSION!r}, got {v!r}"
            )
        return v


__all__ = ["CACHE_SCHEMA_VERSION", "Cache"]
