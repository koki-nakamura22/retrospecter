"""Unit tests for repo_retrospecter.services.classifier."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from anthropic import AuthenticationError as AnthropicAuthError

from repo_retrospecter.models.comment import Comment
from repo_retrospecter.models.knowledge import Knowledge
from repo_retrospecter.models.pull_request import PullRequest
from repo_retrospecter.services import classifier as classifier_mod
from repo_retrospecter.services.classifier import (
    API_KEY_ENV,
    DEFAULT_BATCH_SIZE,
    SYSTEM_PROMPT_HEADER,
    _build_client,
    _build_system_blocks,
    _build_user_message,
    _coerce_themes,
    _extract_text,
    _format_pr,
    _parse_response,
    _redact,
    _resolve_themes,
    _strip_fences,
    classify_pull_requests,
)
from repo_retrospecter.services.exceptions import AuthError, FetchError

# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pr(
    number: int = 1,
    *,
    title: str = "Refactor cache layer",
    body: str = "see ADR-0003",
    url: str | None = None,
    review_comments: list[Comment] | None = None,
    inline_comments: list[Comment] | None = None,
) -> PullRequest:
    return PullRequest(
        number=number,
        title=title,
        body=body,
        author="alice",
        merged_at=datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC),
        url=url or f"https://github.com/o/r/pull/{number}",
        review_comments=review_comments or [],
        inline_comments=inline_comments or [],
    )


def _make_comment(
    cid: str = "issue-1",
    *,
    author: str = "carol",
    body: str = "consider extracting this",
    kind: Any = "issue",
) -> Comment:
    return Comment(
        id=cid,
        author=author,
        body=body,
        created_at=datetime(2026, 5, 3, 13, 0, 0, tzinfo=UTC),
        kind=kind,
    )


def _make_text_block(text: str) -> Any:
    """Build a stand-in for anthropic.types.TextBlock with .type/.text."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_message(text: str) -> Any:
    """Build a stand-in Message whose content[0] is a TextBlock-like."""
    msg = MagicMock()
    msg.content = [_make_text_block(text)]
    return msg


def _make_client(text_or_texts: str | list[str]) -> Any:
    """Build a mock Anthropic client returning canned message text(s)."""
    client = MagicMock()
    if isinstance(text_or_texts, str):
        client.messages.create.return_value = _make_message(text_or_texts)
    else:
        client.messages.create.side_effect = [_make_message(t) for t in text_or_texts]
    return client


_UNSET: Any = object()


def _knowledge_payload(
    rule: str = "Prefer dependency injection for clients",
    anti: str = "Importing global singletons inside functions",
    example: str = "def f(client: Anthropic): ...",
    urls: Any = _UNSET,
    themes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "anti_pattern": anti,
        "example": example,
        "source_urls": ["https://github.com/o/r/pull/1"] if urls is _UNSET else urls,
        "themes": themes or ["design_decision"],
    }


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------


class TestRedact:
    def test_masks_anthropic_key_shape(self) -> None:
        assert _redact("token=sk-ant-abc123XYZ_def") == "token=***"

    def test_masks_github_personal_token(self) -> None:
        assert "ghp_" not in _redact("Bearer ghp_" + "A" * 36)

    def test_masks_github_pat(self) -> None:
        assert "github_pat_" not in _redact("github_pat_" + "A" * 30)

    def test_masks_active_env_key_even_if_unusual_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "WEIRD-KEY-VALUE-12345")
        assert "WEIRD-KEY-VALUE-12345" not in _redact("got: WEIRD-KEY-VALUE-12345 in header")

    def test_does_not_mask_short_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Very short values (< 8 chars) are too risky to mask globally — they
        # would over-redact unrelated text. The contract is "don't leak tokens",
        # not "redact everything".
        monkeypatch.setenv(API_KEY_ENV, "ab")
        assert "abolish" in _redact("the word abolish should survive")

    def test_passes_through_safe_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        assert _redact("just a regular log line") == "just a regular log line"

    def test_masks_multiple_occurrences(self) -> None:
        out = _redact("a=sk-ant-AAA111 and b=sk-ant-BBB222")
        assert "sk-ant-AAA111" not in out
        assert "sk-ant-BBB222" not in out
        assert out.count("***") == 2


# ---------------------------------------------------------------------------
# _build_client
# ---------------------------------------------------------------------------


