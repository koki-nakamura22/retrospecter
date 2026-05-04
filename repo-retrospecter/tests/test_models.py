"""Unit tests for repo_retrospecter.models (Pydantic v2 domain models)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from repo_retrospecter.models import (
    CACHE_SCHEMA_VERSION,
    CANONICAL_THEMES,
    CacheFile,
    Comment,
    Knowledge,
    PullRequest,
)


def _make_comment(kind: str = "issue", suffix: str = "1") -> Comment:
    return Comment(
        id=f"c-{suffix}",
        author="alice",
        body="LGTM",
        created_at=datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC),
        kind=kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Comment
# ---------------------------------------------------------------------------


class TestComment:
    def test_accepts_all_four_kinds(self) -> None:
        for kind in ("issue", "review", "inline", "suggestion"):
            c = _make_comment(kind=kind, suffix=kind)
            assert c.kind == kind

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError):
            Comment(
                id="x",
                author="a",
                body="b",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                kind="bogus",  # type: ignore[arg-type]
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Comment.model_validate(
                {
                    "id": "x",
                    "author": "a",
                    "body": "b",
                    "created_at": "2026-01-01T00:00:00Z",
                    "kind": "issue",
                    "rogue": True,
                }
            )

    def test_round_trip_via_json(self) -> None:
        original = _make_comment()
        as_json = original.model_dump_json()
        restored = Comment.model_validate_json(as_json)
        assert restored == original
        assert json.loads(as_json)["kind"] == "issue"

    def test_is_frozen(self) -> None:
        c = _make_comment()
        with pytest.raises(ValidationError):
            c.body = "tampered"  # type: ignore[misc]

    def test_rejects_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            Comment.model_validate(
                {
                    "author": "a",
                    "body": "b",
                    "created_at": "2026-01-01T00:00:00Z",
                    "kind": "issue",
                }
            )

    def test_rejects_non_datetime_created_at(self) -> None:
        with pytest.raises(ValidationError):
            Comment.model_validate(
                {
                    "id": "x",
                    "author": "a",
                    "body": "b",
                    "created_at": "not-a-timestamp",
                    "kind": "issue",
                }
            )


# ---------------------------------------------------------------------------
# PullRequest
# ---------------------------------------------------------------------------


class TestPullRequest:
    def test_minimal_pr_has_empty_comment_lists(self) -> None:
        pr = PullRequest(
            number=42,
            title="Add feature",
            body="",
            author="alice",
            merged_at=datetime(2026, 5, 4, tzinfo=UTC),
            url="https://github.com/o/r/pull/42",
        )
        assert pr.review_comments == []
        assert pr.inline_comments == []

    def test_carries_comments_through_round_trip(self) -> None:
        pr = PullRequest(
            number=1,
            title="t",
            body="b",
            author="a",
            merged_at=datetime(2026, 1, 1, tzinfo=UTC),
            url="https://example.test/pr/1",
            review_comments=[_make_comment("review", "r1")],
            inline_comments=[_make_comment("inline", "i1")],
        )
        restored = PullRequest.model_validate_json(pr.model_dump_json())
        assert restored == pr
        assert restored.review_comments[0].kind == "review"
        assert restored.inline_comments[0].kind == "inline"

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            PullRequest.model_validate(
                {
                    "number": 1,
                    "title": "t",
                    "body": "",
                    "author": "a",
                    "merged_at": "2026-01-01T00:00:00Z",
                    "url": "https://x/pr/1",
                    "draft": True,
                }
            )

    def test_rejects_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            PullRequest.model_validate(
                {
                    "number": 1,
                    "title": "t",
                    "body": "",
                    "author": "a",
                    "url": "https://x/pr/1",
                }
            )

    def test_default_comment_lists_are_per_instance(self) -> None:
        """Guards against the classic mutable-default trap: each instance
        must own its own list so appending to one PR doesn't leak into others.
        """
        pr_a = PullRequest(
            number=1,
            title="a",
            body="",
            author="x",
            merged_at=datetime(2026, 1, 1, tzinfo=UTC),
            url="https://x/pr/1",
        )
        pr_b = PullRequest(
            number=2,
            title="b",
            body="",
            author="x",
            merged_at=datetime(2026, 1, 2, tzinfo=UTC),
            url="https://x/pr/2",
        )
        pr_a.review_comments.append(_make_comment())
        assert pr_b.review_comments == []
        assert pr_a.review_comments is not pr_b.review_comments


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


class TestTheme:
    def test_canonical_themes_match_oq02(self) -> None:
        assert CANONICAL_THEMES == (
            "design_decision",
            "review_rule",
            "bug_pattern",
            "refactor",
            "other",
        )


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------


class TestKnowledge:
    def test_defaults_to_empty_lists(self) -> None:
        k = Knowledge(rule="r", anti_pattern="a", example="e")
        assert k.source_urls == []
        assert k.themes == []

    def test_accepts_canonical_and_custom_themes(self) -> None:
        k = Knowledge(
            rule="r",
            anti_pattern="a",
            example="e",
            source_urls=["https://example.test/pr/1"],
            themes=["design_decision", "team_specific_topic"],
        )
        assert "design_decision" in k.themes
        assert "team_specific_topic" in k.themes

    def test_round_trip_via_json(self) -> None:
        k = Knowledge(
            rule="Always type-annotate public APIs",
            anti_pattern="Untyped public function returning Any",
            example="def foo(x: int) -> str: ...",
            source_urls=["https://example.test/pr/9"],
            themes=["review_rule"],
        )
        assert Knowledge.model_validate_json(k.model_dump_json()) == k

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Knowledge.model_validate(
                {
                    "rule": "r",
                    "anti_pattern": "a",
                    "example": "e",
                    "confidence": 0.9,
                }
            )

    def test_rejects_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            Knowledge.model_validate({"rule": "r", "anti_pattern": "a"})


# ---------------------------------------------------------------------------
# CacheFile
# ---------------------------------------------------------------------------


class TestCacheFile:
    def test_default_schema_version_is_1(self) -> None:
        cache = CacheFile(
            generated_at=datetime(2026, 5, 4, tzinfo=UTC),
            repo="owner/repo",
        )
        assert cache.schema_version == CACHE_SCHEMA_VERSION == "1"
        assert cache.pull_requests == []
        assert cache.knowledge is None

    def test_full_round_trip_via_json(self) -> None:
        cache = CacheFile(
            generated_at=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
            repo="owner/repo",
            pull_requests=[
                PullRequest(
                    number=7,
                    title="Refactor cache layer",
                    body="see ADR-0003",
                    author="bob",
                    merged_at=datetime(2026, 5, 3, tzinfo=UTC),
                    url="https://github.com/owner/repo/pull/7",
                    review_comments=[_make_comment("review", "r7")],
                    inline_comments=[],
                )
            ],
            knowledge=[
                Knowledge(
                    rule="Use Pydantic v2 ConfigDict",
                    anti_pattern="Mutating frozen models",
                    example="model_config = ConfigDict(frozen=True)",
                    source_urls=["https://github.com/owner/repo/pull/7"],
                    themes=["design_decision"],
                )
            ],
        )
        as_json = cache.model_dump_json()
        restored = CacheFile.model_validate_json(as_json)
        assert restored == cache

    def test_dumped_json_is_pure_python_serializable(self) -> None:
        """Ensures every field round-trips through json.dumps/loads."""
        cache = CacheFile(
            generated_at=datetime(2026, 5, 4, tzinfo=UTC),
            repo="owner/repo",
            pull_requests=[],
            knowledge=None,
        )
        payload = cache.model_dump(mode="json")
        # If this round-trips, every field is JSON-serializable.
        reloaded = json.loads(json.dumps(payload))
        assert reloaded["schema_version"] == "1"
        assert reloaded["repo"] == "owner/repo"
        assert reloaded["knowledge"] is None

    def test_rejects_schema_version_typo_via_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            CacheFile.model_validate(
                {
                    "schema_versions": "1",  # typo, extra field
                    "generated_at": "2026-05-04T00:00:00Z",
                    "repo": "owner/repo",
                }
            )

    def test_explicit_schema_version_is_preserved(self) -> None:
        """Future cache-version checking (OQ-03) must see the value the file
        was written with, not the current default."""
        cache = CacheFile(
            schema_version="2",
            generated_at=datetime(2026, 5, 4, tzinfo=UTC),
            repo="owner/repo",
        )
        assert cache.schema_version == "2"
        restored = CacheFile.model_validate_json(cache.model_dump_json())
        assert restored.schema_version == "2"

    def test_default_pull_request_list_is_per_instance(self) -> None:
        a = CacheFile(generated_at=datetime(2026, 5, 4, tzinfo=UTC), repo="o/a")
        b = CacheFile(generated_at=datetime(2026, 5, 4, tzinfo=UTC), repo="o/b")
        assert a.pull_requests is not b.pull_requests

    def test_rejects_missing_required_field(self) -> None:
        with pytest.raises(ValidationError):
            CacheFile.model_validate({"generated_at": "2026-05-04T00:00:00Z"})
