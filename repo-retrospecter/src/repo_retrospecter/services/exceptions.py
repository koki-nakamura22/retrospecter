"""Exceptions raised by the services layer.

Per docs/decision-defaults.md §エラー処理, expected errors get dedicated
exception classes that the CLI layer wraps into ``click.ClickException``.
"""

from __future__ import annotations


class FetchError(Exception):
    """Generic failure during PR/comment fetching via ``gh`` CLI."""


class AuthError(FetchError):
    """``gh`` is not authenticated. Resolve with ``gh auth login``."""


class RateLimitError(FetchError):
    """GitHub API rate limit hit.

    The exception message preserves the wait hint emitted by ``gh`` so the
    CLI layer can surface it verbatim (decision-defaults.md §リトライ:
    GitHub レート制限はリトライせずエラー終了 + 待機時間案内).
    """


__all__ = ["AuthError", "FetchError", "RateLimitError"]