class TestBuildClient:
    def test_missing_api_key_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        with pytest.raises(AuthError, match="ANTHROPIC_API_KEY is not set"):
            _build_client(timeout=10.0)

    def test_empty_api_key_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "")
        with pytest.raises(AuthError):
            _build_client(timeout=10.0)

    def test_passes_key_and_timeout_to_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-fakekey")
        with patch("repo_retrospecter.services.classifier.Anthropic") as ctor:
            ctor.return_value = MagicMock()
            _build_client(timeout=42.0)
            ctor.assert_called_once_with(api_key="sk-ant-fakekey", timeout=42.0)


# ---------------------------------------------------------------------------
# _resolve_themes
# ---------------------------------------------------------------------------


class TestResolveThemes:
    def test_none_returns_canonical_five(self) -> None:
        assert _resolve_themes(None) == [
            "design_decision",
            "review_rule",
            "bug_pattern",
            "refactor",
            "other",
        ]

    def test_overrides_with_user_list(self) -> None:
        assert _resolve_themes(["security", "perf"]) == ["security", "perf"]

    def test_strips_whitespace(self) -> None:
        assert _resolve_themes(["  security ", "\nperf\t"]) == ["security", "perf"]

    def test_drops_empty_strings(self) -> None:
        assert _resolve_themes(["security", "", "   "]) == ["security"]

    def test_empty_after_clean_falls_back_to_canonical(self) -> None:
        # If user passes only empty/whitespace, we don't ship an empty allow-list
        # to Claude — fall back to canonical so classification still works.
        assert _resolve_themes(["", "   "]) == [
            "design_decision",
            "review_rule",
            "bug_pattern",
            "refactor",
            "other",
        ]

    def test_empty_list_falls_back_to_canonical(self) -> None:
        assert len(_resolve_themes([])) == 5


# ---------------------------------------------------------------------------
# _build_system_blocks
# ---------------------------------------------------------------------------


class TestBuildSystemBlocks:
    def test_two_block_structure(self) -> None:
        blocks = _build_system_blocks(["a", "b"])
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "text"

    def test_first_block_is_header(self) -> None:
        blocks = _build_system_blocks(["a"])
        assert blocks[0]["text"] == SYSTEM_PROMPT_HEADER

    def test_second_block_has_ephemeral_cache_control(self) -> None:
        blocks = _build_system_blocks(["a"])
        cc = blocks[1].get("cache_control")
        assert cc == {"type": "ephemeral"}

    def test_first_block_has_no_cache_control(self) -> None:
        # Caching only the rules half keeps the header minimal — but the API
        # accepts caching on either; we deliberately pin it to the rules block.
        blocks = _build_system_blocks(["a"])
        assert blocks[0].get("cache_control") is None

    def test_themes_appear_in_cached_block(self) -> None:
        blocks = _build_system_blocks(["security", "perf"])
        assert "security" in blocks[1]["text"]
        assert "perf" in blocks[1]["text"]

    def test_distinct_theme_lists_produce_distinct_cached_text(self) -> None:
        a = _build_system_blocks(["x"])[1]["text"]
        b = _build_system_blocks(["y"])[1]["text"]
        assert a != b


# ---------------------------------------------------------------------------
# _format_pr / _build_user_message
# ---------------------------------------------------------------------------


class TestFormatPr:
    def test_includes_number_title_url_author(self) -> None:
        text = _format_pr(_make_pr(number=42, title="My change"))
        assert "PR #42" in text
        assert "My change" in text
        assert "https://github.com/o/r/pull/42" in text
        assert "alice" in text

    def test_omits_body_when_empty(self) -> None:
        text = _format_pr(_make_pr(body=""))
        assert "Body:" not in text

    def test_omits_body_when_whitespace_only(self) -> None:
        text = _format_pr(_make_pr(body="   \n\t  "))
        assert "Body:" not in text

    def test_omits_review_section_when_empty(self) -> None:
        text = _format_pr(_make_pr(review_comments=[]))
        assert "Review thread:" not in text

    def test_omits_inline_section_when_empty(self) -> None:
        text = _format_pr(_make_pr(inline_comments=[]))
        assert "Inline" not in text

    def test_renders_review_and_inline_sections(self) -> None:
        text = _format_pr(
            _make_pr(
                review_comments=[_make_comment(body="LGTM")],
                inline_comments=[_make_comment("inline-1", body="rename", kind="inline")],
            )
        )
        assert "Review thread:" in text
        assert "LGTM" in text
        assert "Inline" in text
        assert "rename" in text


