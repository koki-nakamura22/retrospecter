"""Unit tests for repo_retrospect.pipeline (T007)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from repo_retrospect.cache.store import load as load_cache
from repo_retrospect.cache.store import save as save_cache
from repo_retrospect.models.cache import CACHE_SCHEMA_VERSION, CacheFile
from repo_retrospect.models.knowledge import Knowledge
from repo_retrospect.models.pull_request import PullRequest
from repo_retrospect.pipeline import fetch as fetch_mod
from repo_retrospect.pipeline import generate as generate_mod
from repo_retrospect.pipeline import run as run_mod
from repo_retrospect.pipeline.fetch import FetchSummary, run_fetch
from repo_retrospect.pipeline.generate import GenerateSummary, run_generate
from repo_retrospect.pipeline.run import RunSummary, run_pipeline
from repo_retrospect.services.exceptions import AuthError, RateLimitError

# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pr(number: int = 1, *, url: str | None = None) -> PullRequest:
    return PullRequest(
        number=number,
        title=f"PR {number}",
        body="",
        author="alice",
        merged_at=datetime(2026, 5, 3, tzinfo=UTC),
        url=url or f"https://github.com/owner/repo/pull/{number}",
    )


def _make_knowledge(
    rule: str = "Prefer X",
    *,
    source_urls: list[str] | None = None,
    themes: list[str] | None = None,
) -> Knowledge:
    return Knowledge(
        rule=rule,
        anti_pattern="don't Y",
        example="ex",
        source_urls=source_urls or ["https://github.com/owner/repo/pull/1"],
        themes=themes or ["design_decision"],
    )


def _make_cache(
    *,
    repo: str = "owner/repo",
    pull_requests: list[PullRequest] | None = None,
    knowledge: list[Knowledge] | None = None,
) -> CacheFile:
    return CacheFile(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC),
        repo=repo,
        pull_requests=pull_requests if pull_requests is not None else [_make_pr()],
        knowledge=knowledge,
    )


# ---------------------------------------------------------------------------
# pipeline.fetch.run_fetch
# ---------------------------------------------------------------------------


class TestRunFetch:
    def test_persists_pull_requests_to_cache_path(self, tmp_path: Path) -> None:
        prs = [_make_pr(1), _make_pr(2)]
        cache_path = tmp_path / "cache.json"
        with patch.object(
            fetch_mod, "fetch_pull_requests", return_value=prs
        ) as fetcher_mock:
            summary = run_fetch(repo="owner/repo", cache_path=cache_path, last=2)

        fetcher_mock.assert_called_once()
        loaded = load_cache(cache_path)
        assert loaded.repo == "owner/repo"
        assert [pr.number for pr in loaded.pull_requests] == [1, 2]
        assert loaded.knowledge is None
        assert summary == FetchSummary(
            repo="owner/repo", cache_path=cache_path, pr_count=2
        )

    def test_passes_through_last_since_and_timeout_to_fetcher(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(
            fetch_mod, "fetch_pull_requests", return_value=[]
        ) as fetcher_mock:
            run_fetch(
                repo="owner/repo",
                cache_path=cache_path,
                last=10,
                since=date(2026, 4, 1),
                timeout=12.5,
            )
        fetcher_mock.assert_called_once_with(
            "owner/repo", last=10, since=date(2026, 4, 1), timeout=12.5
        )

    def test_creates_parent_directories_for_cache_path(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "nested" / "deep" / "cache.json"
        with patch.object(fetch_mod, "fetch_pull_requests", return_value=[]):
            run_fetch(repo="owner/repo", cache_path=cache_path)
        assert cache_path.exists()

    def test_overwrites_previous_knowledge_in_cache(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        # Pre-existing cache with knowledge — fetch must drop it.
        save_cache(
            cache_path,
            _make_cache(knowledge=[_make_knowledge("legacy rule")]),
        )
        with patch.object(
            fetch_mod, "fetch_pull_requests", return_value=[_make_pr(7)]
        ):
            run_fetch(repo="owner/repo", cache_path=cache_path)

        reloaded = load_cache(cache_path)
        assert reloaded.knowledge is None
        assert [pr.number for pr in reloaded.pull_requests] == [7]

    def test_propagates_auth_error(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(
            fetch_mod,
            "fetch_pull_requests",
            side_effect=AuthError("gh authentication required"),
        ), pytest.raises(AuthError):
            run_fetch(repo="owner/repo", cache_path=cache_path)
        # Cache must not be created when fetch failed (TC-F1-03 contract).
        assert not cache_path.exists()

    def test_propagates_rate_limit_error(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(
            fetch_mod,
            "fetch_pull_requests",
            side_effect=RateLimitError("rate limit"),
        ), pytest.raises(RateLimitError):
            run_fetch(repo="owner/repo", cache_path=cache_path)
        assert not cache_path.exists()

    def test_zero_prs_yields_summary_with_zero_count(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(fetch_mod, "fetch_pull_requests", return_value=[]):
            summary = run_fetch(repo="owner/repo", cache_path=cache_path)
        assert summary.pr_count == 0
        loaded = load_cache(cache_path)
        assert loaded.pull_requests == []

    def test_generated_at_is_timezone_aware_utc(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(fetch_mod, "fetch_pull_requests", return_value=[]):
            run_fetch(repo="owner/repo", cache_path=cache_path)
        loaded = load_cache(cache_path)
        assert loaded.generated_at.tzinfo is not None
        assert loaded.generated_at.utcoffset() == datetime.now(UTC).utcoffset()

    def test_writes_current_cache_schema_version(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        with patch.object(fetch_mod, "fetch_pull_requests", return_value=[]):
            run_fetch(repo="owner/repo", cache_path=cache_path)
        loaded = load_cache(cache_path)
        assert loaded.schema_version == CACHE_SCHEMA_VERSION

    def test_accepts_since_as_iso_string(self, tmp_path: Path) -> None:
        # Per type signature `since: date | str | None`, callers may pass
        # an ISO-formatted string and the value must be forwarded unchanged.
        cache_path = tmp_path / "cache.json"
        with patch.object(
            fetch_mod, "fetch_pull_requests", return_value=[]
        ) as fetcher_mock:
            run_fetch(
                repo="owner/repo",
                cache_path=cache_path,
                since="2026-04-01",
            )
        _, kwargs = fetcher_mock.call_args
        assert kwargs.get("since") == "2026-04-01"


# ---------------------------------------------------------------------------
# pipeline.generate.run_generate
# ---------------------------------------------------------------------------


class TestRunGenerateClassification:
    def test_calls_classifier_when_cache_has_no_knowledge(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=None))
        knowledge = [_make_knowledge()]

        with patch.object(
            generate_mod, "classify_pull_requests", return_value=knowledge
        ) as classifier_mock:
            summary = run_generate(cache_path=cache_path, skip_render=True)

        classifier_mock.assert_called_once()
        assert summary.classified is True
        assert summary.knowledge_count == 1
        # Cache is updated in place with the new knowledge.
        reloaded = load_cache(cache_path)
        assert reloaded.knowledge == knowledge

    def test_skips_classifier_when_cache_already_has_knowledge(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.json"
        existing_knowledge = [_make_knowledge("cached rule")]
        save_cache(cache_path, _make_cache(knowledge=existing_knowledge))

        with patch.object(
            generate_mod, "classify_pull_requests"
        ) as classifier_mock:
            summary = run_generate(cache_path=cache_path, skip_render=True)

        classifier_mock.assert_not_called()
        assert summary.classified is False
        assert summary.knowledge_count == 1

    def test_empty_knowledge_with_no_uncovered_items_skips_classifier(
        self, tmp_path: Path
    ) -> None:
        # Updated for ADR-0005: knowledge != None means "we've already
        # classified what we know about". Generate skips the classifier
        # only when there are no PRs/commits with uncovered URLs.
        cache_path = tmp_path / "cache.json"
        # No pull_requests AND knowledge=[] => nothing to classify.
        save_cache(cache_path, _make_cache(knowledge=[], pull_requests=[]))

        with patch.object(
            generate_mod, "classify_pull_requests"
        ) as classifier_mock:
            summary = run_generate(cache_path=cache_path, skip_render=True)

        classifier_mock.assert_not_called()
        assert summary.classified is False
        assert summary.knowledge_count == 0

    def test_forwards_themes_to_classifier(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=None))

        with patch.object(
            generate_mod, "classify_pull_requests", return_value=[]
        ) as classifier_mock:
            run_generate(
                cache_path=cache_path,
                skip_render=True,
                themes=["security", "performance"],
            )

        classifier_mock.assert_called_once()
        _, kwargs = classifier_mock.call_args
        assert kwargs.get("themes") == ["security", "performance"]

    def test_propagates_auth_error_from_classifier(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=None))

        with patch.object(
            generate_mod,
            "classify_pull_requests",
            side_effect=AuthError("ANTHROPIC_API_KEY missing"),
        ), pytest.raises(AuthError):
            run_generate(cache_path=cache_path, skip_render=True)

    def test_propagates_rate_limit_error_from_classifier(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=None))

        with patch.object(
            generate_mod,
            "classify_pull_requests",
            side_effect=RateLimitError("anthropic rate limit"),
        ), pytest.raises(RateLimitError):
            run_generate(cache_path=cache_path, skip_render=True)

    def test_refreshes_generated_at_when_classifier_runs(
        self, tmp_path: Path
    ) -> None:
        # Code contract: when classification runs, the persisted cache's
        # generated_at is updated to "now" so consumers can tell when the
        # knowledge was produced. We seed an unambiguously-past fetch time
        # so the comparison is robust to wall-clock skew.
        cache_path = tmp_path / "cache.json"
        past = datetime(2020, 1, 1, tzinfo=UTC)
        original = CacheFile(
            schema_version=CACHE_SCHEMA_VERSION,
            generated_at=past,
            repo="owner/repo",
            pull_requests=[_make_pr()],
            knowledge=None,
        )
        save_cache(cache_path, original)

        with patch.object(
            generate_mod, "classify_pull_requests", return_value=[_make_knowledge()]
        ):
            run_generate(cache_path=cache_path, skip_render=True)

        reloaded = load_cache(cache_path)
        assert reloaded.generated_at > past

    def test_preserves_generated_at_when_reusing_cached_knowledge(
        self, tmp_path: Path
    ) -> None:
        # Counterpart contract: when classification is skipped (knowledge
        # already cached), the cache file is NOT rewritten, so generated_at
        # on disk must match the value originally saved.
        cache_path = tmp_path / "cache.json"
        original = _make_cache(knowledge=[_make_knowledge("cached rule")])
        save_cache(cache_path, original)

        with patch.object(
            generate_mod, "classify_pull_requests"
        ) as classifier_mock:
            run_generate(cache_path=cache_path, skip_render=True)

        classifier_mock.assert_not_called()
        reloaded = load_cache(cache_path)
        assert reloaded.generated_at == original.generated_at


class TestRunGenerateRendering:
    def test_renders_human_output_when_human_out_specified(
        self, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(
            cache_path, _make_cache(knowledge=[_make_knowledge(themes=["design_decision"])])
        )
        human_out = tmp_path / "human.md"

        summary = run_generate(cache_path=cache_path, human_out=human_out)

        assert human_out.exists()
        assert summary.rendered_outputs == (human_out,)
        text = human_out.read_text(encoding="utf-8")
        assert "# 振り返り" in text

    def test_renders_ai_output_when_ai_out_specified(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=[_make_knowledge()]))
        ai_out = tmp_path / "ai.md"

        summary = run_generate(cache_path=cache_path, ai_out=ai_out)

        assert ai_out.exists()
        assert summary.rendered_outputs == (ai_out,)
        text = ai_out.read_text(encoding="utf-8")
        assert "https://github.com/" in text

    def test_renders_both_outputs_when_both_paths_given(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=[_make_knowledge()]))
        human_out = tmp_path / "human.md"
        ai_out = tmp_path / "ai.md"

        summary = run_generate(
            cache_path=cache_path, human_out=human_out, ai_out=ai_out
        )

        assert human_out.exists() and ai_out.exists()
        assert summary.rendered_outputs == (human_out, ai_out)

    def test_skip_render_suppresses_all_output_files(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=[_make_knowledge()]))
        human_out = tmp_path / "human.md"
        ai_out = tmp_path / "ai.md"

        summary = run_generate(
            cache_path=cache_path,
            human_out=human_out,
            ai_out=ai_out,
            skip_render=True,
        )

        assert not human_out.exists()
        assert not ai_out.exists()
        assert summary.rendered_outputs == ()

    def test_no_outputs_when_neither_path_given(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=[_make_knowledge()]))

        summary = run_generate(cache_path=cache_path)

        assert summary.rendered_outputs == ()

    def test_summary_reports_pr_count_from_cache(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        prs = [_make_pr(i) for i in (1, 2, 3, 4)]
        save_cache(
            cache_path,
            _make_cache(pull_requests=prs, knowledge=[_make_knowledge()]),
        )
        summary = run_generate(cache_path=cache_path)
        assert summary.pr_count == 4

    def test_uses_renderer_factory_for_each_format(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        save_cache(cache_path, _make_cache(knowledge=[_make_knowledge()]))
        human_out = tmp_path / "h.md"
        ai_out = tmp_path / "a.md"

        captured: list[str] = []

        def fake_get_renderer(name: Any) -> Any:
            captured.append(name)
            renderer = MagicMock()
            renderer.render = MagicMock(
                side_effect=lambda _cache, out: out.write_text("ok\n", encoding="utf-8")
            )
            return renderer

        with patch.object(generate_mod, "get_renderer", side_effect=fake_get_renderer):
            run_generate(cache_path=cache_path, human_out=human_out, ai_out=ai_out)

        assert captured == ["human", "ai"]


# ---------------------------------------------------------------------------
# pipeline.run.run_pipeline
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def test_calls_fetch_then_generate_in_order(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        human_out = tmp_path / "h.md"
        ai_out = tmp_path / "a.md"

        fetch_summary = FetchSummary(
            repo="owner/repo", cache_path=cache_path, pr_count=3
        )
        generate_summary = GenerateSummary(
            cache_path=cache_path,
            pr_count=3,
            knowledge_count=2,
            classified=True,
            rendered_outputs=(human_out, ai_out),
        )
        order: list[str] = []

        def fake_fetch(**_: object) -> FetchSummary:
            order.append("fetch")
            return fetch_summary

        def fake_generate(**_: object) -> GenerateSummary:
            order.append("generate")
            return generate_summary

        with (
            patch.object(run_mod, "run_fetch", side_effect=fake_fetch) as fetch_mock,
            patch.object(
                run_mod, "run_generate", side_effect=fake_generate
            ) as generate_mock,
        ):
            result = run_pipeline(
                repo="owner/repo",
                cache_path=cache_path,
                last=5,
                human_out=human_out,
                ai_out=ai_out,
            )

        assert order == ["fetch", "generate"]
        fetch_mock.assert_called_once()
        generate_mock.assert_called_once()
        assert result == RunSummary(fetch=fetch_summary, generate=generate_summary)

    def test_skips_generate_when_fetch_raises(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"

        with (
            patch.object(
                run_mod, "run_fetch", side_effect=AuthError("no auth")
            ) as fetch_mock,
            patch.object(run_mod, "run_generate") as generate_mock,
            pytest.raises(AuthError),
        ):
            run_pipeline(repo="owner/repo", cache_path=cache_path)

        fetch_mock.assert_called_once()
        generate_mock.assert_not_called()

    def test_propagates_rate_limit_error_from_fetch(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"

        with (
            patch.object(
                run_mod, "run_fetch", side_effect=RateLimitError("gh rate limit")
            ) as fetch_mock,
            patch.object(run_mod, "run_generate") as generate_mock,
            pytest.raises(RateLimitError),
        ):
            run_pipeline(repo="owner/repo", cache_path=cache_path)

        fetch_mock.assert_called_once()
        generate_mock.assert_not_called()

    def test_propagates_error_from_generate_after_successful_fetch(
        self, tmp_path: Path
    ) -> None:
        # Symmetric to test_skips_generate_when_fetch_raises: a failure in
        # the generate phase must surface to the caller (CLI converts to
        # ClickException) without being silently swallowed.
        cache_path = tmp_path / "cache.json"

        with (
            patch.object(
                run_mod,
                "run_fetch",
                return_value=FetchSummary(
                    repo="owner/repo", cache_path=cache_path, pr_count=0
                ),
            ) as fetch_mock,
            patch.object(
                run_mod,
                "run_generate",
                side_effect=AuthError("ANTHROPIC_API_KEY missing"),
            ) as generate_mock,
            pytest.raises(AuthError),
        ):
            run_pipeline(repo="owner/repo", cache_path=cache_path)

        fetch_mock.assert_called_once()
        generate_mock.assert_called_once()

    def test_forwards_arguments_to_fetch_and_generate(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        human_out = tmp_path / "h.md"
        ai_out = tmp_path / "a.md"

        with (
            patch.object(
                run_mod,
                "run_fetch",
                return_value=FetchSummary(
                    repo="owner/repo", cache_path=cache_path, pr_count=0
                ),
            ) as fetch_mock,
            patch.object(
                run_mod,
                "run_generate",
                return_value=GenerateSummary(
                    cache_path=cache_path,
                    pr_count=0,
                    knowledge_count=0,
                    classified=True,
                ),
            ) as generate_mock,
        ):
            run_pipeline(
                repo="owner/repo",
                cache_path=cache_path,
                last=7,
                since=date(2026, 1, 1),
                human_out=human_out,
                ai_out=ai_out,
                themes=["security"],
                timeout=42.0,
            )

        fetch_mock.assert_called_once_with(
            repo="owner/repo",
            cache_path=cache_path,
            last=7,
            last_commits=None,
            since=date(2026, 1, 1),
            include_loose_commits=True,
            append=False,
            timeout=42.0,
        )
        generate_mock.assert_called_once_with(
            cache_path=cache_path,
            human_out=human_out,
            ai_out=ai_out,
            themes=["security"],
        )

    def test_end_to_end_with_real_modules(self, tmp_path: Path) -> None:
        # Patch only the leaf services (gh CLI + Anthropic) and let the
        # pipeline + cache + renderer wire-up run for real.
        cache_path = tmp_path / "cache.json"
        human_out = tmp_path / "human.md"
        ai_out = tmp_path / "ai.md"

        prs = [_make_pr(1)]
        knowledge = [_make_knowledge(themes=["design_decision"])]

        with (
            patch.object(fetch_mod, "fetch_pull_requests", return_value=prs),
            patch.object(
                generate_mod, "classify_pull_requests", return_value=knowledge
            ),
        ):
            summary = run_pipeline(
                repo="owner/repo",
                cache_path=cache_path,
                last=1,
                human_out=human_out,
                ai_out=ai_out,
            )

        assert summary.fetch.pr_count == 1
        assert summary.generate.classified is True
        assert summary.generate.knowledge_count == 1
        assert summary.generate.rendered_outputs == (human_out, ai_out)
        assert human_out.exists() and ai_out.exists()
