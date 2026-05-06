"""ExtractionCandidate — extractor 出力 / classifier 入力."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal[
    "correction",
    "validated_pattern",
    "tool_pitfall",
    "decision_rationale",
]


class ExtractionCandidate(BaseModel):
    """extractor の 1 候補. test-cases/extractor.md TC-EX-01〜14 の expected shape.

    `citation` は `session://<session_id>#L<line_no>` 形式で必ず生成する
    (initial-requirements §6 / threat-model T-06 受け入れ条件).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Kind
    session_id: str
    line_no: int = Field(ge=1)
    context: str = Field(description="抽出本文 (前後 1-2 ターン同梱)")
    metadata: dict[str, Any] = Field(default_factory=dict)
    citation: str = Field(
        pattern=r"^session://[A-Za-z0-9_\-]+#L\d+$",
        description="session://<id>#L<n>",
    )


__all__ = ["ExtractionCandidate", "Kind"]