class TestBuildUserMessage:
    def test_includes_allowed_themes_line(self) -> None:
        msg = _build_user_message([_make_pr()], ["security", "perf"])
        assert "Allowed themes: security, perf" in msg

    def test_concatenates_multiple_prs_with_separator(self) -> None:
        msg = _build_user_message(
            [_make_pr(number=1), _make_pr(number=2), _make_pr(number=3)],
            ["other"],
        )
        assert "PR #1" in msg
        assert "PR #2" in msg
        assert "PR #3" in msg
        # Separator between blocks
        assert "---" in msg

    def test_includes_json_contract_reminder(self) -> None:
        msg = _build_user_message([_make_pr()], ["other"])
        assert "knowledge" in msg
        assert "JSON" in msg


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


class TestStripFences:
    def test_strips_json_fence(self) -> None:
        assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_bare_fence(self) -> None:
        assert _strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_unfenced_passes_through(self) -> None:
        assert _strip_fences('{"a": 1}') == '{"a": 1}'

    def test_handles_surrounding_whitespace(self) -> None:
        assert _strip_fences('  \n```\n{"a": 1}\n```\n  ') == '{"a": 1}'

    def test_fence_without_closing_still_strips_open(self) -> None:
        # If the model forgets the closing fence we still peel the opening
        # fence so json.loads has a fighting chance.
        assert _strip_fences('```json\n{"a": 1}') == '{"a": 1}'


# ---------------------------------------------------------------------------
# _coerce_themes
# ---------------------------------------------------------------------------


class TestCoerceThemes:
    def test_keeps_known_tags(self) -> None:
        assert _coerce_themes(["design_decision"], {"design_decision", "other"}) == [
            "design_decision"
        ]

    def test_unknown_tag_collapses_to_other(self) -> None:
        assert _coerce_themes(["mystery"], {"design_decision", "other"}) == ["other"]

    def test_drops_non_string_entries(self) -> None:
        assert _coerce_themes(["design_decision", 1, None], {"design_decision", "other"}) == [
            "design_decision"
        ]

    def test_non_list_input_yields_other(self) -> None:
        assert _coerce_themes("design_decision", {"design_decision", "other"}) == ["other"]
        assert _coerce_themes(None, {"design_decision", "other"}) == ["other"]

    def test_dedupes_repeated_tags(self) -> None:
        result = _coerce_themes(
            ["design_decision", "design_decision", "other"],
            {"design_decision", "other"},
        )
        assert result == ["design_decision", "other"]

    def test_empty_after_filter_yields_other(self) -> None:
        assert _coerce_themes([], {"design_decision", "other"}) == ["other"]


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_concatenates_text_blocks(self) -> None:
        msg = MagicMock()
        msg.content = [_make_text_block("hello "), _make_text_block("world")]
        assert _extract_text(msg) == "hello world"

    def test_skips_non_text_blocks(self) -> None:
        msg = MagicMock()
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        msg.content = [_make_text_block("hi"), tool_block]
        assert _extract_text(msg) == "hi"

    def test_empty_content_yields_empty_string(self) -> None:
        msg = MagicMock()
        msg.content = []
        assert _extract_text(msg) == ""


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_returns_records_for_valid_payload(self) -> None:
        text = json.dumps({"knowledge": [_knowledge_payload()]})
        records = _parse_response(text, ["design_decision", "other"])
        assert len(records) == 1
        assert isinstance(records[0], Knowledge)
        assert records[0].source_urls == ["https://github.com/o/r/pull/1"]
        assert records[0].themes == ["design_decision"]

    def test_empty_text_yields_no_records(self) -> None:
        assert _parse_response("", ["other"]) == []

    def test_strips_fence_before_parsing(self) -> None:
        text = "```json\n" + json.dumps({"knowledge": [_knowledge_payload()]}) + "\n```"
        records = _parse_response(text, ["design_decision", "other"])
        assert len(records) == 1

    def test_invalid_json_raises_fetch_error(self) -> None:
        with pytest.raises(FetchError, match="not valid JSON"):
            _parse_response("{ not json", ["other"])

    def test_top_level_must_be_object(self) -> None:
        with pytest.raises(FetchError, match="top-level must be an object"):
            _parse_response("[]", ["other"])

    def test_knowledge_field_must_be_list(self) -> None:
        with pytest.raises(FetchError, match="'knowledge' field must be a list"):
            _parse_response(json.dumps({"knowledge": "oops"}), ["other"])

    def test_record_without_source_urls_is_dropped(self) -> None:
        record = _knowledge_payload(urls=[])
        text = json.dumps({"knowledge": [record]})
        assert _parse_response(text, ["design_decision", "other"]) == []

    def test_record_with_non_list_source_urls_is_dropped(self) -> None:
        record = _knowledge_payload()
        record["source_urls"] = "not a list"
        text = json.dumps({"knowledge": [record]})
        assert _parse_response(text, ["design_decision", "other"]) == []

    def test_record_with_only_non_string_urls_is_dropped(self) -> None:
        record = _knowledge_payload()
        record["source_urls"] = [42, None, ""]
        text = json.dumps({"knowledge": [record]})
        assert _parse_response(text, ["design_decision", "other"]) == []

    def test_empty_string_url_elements_are_filtered_out(self) -> None:
        # TC-F4-02 boundary: among a mix of valid URL + empty strings, only the
        # non-empty entry survives. Pins the `if isinstance(u, str) and u` guard
        # in _parse_response so a regression that lets "" through is caught.
        record = _knowledge_payload()
        record["source_urls"] = ["https://github.com/o/r/pull/1", "", "  "]
        text = json.dumps({"knowledge": [record]})
        records = _parse_response(text, ["design_decision", "other"])
        assert len(records) == 1
        assert "" not in records[0].source_urls
        assert "https://github.com/o/r/pull/1" in records[0].source_urls

    def test_non_dict_record_is_skipped(self) -> None:
        text = json.dumps({"knowledge": ["string-instead-of-dict", _knowledge_payload()]})
        assert len(_parse_response(text, ["design_decision", "other"])) == 1

    def test_unknown_theme_collapses_to_other(self) -> None:
        record = _knowledge_payload(themes=["mystery"])
        text = json.dumps({"knowledge": [record]})
        records = _parse_response(text, ["design_decision", "other"])
        assert records[0].themes == ["other"]

    def test_missing_optional_string_fields_default_to_empty(self) -> None:
        record = {
            "source_urls": ["https://github.com/o/r/pull/1"],
            # rule / anti_pattern / example / themes omitted
        }
        text = json.dumps({"knowledge": [record]})
        records = _parse_response(text, ["design_decision", "other"])
        assert records[0].rule == ""
        assert records[0].anti_pattern == ""
        assert records[0].example == ""
        assert records[0].themes == ["other"]

    def test_empty_knowledge_array_yields_empty(self) -> None:
        assert _parse_response(json.dumps({"knowledge": []}), ["other"]) == []

    def test_missing_knowledge_key_yields_empty(self) -> None:
        # A response that's a plain object but lacks the key is treated as
        # "no knowledge" rather than an error — the contract permits zero.
        assert _parse_response(json.dumps({"other": []}), ["other"]) == []


