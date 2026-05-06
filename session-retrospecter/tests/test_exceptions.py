"""service 層例外ヒエラルキーの継承関係テスト.

TC-EX-01〜03 は brief T003 AC4 に紐づく.
各サブクラスを raise して FetchError として catch できることを確認する.
"""

from __future__ import annotations

import pytest

from session_retrospecter.services.exceptions import (
    ClassifierError,
    FetchError,
    RateLimitError,
    SessionParseError,
)

# ---------------------------------------------------------------------------
# TC-EX-01: SessionParseError は FetchError のサブクラス
# ---------------------------------------------------------------------------


def test_session_parse_error_is_caught_as_fetch_error() -> None:
    with pytest.raises(FetchError):
        raise SessionParseError("invalid JSON on line 3")


# ---------------------------------------------------------------------------
# TC-EX-02: RateLimitError は FetchError のサブクラス
# ---------------------------------------------------------------------------


def test_rate_limit_error_is_caught_as_fetch_error() -> None:
    with pytest.raises(FetchError):
        raise RateLimitError("Anthropic 429: Too Many Requests")


# ---------------------------------------------------------------------------
# TC-EX-03: ClassifierError は FetchError のサブクラス
# ---------------------------------------------------------------------------


def test_classifier_error_is_caught_as_fetch_error() -> None:
    with pytest.raises(FetchError):
        raise ClassifierError("Anthropic API 5xx: internal server error")


# ---------------------------------------------------------------------------
# 補足: message 保持 (AC2) と __str__ の確認
# ---------------------------------------------------------------------------


def test_fetch_error_preserves_message() -> None:
    msg = "something went wrong"

    exc = FetchError(msg)

    assert str(exc) == msg


def test_session_parse_error_preserves_message() -> None:
    msg = "missing field: session_id"

    exc = SessionParseError(msg)

    assert str(exc) == msg
