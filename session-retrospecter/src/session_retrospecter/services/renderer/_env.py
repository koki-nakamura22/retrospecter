"""共有 Jinja2 Environment (human / ai テンプレート共用)."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader

__all__: list[str] = ["env"]

env: Environment = Environment(
    loader=PackageLoader("session_retrospecter.services.renderer", "templates"),
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

def _surround_backtick(s: str) -> str:
    return f"`{s}`"


_filters: dict[str, Any] = env.filters  # type: ignore[assignment]
_filters["surround_backtick"] = _surround_backtick
