"""services.renderer.ai — Cache を AI agent 向け Knowledge Markdown に変換する."""

from __future__ import annotations

from session_retrospecter.models.cache import Cache

from ._env import env

__all__ = ["render"]


def render(cache: Cache) -> str:
    """Cache を AI agent 向け Markdown に変換して返す.

    Knowledge.sources が空のものが含まれる場合は ValueError を送出する (T-06).
    """
    knowledge = cache.knowledge or []

    for k in knowledge:
        if not k.sources:
            raise ValueError(f"Knowledge.sources が空: rule={k.rule!r}")

    tmpl = env.get_template("ai.md.j2")
    return tmpl.render(knowledge=knowledge)
