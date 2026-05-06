"""services.renderer — Cache → Markdown 変換 (human / ai 2 系統)."""

from __future__ import annotations

from typing import Protocol

from session_retrospecter.models.cache import Cache

from . import ai, human

__all__ = ["Renderer", "ai", "human"]


class Renderer(Protocol):
    """renderer モジュールの公開 API 型."""

    def render(self, cache: Cache) -> str: ...
