"""Renderer plugins for repo-retrospecter (ADR-0004).

The :func:`get_renderer` factory keeps the CLI's ``--format`` option
declarative: today it dispatches to ``human`` and ``ai``; tomorrow a
``skill`` plugin (Post-MVP F6) drops in here without touching callers.
"""

from __future__ import annotations

from typing import Literal

from repo_retrospecter.services.renderer.ai import AiRenderer
from repo_retrospecter.services.renderer.base import Renderer
from repo_retrospecter.services.renderer.human import HumanRenderer

RendererName = Literal["human", "ai"]


def get_renderer(name: RendererName) -> Renderer:
    """Return the concrete renderer registered under ``name``.

    Raises ``ValueError`` when ``name`` is not a known plugin so the
    CLI layer can surface a typed error.
    """
    if name == "human":
        return HumanRenderer()
    if name == "ai":
        return AiRenderer()
    # Reachable only if a caller bypasses the Literal hint; surface a
    # clear error rather than returning None.
    raise ValueError(f"unknown renderer: {name!r}")


__all__ = [
    "AiRenderer",
    "HumanRenderer",
    "Renderer",
    "RendererName",
    "get_renderer",
]
