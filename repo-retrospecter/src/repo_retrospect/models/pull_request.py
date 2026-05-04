"""PullRequest model: merged PR with its associated comments."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from repo_retrospect.models.comment import Comment


class PullRequest(BaseModel):
    """A merged pull request normalized from `gh` CLI output.

    `body` may be empty when the PR description is blank; downstream
    renderers should skip empty fields rather than print blanks
    (decision-defaults.md §null/欠損値).
    """

    model_config = ConfigDict(extra="forbid")

    number: int
    title: str
    body: str
    author: str
    merged_at: datetime
    url: str
    review_comments: list[Comment] = Field(default_factory=list[Comment])
    inline_comments: list[Comment] = Field(default_factory=list[Comment])


__all__ = ["PullRequest"]
