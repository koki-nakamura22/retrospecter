"""TC-CL-02〜07: services.classifier の受け入れテスト.

ANTHROPIC_API_KEY が未設定の場合はモジュール全体をスキップ.
正常系 (TC-CL-02, TC-CL-04) は VCR cassette replay で検証する.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from anthropic.types import TextBlock

from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import Knowledge
from session_retrospecter.services.classifier import DEFAULT_SYSTEM_PROMPT, classify
from session_retrospecter.services.exceptions import ClassifierError, RateLimitError

# ---------------------------------------------------------------------------
# module-level skip guard
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY を環境変数か .env に設定してください",
)

# ---------------------------------------------------------------------------
# VCR cassette directory
# ---------------------------------------------------------------------------

_CASSETTE_DIR = str(Path(__file__).parent / "fixtures" / "cassettes")


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return _CASSETTE_DIR


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    return {
        "record_mode": "none",
        "match_on": ["method", "uri"],
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    session_id: str = "test-session",
    line_no: int = 5,
    kind: str = "correction",
    context: str = "User: stop\nAssistant: understood.",
) -> ExtractionCandidate:
    return ExtractionCandidate(
        kind=kind,  # type: ignore[arg-type]
        session_id=session_id,
        line_no=line_no,
        context=context,
        citation=f"session://{session_id}#L{line_no}",
    )


def _make_rate_limit_error() -> anthropic.RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code=429,
        content=b'{"type":"error","error":{"type":"rate_limit_error","message":"rate limited"}}',
        request=request,
    )
    return anthropic.RateLimitError(
        message="Rate limited",
        response=response,
        body={"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}},
    )


def _make_server_error() -> anthropic.InternalServerError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code=500,
        content=b'{"type":"error","error":{"type":"api_error","message":"internal error"}}',
        request=request,
    )
    return anthropic.InternalServerError(
        message="Internal server error",
        response=response,
        body={"type": "error", "error": {"type": "api_error", "message": "internal error"}},
    )


# ---------------------------------------------------------------------------
# TC-CL-02: VCR cassette replay — single candidate → 1 Knowledge
# ---------------------------------------------------------------------------


@pytest.mark.vcr(cassette_name="classify_single.yaml")
def test_classify_single_candidate_returns_knowledge() -> None:
    """TC-CL-02: correction candidate を分類すると Knowledge が 1 件返る."""
    # Arrange
    candidate = _make_candidate(session_id="test-session", line_no=5, kind="correction")

    # Act
    results = classify([candidate])

    # Assert
    assert len(results) == 1
    k = results[0]
    assert isinstance(k, Knowledge)
    assert "session://test-session#L5" in k.sources
    assert k.rule
    assert k.anti_pattern
    assert k.example


# ---------------------------------------------------------------------------
# TC-CL-03: cached_citations skip — no LLM call
# ---------------------------------------------------------------------------


def test_classify_cached_citations_skipped() -> None:
    """TC-CL-03: citation が cached_citations に含まれる candidate は LLM call をスキップ."""
    # Arrange
    candidate = _make_candidate(session_id="test-session", line_no=10)
    cached = {candidate.citation}

    # Act
    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        results = classify([candidate], cached_citations=cached)

    # Assert
    assert results == []
    mock_cls.assert_not_called()


def test_classify_empty_candidates_returns_empty() -> None:
    """候補リストが空の場合は LLM call なしで空リストを返す."""
    # Arrange (empty candidate list)

    # Act
    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        results = classify([])

    # Assert
    assert results == []
    mock_cls.assert_not_called()


def test_classify_partial_cache_only_uncached_sent() -> None:
    """一部 cached の場合は未 cache 分だけ LLM call される (クライアントが生成される)."""
    # Arrange
    cached_c = _make_candidate(session_id="s1", line_no=1)
    new_c = _make_candidate(session_id="s1", line_no=2)
    cached_set = {cached_c.citation}

    mock_response = MagicMock()
    mock_response.content = [MagicMock(spec=TextBlock)]
    mock_response.content[0].text = (
        '[{"rule":"r","anti_pattern":"a","example":"e",'
        '"sources":["session://s1#L2"],"themes":["correction"]}]'
    )

    # Act
    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = mock_response

        results = classify([cached_c, new_c], cached_citations=cached_set)

    # Assert
    mock_cls.assert_called_once()
    assert mock_client.messages.create.call_count == 1
    assert len(results) == 1
    assert results[0].sources == ["session://s1#L2"]


# ---------------------------------------------------------------------------
# TC-CL-04: VCR cassette replay — multiple candidates → 2 Knowledge
# ---------------------------------------------------------------------------


@pytest.mark.vcr(cassette_name="classify_multi.yaml")
def test_classify_multiple_candidates_returns_all_knowledge() -> None:
    """TC-CL-04: 複数 candidate を分類すると複数 Knowledge が返る."""
    # Arrange
    c1 = _make_candidate(session_id="test-session", line_no=10, kind="validated_pattern")
    c2 = _make_candidate(session_id="test-session", line_no=20, kind="decision_rationale")

    # Act
    results = classify([c1, c2])

    # Assert
    assert len(results) == 2
    citations = {s for k in results for s in k.sources}
    assert "session://test-session#L10" in citations
    assert "session://test-session#L20" in citations
    for k in results:
        assert isinstance(k, Knowledge)
        assert k.rule
        assert k.anti_pattern
        assert k.example


# ---------------------------------------------------------------------------
# TC-CL-05: 429 → RateLimitError
# ---------------------------------------------------------------------------


def test_classify_429_raises_rate_limit_error() -> None:
    """TC-CL-05: Anthropic API が 429 を返すと RateLimitError が raise される."""
    # Arrange
    candidate = _make_candidate()
    rate_limit_exc = _make_rate_limit_error()

    # Act / Assert
    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = rate_limit_exc

        with pytest.raises(RateLimitError):
            classify([candidate])


def test_classify_429_on_retry_raises_rate_limit_error() -> None:
    """1 回目は通常 API エラー、retry で 429 → RateLimitError が上がる."""
    # Arrange
    candidate = _make_candidate()
    server_err = _make_server_error()
    rate_limit_exc = _make_rate_limit_error()

    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = [server_err, rate_limit_exc]

        # Act / Assert
        with pytest.raises(RateLimitError):
            classify([candidate])


# ---------------------------------------------------------------------------
# TC-CL-06: API error + retry → WARN log + return []
# ---------------------------------------------------------------------------


def test_classify_api_error_twice_returns_empty_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TC-CL-06: API 失敗が 2 回続くと WARN ログを出力して空リストを返す."""
    # Arrange
    candidate = _make_candidate()
    server_err = _make_server_error()

    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = [server_err, _make_server_error()]

        # Act
        with caplog.at_level(logging.WARNING):
            results = classify([candidate])

    # Assert
    assert results == []
    assert mock_client.messages.create.call_count == 2
    # At least one WARN message about the failure
    assert any("failed" in r.message.lower() for r in caplog.records)


