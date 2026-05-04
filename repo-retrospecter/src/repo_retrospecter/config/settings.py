"""Optional configuration file loader (architecture.md §config/settings.py).

Users may pass ``--config repo-retrospecter.config.json`` (or ``.toml``) to
hold defaults for ``--repo``, ``--last/--since``, output paths, and the
classifier's theme axes (PRD F2 / OQ-02). CLI options always override
file values; absent file values fall back to ``None`` so the CLI layer
can apply its own defaults.

Per decision-defaults.md §バージョン管理 / 依存: TOML parsing uses the
stdlib ``tomllib`` (Python 3.11+) — no third-party dependency.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """File-backed defaults for the CLI.

    Every field is optional: a config file may set just ``themes`` (the
    most common override per PRD F2) and leave the rest to the CLI.
    """

    model_config = ConfigDict(extra="forbid")

    repo: str | None = None
    last: int | None = None
    last_commits: int | None = None
    since: date | None = None
    out: Path | None = None
    ai_out: Path | None = None
    cache: Path | None = None
    themes: list[str] | None = Field(default=None, min_length=1)


def load_settings(path: Path) -> Settings:
    """Parse ``path`` as JSON or TOML (decided by file extension).

    A missing file raises :class:`FileNotFoundError`; malformed content
    raises :class:`ValueError`. Validation errors from Pydantic propagate
    so the CLI layer can wrap them into ``click.ClickException``.
    """
    suffix = path.suffix.lower()
    data: dict[str, Any]
    if suffix == ".toml":
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    elif suffix == ".json":
        text = path.read_text(encoding="utf-8")
        parsed: object = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"config file {path} must contain a JSON object at top level"
            )
        data = cast(dict[str, Any], parsed)
    else:
        raise ValueError(
            f"unsupported config extension {suffix!r}: use .json or .toml"
        )
    return Settings.model_validate(data)


__all__ = ["Settings", "load_settings"]
