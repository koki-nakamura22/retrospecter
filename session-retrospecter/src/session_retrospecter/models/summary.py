"""Pipeline Summaries — 各 orchestrator の戻り値 (frozen)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .extraction import Kind
from .target import TargetSpec


class FetchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: TargetSpec
    session_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    cache_path: Path


class ExtractSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int = Field(ge=0)
    by_kind: dict[Kind, int]
    cache_path: Path


class GenerateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int = Field(ge=0)
    knowledge_count: int = Field(ge=0)
    classified: int = Field(ge=0, description="うち今回 LLM call した数 (--append 効果計測)")
    rendered_outputs: list[Path]


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fetch: FetchSummary
    extract: ExtractSummary
    generate: GenerateSummary


__all__ = [
    "ExtractSummary",
    "FetchSummary",
    "GenerateSummary",
    "RunSummary",
]
