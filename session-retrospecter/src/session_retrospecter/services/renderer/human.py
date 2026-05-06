"""services.renderer.human — Cache を人間向け Markdown に変換する."""

from __future__ import annotations

from session_retrospecter.models.cache import Cache
from session_retrospecter.models.knowledge import CANONICAL_THEMES, Knowledge

from ._env import env

__all__ = ["render"]


def render(cache: Cache) -> str:
    """Cache を人間向け Markdown に変換して返す.

    Knowledge.sources が空のものが含まれる場合は ValueError を送出する (T-06).
    """
    knowledge = cache.knowledge or []

    for k in knowledge:
        if not k.sources:
            raise ValueError(f"Knowledge.sources が空: rule={k.rule!r}")

    by_theme: dict[str, list[Knowledge]] = {theme: [] for theme in CANONICAL_THEMES}
    for k in knowledge:
        for theme in k.themes:
            if theme in by_theme:
                by_theme[theme].append(k)

    tmpl = env.get_template("human.md.j2")
    return tmpl.render(knowledge_by_theme=by_theme)
