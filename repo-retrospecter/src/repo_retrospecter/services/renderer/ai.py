"""AI-facing structured-knowledge renderer (PRD F4 / TC-F4-01 / TC-F4-02).

Emits Markdown that an AI coding agent (Claude Code skill, CLAUDE.md
include, etc.) can read as-is. Each item carries:

- ``### Rule: <one-liner>``
- ``**Anti-pattern**: ...`` when applicable
- a fenced code block when an example is present
- at least one ``https://github.com/`` source URL

Records lacking a GitHub URL are dropped to enforce TC-F4-02 (citation
required to deter hallucinated rules).
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from repo_retrospecter.models.cache import CacheFile
from repo_retrospecter.models.knowledge import Knowledge

GITHUB_URL_PREFIX: str = "https://github.com/"


def _has_github_source(k: Knowledge) -> bool:
    """TC-F4-02: at least one source URL must point at github.com."""
    return any(u.startswith(GITHUB_URL_PREFIX) for u in k.source_urls)


class AiRenderer:
    """Render a ``CacheFile`` as AI-consumable structured knowledge."""

    template_name: str = "ai.md.j2"

    def __init__(self) -> None:
        self._env = Environment(
            loader=PackageLoader("repo_retrospecter.services.renderer", "templates"),
            autoescape=select_autoescape(disabled_extensions=("md", "j2")),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def render(self, cache: CacheFile, out_path: Path) -> None:
        knowledge = [k for k in (cache.knowledge or []) if _has_github_source(k)]

        template = self._env.get_template(self.template_name)
        text = template.render(cache=cache, knowledge=knowledge)
        if not text.endswith("\n"):
            text += "\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8", newline="\n")


__all__ = ["GITHUB_URL_PREFIX", "AiRenderer"]
