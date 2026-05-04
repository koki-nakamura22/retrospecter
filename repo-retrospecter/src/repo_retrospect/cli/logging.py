"""CLI logging setup (decision-defaults.md §ログ).

- Output goes through ``rich`` for human-readable colored console output.
- A ``RedactFilter`` masks ``sk-ant-*``, ``gh[psoru]_*``, ``github_pat_*``
  and the live ``ANTHROPIC_API_KEY`` value before any record reaches the
  handler, so accidental echoes (error messages quoting headers, etc.)
  cannot leak credentials.
- Levels: ``--verbose`` → DEBUG, ``--quiet`` → WARN, default → INFO.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Final

from rich.logging import RichHandler

API_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"

_REDACT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"gh[psoru]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)
_REDACT_PLACEHOLDER: Final[str] = "***"


def redact(text: str) -> str:
    """Mask known credential shapes in ``text``."""
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub(_REDACT_PLACEHOLDER, out)
    key = os.environ.get(API_KEY_ENV)
    if key and len(key) >= 8 and key in out:
        out = out.replace(key, _REDACT_PLACEHOLDER)
    return out


class RedactFilter(logging.Filter):
    """Mutate ``LogRecord.msg`` and ``args`` to strip credentials."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True


def configure_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    """Install a single ``RichHandler`` on the root logger with redact.

    Idempotent: existing handlers added by previous calls (or by
    ``logging.basicConfig``) are removed first so repeated CLI invocations
    in the same process don't accumulate output.
    """
    if verbose and quiet:
        raise ValueError("--verbose and --quiet are mutually exclusive")
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = RichHandler(
        rich_tracebacks=True,
        show_path=verbose,
        show_time=False,
        markup=False,
    )
    handler.setLevel(level)
    handler.addFilter(RedactFilter())

    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(handler)


__all__ = [
    "API_KEY_ENV",
    "RedactFilter",
    "configure_logging",
    "redact",
]