def test_classify_api_error_then_success_returns_knowledge() -> None:
    """1 回目 API エラー → retry で成功した場合は Knowledge が返る."""
    # Arrange
    candidate = _make_candidate()
    server_err = _make_server_error()

    mock_response = MagicMock()
    mock_response.content = [MagicMock(spec=TextBlock)]
    mock_response.content[0].text = (
        '[{"rule":"retry rule","anti_pattern":"anti","example":"ex",'
        '"sources":["session://test-session#L5"],"themes":["correction"]}]'
    )

    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = [server_err, mock_response]

        # Act
        results = classify([candidate])

    # Assert
    assert len(results) == 1
    assert results[0].rule == "retry rule"
    assert results[0].sources == ["session://test-session#L5"]


# ---------------------------------------------------------------------------
# TC-CL-07: system prompt に cache_control が付いている
# ---------------------------------------------------------------------------


def test_classify_system_prompt_has_cache_control() -> None:
    """AC3: messages.create に渡す system に cache_control ephemeral が設定される."""
    # Arrange
    candidate = _make_candidate()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(spec=TextBlock)]
    mock_response.content[0].text = (
        '[{"rule":"r","anti_pattern":"a","example":"e",'
        '"sources":["session://test-session#L5"],"themes":["correction"]}]'
    )

    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = mock_response

        # Act
        classify([candidate])

    # Assert: system has cache_control
    create_kwargs = mock_client.messages.create.call_args
    system_blocks = create_kwargs.kwargs["system"]
    assert isinstance(system_blocks, list)
    assert len(system_blocks) >= 1
    first_block = system_blocks[0]
    assert first_block["type"] == "text"
    assert first_block["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# TC-CL-08: DEFAULT_SYSTEM_PROMPT は session 文脈専用
# ---------------------------------------------------------------------------


def test_classify_no_api_key_raises_classifier_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTHROPIC_API_KEY 未設定時は ClassifierError が raise される."""
    # Arrange
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    candidate = _make_candidate()

    # Act / Assert
    with pytest.raises(ClassifierError, match="ANTHROPIC_API_KEY"):
        classify([candidate])


def test_classify_llm_invalid_json_raises_classifier_error() -> None:
    """LLM が JSON 配列でないレスポンスを返した場合は ClassifierError が raise される."""
    # Arrange
    candidate = _make_candidate()

    mock_response = MagicMock()
    mock_response.content = [MagicMock(spec=TextBlock)]
    mock_response.content[0].text = "I cannot process this request."

    with patch("session_retrospecter.services.classifier.anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = mock_response

        # Act / Assert
        with pytest.raises(ClassifierError):
            classify([candidate])


# ---------------------------------------------------------------------------
# TC-CL-08: DEFAULT_SYSTEM_PROMPT は session 文脈専用
# ---------------------------------------------------------------------------


def test_default_system_prompt_contains_session_context() -> None:
    """DEFAULT_SYSTEM_PROMPT は session 文脈専用の内容を含む."""
    # Assert: PR レビュー用語句が含まれない
    assert "session" in DEFAULT_SYSTEM_PROMPT.lower()
    assert "pull request" not in DEFAULT_SYSTEM_PROMPT.lower()
    assert "pr review" not in DEFAULT_SYSTEM_PROMPT.lower()
    # session-retrospecter のシグナル種別が含まれる
    assert "correction" in DEFAULT_SYSTEM_PROMPT
    assert "validated_pattern" in DEFAULT_SYSTEM_PROMPT
    assert "sources" in DEFAULT_SYSTEM_PROMPT
