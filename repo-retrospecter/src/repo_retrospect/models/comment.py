"""Comment model: a single PR comment / review / inline / suggestion."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CommentKind = Literal["issue", "review", "inline", "suggestion"]


class Comment(BaseModel):
    """A normalized PR comment.

    `kind` distinguishes:
      - issue: top-level PR conversation comment
      - review: review submission body
      - inline: review comment anchored to a code line
      - suggestion: GitHub suggested-change block within an inline comment
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    author: str
    body: str
    created_at: datetime
    kind: CommentKind = Field(description="Comment surface where it was posted")


__all__ = ["Comment", "CommentKind"]
