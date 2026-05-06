"""RedactOptions — services.redactor の挙動制御 (threat-model T-01〜T-03)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RedactOptions(BaseModel):
    """services.redactor の挙動制御.

    Cross-ref: threat-model.md T-01 (token), T-02 (path), T-03 (cache 共有).
    decision-defaults: mask_tokens=True / mask_paths=False が default.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mask_tokens: bool = True
    mask_paths: bool = False
    exclude_tools: frozenset[str] = Field(default_factory=frozenset)


__all__ = ["RedactOptions"]
