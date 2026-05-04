"""Knowledge model: extracted Rule / Anti-pattern / Example unit."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from repo_retrospecter.models.theme import Theme


class Knowledge(BaseModel):
    """A single piece of extracted knowledge from PR history.

    `source_urls` is required to be non-empty by downstream renderers
    (TC-F4-02: AI-facing output must cite source PR URLs); validation of
    "non-empty" is left to renderer policy so that classifier output can
    still be persisted for inspection.
    """

    model_config = ConfigDict(extra="forbid")

    rule: str
    anti_pattern: str
    example: str
    source_urls: list[str] = Field(default_factory=list[str])
    themes: list[Theme] = Field(default_factory=list[Theme])


__all__ = ["Knowledge"]