# ---------------------------------------------------------------------------
# classify_pull_requests — happy / batching / themes
# ---------------------------------------------------------------------------


class TestClassifyPullRequests:
    def test_empty_input_returns_empty_without_calling_api(self) -> None:
        client = MagicMock()
        result = classify_pull_requests([], client=client)
        assert result == []
        client.messages.create.assert_not_called()

    def test_invalid_batch_size_raises(self) -> None:
        with pytest.raises(ValueError, match="batch_size must be >= 1"):
            classify_pull_requests([_make_pr()], client=MagicMock(), batch_size=0)

    def test_returns_records_from_single_batch(self) -> None:
        text = json.dumps({"knowledge": [_knowledge_payload()]})
        client = _make_client(text)
        result = classify_pull_requests([_make_pr()], client=client)
        assert len(result) == 1
        assert isinstance(result[0], Knowledge)

    def test_default_batch_size_is_five(self) -> None:
        # Sanity-check the architecture.md §パフォーマンス constant. If someone
        # changes it we want the test to fail loudly.
        assert DEFAULT_BATCH_SIZE == 5

    def test_batches_at_size_five_by_default(self) -> None:
        # 11 PRs ⇒ 3 calls (5, 5, 1).
        prs = [_make_pr(number=i) for i in range(1, 12)]
        client = _make_client([json.dumps({"knowledge": []})] * 3)
        classify_pull_requests(prs, client=client)
        assert client.messages.create.call_count == 3

    def test_custom_batch_size_partitions(self) -> None:
        prs = [_make_pr(number=i) for i in range(1, 8)]  # 7 PRs
        client = _make_client([json.dumps({"knowledge": []})] * 4)
        classify_pull_requests(prs, client=client, batch_size=2)
        assert client.messages.create.call_count == 4

    def test_aggregates_records_across_batches(self) -> None:
        prs = [_make_pr(number=i) for i in range(1, 7)]  # 6 PRs ⇒ 2 batches
        texts = [
            json.dumps({"knowledge": [_knowledge_payload(rule="rule-A")]}),
            json.dumps(
                {
                    "knowledge": [
                        _knowledge_payload(rule="rule-B"),
                        _knowledge_payload(rule="rule-C"),
                    ]
                }
            ),
        ]
        client = _make_client(texts)
        result = classify_pull_requests(prs, client=client)
        rules = sorted(k.rule for k in result)
        assert rules == ["rule-A", "rule-B", "rule-C"]

    def test_passes_default_themes_when_none(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests([_make_pr()], client=client)
        kwargs = client.messages.create.call_args.kwargs
        system_blocks = kwargs["system"]
        cached = system_blocks[1]["text"]
        for canonical in ("design_decision", "review_rule", "bug_pattern", "refactor", "other"):
            assert canonical in cached

    def test_passes_custom_themes(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests(
            [_make_pr()], client=client, themes=["security", "performance", "other"]
        )
        kwargs = client.messages.create.call_args.kwargs
        cached = kwargs["system"][1]["text"]
        assert "security" in cached
        assert "performance" in cached

    def test_uses_provided_model_id(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests([_make_pr()], client=client, model="claude-test-model")
        assert client.messages.create.call_args.kwargs["model"] == "claude-test-model"

    def test_includes_cache_control_on_rules_block(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests([_make_pr()], client=client)
        system_blocks = client.messages.create.call_args.kwargs["system"]
        assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_user_message_contains_pr_url(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests(
            [_make_pr(number=99, url="https://github.com/o/r/pull/99")], client=client
        )
        user_msgs = client.messages.create.call_args.kwargs["messages"]
        content = user_msgs[0]["content"]
        assert "https://github.com/o/r/pull/99" in content

    def test_skips_records_without_source_urls(self) -> None:
        record_no_url = _knowledge_payload(urls=[])
        record_ok = _knowledge_payload(urls=["https://github.com/o/r/pull/2"])
        text = json.dumps({"knowledge": [record_no_url, record_ok]})
        client = _make_client(text)
        result = classify_pull_requests([_make_pr()], client=client)
        assert len(result) == 1
        assert result[0].source_urls == ["https://github.com/o/r/pull/2"]

    def test_batch_size_one_calls_api_per_pr(self) -> None:
        # Boundary value (smallest valid batch_size). With batch_size=1, the
        # range(0, 5, 1) loop produces 5 batches of 1 PR each.
        prs = [_make_pr(number=i) for i in range(1, 6)]
        client = _make_client([json.dumps({"knowledge": []})] * 5)
        classify_pull_requests(prs, client=client, batch_size=1)
        assert client.messages.create.call_count == 5

    def test_batch_size_larger_than_input_uses_single_call(self) -> None:
        # Boundary value (batch_size > len). 3 PRs fit in one batch even when
        # the configured size is 10 — range(0, 3, 10) yields a single start=0.
        prs = [_make_pr(number=i) for i in range(1, 4)]
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests(prs, client=client, batch_size=10)
        assert client.messages.create.call_count == 1
        # All three PRs must appear in that single call's user message.
        content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "PR #1" in content
        assert "PR #2" in content
        assert "PR #3" in content

    def test_max_tokens_forwarded_to_messages_create(self) -> None:
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests([_make_pr()], client=client, max_tokens=1234)
        assert client.messages.create.call_args.kwargs["max_tokens"] == 1234

    def test_timeout_forwarded_to_built_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # When no client is injected, the timeout argument must reach the
        # Anthropic constructor so per-call deadlines (decision-defaults.md
        # §タイムアウト) are honored.
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-fakekey")
        with patch("repo_retrospecter.services.classifier.Anthropic") as ctor:
            fake = MagicMock()
            fake.messages.create.return_value = _make_message(json.dumps({"knowledge": []}))
            ctor.return_value = fake
            classify_pull_requests([_make_pr()], timeout=33.5)
            assert ctor.call_args.kwargs["timeout"] == 33.5

    def test_system_prompt_does_not_leak_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Negative assertion (TC-SEC-01 / decision-defaults.md §ログ extended
        # to the request payload itself): even with the env key set, neither
        # system blocks nor the user message must echo the credential.
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-SECRETKEYVALUE")
        client = _make_client(json.dumps({"knowledge": []}))
        classify_pull_requests([_make_pr()], client=client)
        kwargs = client.messages.create.call_args.kwargs
        for block in kwargs["system"]:
            assert "sk-ant-SECRETKEYVALUE" not in block["text"]
        for message in kwargs["messages"]:
            assert "sk-ant-SECRETKEYVALUE" not in message["content"]


# ---------------------------------------------------------------------------
# classify_pull_requests — error normalization
# ---------------------------------------------------------------------------


class TestClassifyErrorNormalization:
    def test_anthropic_auth_error_becomes_auth_error(self) -> None:
        client = MagicMock()
        # AnthropicAuthError requires response/body kwargs in newer SDK; we
        # construct a real instance to mirror what the SDK would raise.
        response = MagicMock(status_code=401)
        client.messages.create.side_effect = AnthropicAuthError(
            message="bad key", response=response, body=None
        )
        with pytest.raises(AuthError):
            classify_pull_requests([_make_pr()], client=client)

    def test_generic_sdk_error_becomes_fetch_error(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("connection reset")
        with pytest.raises(FetchError, match="Anthropic call failed"):
            classify_pull_requests([_make_pr()], client=client)

    def test_fetch_error_message_mentions_pr_numbers(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("boom")
        with pytest.raises(FetchError) as exc:
            classify_pull_requests([_make_pr(number=42), _make_pr(number=43)], client=client)
        assert "42" in str(exc.value)
        assert "43" in str(exc.value)

    def test_fetch_error_message_redacts_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-LEAKEDKEYVALUE")
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("auth: sk-ant-LEAKEDKEYVALUE rejected")
        with pytest.raises(FetchError) as exc:
            classify_pull_requests([_make_pr()], client=client)
        assert "sk-ant-LEAKEDKEYVALUE" not in str(exc.value)

    def test_malformed_response_logs_warning_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Two batches: batch 1 returns garbage, batch 2 returns a valid record.
        prs = [_make_pr(number=i) for i in range(1, 11)]  # 10 PRs ⇒ 2 batches
        client = _make_client(["{ not json", json.dumps({"knowledge": [_knowledge_payload()]})])
        with caplog.at_level(logging.WARNING, logger=classifier_mod.logger.name):
            result = classify_pull_requests(prs, client=client)
        assert len(result) == 1
        assert any("skipping batch" in r.message for r in caplog.records)

    def test_warning_log_redacts_api_key(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-WARNKEYVALUE")
        # Returning malformed JSON whose error message would echo the key.
        prs = [_make_pr()]
        client = _make_client(["sk-ant-WARNKEYVALUE not-json"])
        with caplog.at_level(logging.WARNING, logger=classifier_mod.logger.name):
            classify_pull_requests(prs, client=client)
        for record in caplog.records:
            assert "sk-ant-WARNKEYVALUE" not in record.getMessage()


# ---------------------------------------------------------------------------
# classify_pull_requests — auth & client wiring
# ---------------------------------------------------------------------------


class TestClassifyClientWiring:
    def test_missing_api_key_raises_auth_error_when_no_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        with pytest.raises(AuthError, match="ANTHROPIC_API_KEY"):
            classify_pull_requests([_make_pr()])

    def test_uses_injected_client_without_touching_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with no env key, an injected client must work — this is the
        # seam tests rely on.
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        client = _make_client(json.dumps({"knowledge": []}))
        # Should not raise.
        classify_pull_requests([_make_pr()], client=client)
        assert client.messages.create.called

    def test_builds_client_when_api_key_set_and_no_client_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(API_KEY_ENV, "sk-ant-realish")
        with patch("repo_retrospecter.services.classifier.Anthropic") as ctor:
            fake = MagicMock()
            fake.messages.create.return_value = _make_message(json.dumps({"knowledge": []}))
            ctor.return_value = fake
            classify_pull_requests([_make_pr()], timeout=7.0)
            ctor.assert_called_once()
            assert ctor.call_args.kwargs["api_key"] == "sk-ant-realish"
            assert ctor.call_args.kwargs["timeout"] == 7.0


# ---------------------------------------------------------------------------
# environment hygiene
# ---------------------------------------------------------------------------


def test_env_key_is_isolated_per_test() -> None:
    """Sanity check that monkeypatch resets between tests."""
    # If a prior test leaked an env var, this assertion document the
    # expected starting state on most CI environments.
    assert os.environ.get(API_KEY_ENV) in (None, "") or os.environ[API_KEY_ENV] != "leaked"
