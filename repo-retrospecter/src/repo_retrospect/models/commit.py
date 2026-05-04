"""Commit model: a commit on the default branch that is not associated
with any merged PR collected for the same run (architecture.md §services
/fetcher / docs/product-requirements.md F1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Commit(BaseModel):
    """A loose (PR-less) commit on the repository's default branch.

    Only the GitHub login is retained for ``author`` to satisfy
    architecture.md §セキュリティアーキテクチャ (PII strip).
    """

    model_config = ConfigDict(extra="forbid")

    sha: str
    message: str
    author: str
    committed_at: datetime
    url: str


__all__ = ["Commit"]
