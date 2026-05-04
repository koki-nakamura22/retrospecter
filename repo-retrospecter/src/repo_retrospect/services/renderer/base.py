"""Renderer plugin protocol (ADR-0004).

Concrete renderers (``human``, ``ai`` and any future plugin such as
``skill``) implement :class:`Renderer` to convert a :class:`CacheFile`
into a Markdown file at ``out_path``. Keeping the seam tiny — one
method, no return value — makes ``--format <name>`` plumbing trivial
and lets users drop in their own jinja2 templates later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from repo_retrospect.models.cache import CacheFile


@runtime_checkable
class Renderer(Protocol):
    """Render a ``CacheFile`` to a single Markdown file.

    Implementations MUST:

    - Create ``out_path``'s parent directory if missing
      (decision-defaults.md §I/O implies a "just write" contract).
    - Write UTF-8, LF line endings, ending with a trailing newline
      (decision-defaults.md §I/O).
    - Be idempotent: rendering the same cache twice yields the same
      bytes.
    """

    def render(self, cache: CacheFile, out_path: Path) -> None:
        """Write the rendered Markdown to ``out_path``."""
        ...


__all__ = ["Renderer"]
