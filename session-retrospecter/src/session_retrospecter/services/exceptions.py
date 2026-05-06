"""session-retrospecter — Service-layer exceptions.

CLI 層が ClickException に翻訳する。各例外は単一の人間可読メッセージを保持する。
"""

from __future__ import annotations

__all__ = ["FetchError", "SessionParseError", "RateLimitError", "ClassifierError"]


class FetchError(Exception):
    """全 service 層例外の親。CLI で ClickException へ翻訳される。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SessionParseError(FetchError):
    """JSONL 1 行の JSON parse 失敗 / 必須 field 欠損。"""


class RateLimitError(FetchError):
    """Anthropic API 429 応答。retry 後も解消しない場合に raise。"""


class ClassifierError(FetchError):
    """Anthropic API 呼出しの 429 以外の失敗 (timeout / 5xx 等)。"""
