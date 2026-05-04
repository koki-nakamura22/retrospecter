"""Unit tests for repo_retrospecter.cache.store."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from repo_retrospecter.cache import store
from repo_retrospecter.cache.store import JSON_INDENT, load, save
from repo_retrospecter.models import (
    CACHE_SCHEMA_VERSION,
    CacheFile,
    Comment,
    Knowledge,
    PullRequest,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_cache(
    *,
    schema_version: str = CACHE_SCHEMA_VERSION,
    with_knowledge: bool = False,
    pull_requests: list[PullRequest] | None = None,
    knowledge: list[Knowledge] | None | object = ...,  # sentinel
    repo: str = "owner/repo",
    generated_at: datetime | None = None,
) -> CacheFile:
    if pull_requests is None:
        pull_requests = [
            PullRequest(
                number=1,
                title="t",
                body="b",
                author="alice",
                merged_at=datetime(2026, 5, 3, tzinfo=UTC),
                url="https://github.com/owner/repo/pull/1",
            )
        ]
    if knowledge is ...:
        knowledge = (
            [
                Knowledge(
                    rule="r",
                    anti_pattern="a",
                    example="e",
                    source_urls=["https://github.com/owner/repo/pull/1"],
                    themes=["design_decision"],
                )
            ]
            if with_knowledge
            else None
        )
    return CacheFile(
        schema_version=schema_version,
        generated_at=generated_at or datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        repo=repo,
        pull_requests=pull_requests,
        knowledge=knowledge,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# save: structure / formatting
# ---------------------------------------------------------------------------


class TestSaveFormat:
    def test_writes_pretty_json_with_2_space_indent(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        text = target.read_text(encoding="utf-8")
        # 2-space indent must show up at depth 1
        assert '\n  "schema_version"' in text
        assert JSON_INDENT == 2

    def test_appends_trailing_newline(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        assert target.read_bytes().endswith(b"\n")

    def test_uses_lf_line_endings(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        # No CR bytes — decision-defaults.md §I/O requires LF on every platform
        assert b"\r" not in target.read_bytes()

    def test_writes_utf8_without_bom(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        # decision-defaults.md §I/O: UTF-8 fixed (BOM なし)
        assert not target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_dumped_file_is_pure_json(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["schema_version"] == CACHE_SCHEMA_VERSION
        assert payload["repo"] == "owner/repo"

    def test_dumped_file_starts_with_open_brace(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        # Sanity: indent + ensure_ascii must not push the document into an
        # exotic prefix (no leading whitespace, no array wrapper).
        assert target.read_text(encoding="utf-8")[0] == "{"


# ---------------------------------------------------------------------------
# save: encoding
# ---------------------------------------------------------------------------


class TestSaveEncoding:
    def test_preserves_japanese_characters_literally(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "cache.json"
        cache = _make_cache(repo="所有者/リポジトリ")

        # Act
        save(target, cache)

        # Assert: ensure_ascii=False keeps the bytes human-readable
        text = target.read_text(encoding="utf-8")
        assert "所有者/リポジトリ" in text
        assert "\\u" not in text  # no escaped unicode for the repo field

    def test_preserves_emoji_in_pr_title(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        cache = _make_cache(
            pull_requests=[
                PullRequest(
                    number=1,
                    title="🚀 launch",
                    body="",
                    author="alice",
                    merged_at=datetime(2026, 5, 3, tzinfo=UTC),
                    url="https://github.com/owner/repo/pull/1",
                )
            ]
        )

        save(target, cache)

        assert "🚀 launch" in target.read_text(encoding="utf-8")

    def test_round_trips_non_ascii_payload(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        original = _make_cache(repo="所有者/リポジトリ")

        save(target, original)
        restored = load(target)

        assert restored == original


# ---------------------------------------------------------------------------
# save: parent-directory creation
# ---------------------------------------------------------------------------


class TestSaveParentDir:
    def test_creates_single_missing_parent(self, tmp_path: Path) -> None:
        target = tmp_path / "missing" / "cache.json"
        save(target, _make_cache())
        assert target.is_file()

    def test_creates_deeply_nested_parents(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "d" / "cache.json"
        save(target, _make_cache())
        assert target.is_file()

    def test_succeeds_when_parent_already_exists(self, tmp_path: Path) -> None:
        # Arrange: parent is the tmp_path which already exists
        target = tmp_path / "cache.json"

        # Act / Assert: no FileExistsError
        save(target, _make_cache())
        assert target.is_file()

    def test_succeeds_when_parent_already_has_other_files(self, tmp_path: Path) -> None:
        parent = tmp_path / ".retrospect"
        parent.mkdir()
        (parent / "stale.json").write_text("ignored", encoding="utf-8")
        target = parent / "cache.json"

        save(target, _make_cache())

        assert target.is_file()
        assert (parent / "stale.json").is_file()  # unrelated files untouched

    def test_raises_when_parent_path_is_a_file(self, tmp_path: Path) -> None:
        # Arrange: a file occupies what should have been a directory
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("nope", encoding="utf-8")
        target = blocker / "cache.json"

        # Act / Assert
        with pytest.raises((FileExistsError, NotADirectoryError, OSError)):
            save(target, _make_cache())


# ---------------------------------------------------------------------------
# save: overwrite behavior (force-check is CLI's job, not store's)
# ---------------------------------------------------------------------------


class TestSaveOverwrite:
    def test_overwrites_existing_file_with_same_name(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        target.write_text("stale", encoding="utf-8")

        save(target, _make_cache())

        text = target.read_text(encoding="utf-8")
        assert "schema_version" in text
        assert "stale" not in text

    def test_overwrites_to_smaller_payload(self, tmp_path: Path) -> None:
        """Regression: writing a smaller payload must not leave trailing bytes
        from the previous content."""
        target = tmp_path / "cache.json"
        target.write_text("x" * 10_000, encoding="utf-8")

        save(target, _make_cache())

        text = target.read_text(encoding="utf-8")
        assert text.startswith("{")
        assert text.endswith("}\n")

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,  # type: ignore[attr-defined]
        reason="chmod read-only is unreliable on Windows / root",
    )
    def test_raises_when_target_is_read_only(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        target.write_text("locked", encoding="utf-8")
        target.chmod(0o400)
        try:
            with pytest.raises(PermissionError):
                save(target, _make_cache())
        finally:
            target.chmod(0o600)


# ---------------------------------------------------------------------------
# save: payload variations (equivalence classes)
# ---------------------------------------------------------------------------


class TestSavePayloadVariations:
    def test_round_trips_with_no_pull_requests(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        original = _make_cache(pull_requests=[])

        save(target, original)

        assert load(target) == original

    def test_round_trips_with_knowledge_none(self, tmp_path: Path) -> None:
        """`fetch` writes knowledge=None before classification runs."""
        target = tmp_path / "cache.json"
        original = _make_cache(knowledge=None)

        save(target, original)

        restored = load(target)
        assert restored.knowledge is None
        assert restored == original

    def test_round_trips_with_knowledge_empty_list(self, tmp_path: Path) -> None:
        """Boundary: distinct from None — classifier ran but yielded nothing."""
        target = tmp_path / "cache.json"
        original = _make_cache(knowledge=[])

        save(target, original)

        restored = load(target)
        assert restored.knowledge == []
        assert restored.knowledge is not None

    def test_round_trips_with_many_pull_requests(self, tmp_path: Path) -> None:
        # Boundary: PRD-stated maximum is 200 PRs; pick a value at that ceiling
        prs = [
            PullRequest(
                number=i,
                title=f"pr-{i}",
                body="",
                author="alice",
                merged_at=datetime(2026, 5, 3, tzinfo=UTC),
                url=f"https://github.com/owner/repo/pull/{i}",
            )
            for i in range(1, 201)
        ]
        target = tmp_path / "cache.json"
        original = _make_cache(pull_requests=prs)

        save(target, original)

        assert load(target) == original

    def test_round_trips_pr_with_review_and_inline_comments(self, tmp_path: Path) -> None:
        comment = Comment(
            id="c-1",
            author="bob",
            body="LGTM",
            created_at=datetime(2026, 5, 3, 9, 0, 0, tzinfo=UTC),
            kind="review",
        )
        pr = PullRequest(
            number=1,
            title="t",
            body="",
            author="alice",
            merged_at=datetime(2026, 5, 3, tzinfo=UTC),
            url="https://github.com/owner/repo/pull/1",
            review_comments=[comment],
            inline_comments=[],
        )
        target = tmp_path / "cache.json"
        original = _make_cache(pull_requests=[pr])

        save(target, original)
        restored = load(target)

        assert restored == original
        assert restored.pull_requests[0].review_comments[0].kind == "review"

    def test_preserves_non_utc_timezone(self, tmp_path: Path) -> None:
        # Equivalence: any offset-aware datetime must survive round trip
        jst = timezone(timedelta(hours=9))
        target = tmp_path / "cache.json"
        original = _make_cache(generated_at=datetime(2026, 5, 4, 21, 0, 0, tzinfo=jst))

        save(target, original)
        restored = load(target)

        # Equality holds because both refer to the same instant
        assert restored.generated_at == original.generated_at
        assert restored.generated_at.utcoffset() == original.generated_at.utcoffset()


# ---------------------------------------------------------------------------
# load: schema_version handling
# ---------------------------------------------------------------------------


class TestLoadSchemaVersion:
    def test_returns_cache_for_matching_version(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())

        result = load(target)

        assert isinstance(result, CacheFile)
        assert result.schema_version == CACHE_SCHEMA_VERSION

    def test_does_not_warn_for_matching_version(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())

        with caplog.at_level(logging.WARNING, logger=store.__name__):
            load(target)

        assert not [r for r in caplog.records if "schema_version" in r.message]

    @pytest.mark.parametrize(
        "bad_version",
        ["", "0", "2", "10", "v1", " 1", "1 ", "1.0"],
        ids=["empty", "older", "next-major", "double-digit", "prefixed", "leading-space", "trailing-space", "dotted"],
    )
    def test_raises_value_error_on_any_mismatch(
        self, tmp_path: Path, bad_version: str
    ) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache(schema_version=bad_version))

        with pytest.raises(ValueError, match="schema_version"):
            load(target)

    def test_warning_includes_both_versions_and_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache(schema_version="999"))

        with (
            caplog.at_level(logging.WARNING, logger=store.__name__),
            pytest.raises(ValueError),
        ):
            load(target)

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "999" in joined
        assert CACHE_SCHEMA_VERSION in joined
        assert str(target) in joined

    def test_value_error_mentions_both_versions(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache(schema_version="999"))

        with pytest.raises(ValueError) as exc_info:
            load(target)

        assert "999" in str(exc_info.value)
        assert CACHE_SCHEMA_VERSION in str(exc_info.value)


# ---------------------------------------------------------------------------
# load: failure modes
# ---------------------------------------------------------------------------


class TestLoadFailures:
    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nope.json")

    def test_raises_for_empty_file(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.json"
        target.write_text("", encoding="utf-8")

        with pytest.raises(ValueError):
            load(target)

    def test_raises_for_invalid_json_syntax(self, tmp_path: Path) -> None:
        target = tmp_path / "broken.json"
        target.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError):
            load(target)

    def test_raises_for_json_array_instead_of_object(self, tmp_path: Path) -> None:
        target = tmp_path / "array.json"
        target.write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError):
            load(target)

    def test_raises_when_required_field_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "partial.json"
        target.write_text(
            json.dumps({"schema_version": CACHE_SCHEMA_VERSION, "repo": "o/r"}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError):
            load(target)

    def test_raises_for_extra_unknown_field(self, tmp_path: Path) -> None:
        # CacheFile uses extra="forbid"; unknown fields must reject so silent
        # cache-format drift is impossible.
        target = tmp_path / "extra.json"
        target.write_text(
            json.dumps(
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "generated_at": "2026-05-04T00:00:00Z",
                    "repo": "o/r",
                    "pull_requests": [],
                    "knowledge": None,
                    "rogue": True,
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError):
            load(target)

    def test_raises_when_path_is_a_directory(self, tmp_path: Path) -> None:
        # Equivalence-class: path exists but isn't a file
        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            load(tmp_path)
