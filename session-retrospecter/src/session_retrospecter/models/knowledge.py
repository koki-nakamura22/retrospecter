"""Knowledge — classifier 出力 / renderer 入力."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CanonicalTheme = Literal[
    "correction",
    "validated_pattern",
    "tool_pitfall",
    "decision_rationale",
    "other",
]

CANONICAL_THEMES: tuple[CanonicalTheme, ...] = (
    "correction",
    "validated_pattern",
    "tool_pitfall",
    "decision_rationale",
    "other",
)

DEFAULT_THEMES: tuple[CanonicalTheme, ...] = CANONICAL_THEMES

Theme = str


class Knowledge(BaseModel):
    """LLM 分類後の単位知識. AI-facing renderer / human renderer 双方の入力.

    `sources` は `session://<id>#L<n>` の集合で 1 件以上必須 (TC-F4-02 相当 / T-06).
    """

    model_config = ConfigDict(extra="forbid")

    rule: str = Field(min_length=1)
    anti_pattern: str = Field(min_length=1)
    example: str = Field(min_length=1)
    sources: list[str] = Field(
        min_length=1,
        description="session://<id>#L<n> の list. 0 件の Knowledge は renderer がエラー.",
    )
    themes: list[Theme] = Field(default_factory=list)


__all__ = [
    "CANONICAL_THEMES",
    "DEFAULT_THEMES",
    "CanonicalTheme",
    "Knowledge",
    "Theme",
]
