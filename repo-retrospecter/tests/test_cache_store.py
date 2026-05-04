"""Unit tests for repo_retrospect.cache.store."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_retrospect.cache import store
from repo_retrospect.cache.store import JSON_INDENT, load, save
from repo_retrospect.models import (
    CACHE_SCHEMA_VERSION,
    CacheFile,
    Knowledge,
    PullRequest,
)


def _make_cache(
    *,
    schema_version: str = CACHE_SCHEMA_VERSION,
    with_knowledge: bool = False,
) -> CacheFile:
    return CacheFile(
        schema_version=schema_version,
        generated_at=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        repo="owner/repo",
        pull_requests=[
            PullRequest(
                number=1,
                title="t",
                body="b",
                author="alice",
                merged_at=datetime(2026, 5, 3, tzinfo=UTC),
                url="https://github.com/owner/repo/pull/1",
            )
        ],
        knowledge=(
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
        ),
    )


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


class TestSave:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "cache.json"
        save(target, _make_cache())
        assert target.is_file()

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
        # No CR bytes — decision-defaults.md §I/O requires LF
        assert b"\r" not in target.read_bytes()

    def test_overwrites_existing_file_unconditionally(self, tmp_path: Path) -> None:
        """Force-checking is the CLI's job; ``save`` just writes."""
        target = tmp_path / "cache.json"
        target.write_text("stale", encoding="utf-8")
        save(target, _make_cache())
        assert "schema_version" in target.read_text(encoding="utf-8")

    def test_round_trips_via_save_then_load(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        original = _make_cache(with_knowledge=True)
        save(target, original)
        assert load(target) == original

    def test_dumped_file_is_pure_json(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["schema_version"] == CACHE_SCHEMA_VERSION
        assert payload["repo"] == "owner/repo"


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_cache_file_for_current_schema(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache())
        result = load(target)
        assert isinstance(result, CacheFile)
        assert result.schema_version == CACHE_SCHEMA_VERSION

    def test_raises_value_error_on_schema_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache(schema_version="999"))
        with pytest.raises(ValueError, match="schema_version"):
            load(target)

    def test_logs_warning_on_schema_mismatch(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "cache.json"
        save(target, _make_cache(schema_version="999"))
        with (
            caplog.at_level(logging.WARNING, logger=store.__name__),
            pytest.raises(ValueError),
        ):
            load(target)
        assert any("schema_version mismatch" in r.message for r in caplog.records)

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nope.json")
