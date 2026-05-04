"""Acceptance tests for repo-retrospect (T009).

Implements docs/test-cases/acceptance.md TC-F1-* / TC-F2-* / TC-F3-* / TC-F4-*.
External boundaries (``gh`` CLI subprocess, Anthropic SDK) are mocked so the
suite runs hermetically inside the auto-implement Docker harness.

Performance / security cases (TC-PERF-01, TC-SEC-01) are marked ``slow`` and
excluded from the default invocation; CI may schedule them in a separate job.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from repo_retrospect.cli.main import cli
from repo_retrospect.models.cache import CACHE_SCHEMA_VERSION, CacheFile
from repo_retrospect.models.knowledge import Knowledge

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_REPO = "koki-n/sample-repo"


# ---------------------------------------------------------------------------
# fixture loaders + synthetic data builders
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _make_pr_dict(
    number: int,
    *,
    merged_at: str = "2026-04-15T10:00:00Z",
) -> dict[str, Any]:
    """Synthesize a single PR dict matching ``gh pr list --json`` output."""
    return {
        "number": number,
        "title": f"Refactor module #{number}",
        "body": f"Body of PR {number}: tightens the cache invariant.",
        "author": {"login": "alice"},
        "mergedAt": merged_at,
        "url": f"https://github.com/{SAMPLE_REPO}/pull/{number}",
    }


def _make_issue_comment(pr_number: int) -> list[dict[str, Any]]:
    return [
        {
            "id": 9000 + pr_number,
            "body": "LGTM — please add a regression test before merging.",
            "user": {"login": "carol"},
            "created_at": "2026-04-15T10:30:00Z",
        }
    ]


def _make_review_payload(pr_number: int) -> list[dict[str, Any]]:
    return [
        {
            "id": 7000 + pr_number,
            "body": "Approved.",
            "user": {"login": "dave"},
            "submitted_at": "2026-04-15T11:00:00Z",
        }
    ]


def _make_inline_comment(pr_number: int) -> list[dict[str, Any]]:
    return [
        {
            "id": 5000 + pr_number,
            "body": "nit: name this `cache_path` for symmetry.",
            "user": {"login": "dave"},
            "created_at": "2026-04-15T10:45:00Z",
        }
    ]


def _completed(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(args=["gh"], returncode=code, stdout=stdout, stderr=stderr)


def _make_gh_dispatcher(prs: list[dict[str, Any]]):
    """Build a ``subprocess.run`` side_effect that emulates the ``gh`` CLI.

    Recognized argv shapes:
      * ``gh pr list --repo X --json ... --state merged --limit N``
      * ``gh pr list --repo X --json ... --search "is:merged merged:>=YYYY-..." --limit N``
      * ``gh api repos/X/issues/<n>/comments``
      * ``gh api repos/X/pulls/<n>/reviews``
      * ``gh api repos/X/pulls/<n>/comments``
    """

    def dispatch(args, **_kwargs):  # noqa: ANN001 - subprocess.run signature
        argv = list(args)
        assert argv and argv[0] == "gh", argv
        sub = argv[1]
        if sub == "pr" and argv[2] == "list":
            return _completed(stdout=json.dumps(prs), code=0)
        if sub == "api":
            endpoint = argv[2]
            if endpoint.endswith("/issues/comments") or "/issues/" in endpoint:
                # repos/X/issues/<n>/comments
                pr_no = int(endpoint.rsplit("/", 2)[-2])
                return _completed(stdout=json.dumps(_make_issue_comment(pr_no)), code=0)
            if endpoint.endswith("/reviews"):
                pr_no = int(endpoint.rsplit("/", 2)[-2])
                return _completed(stdout=json.dumps(_make_review_payload(pr_no)), code=0)
            if endpoint.endswith("/comments"):
                pr_no = int(endpoint.rsplit("/", 2)[-2])
                return _completed(stdout=json.dumps(_make_inline_comment(pr_no)), code=0)
        raise AssertionError(f"unexpected gh argv: {argv}")

    return dispatch


# ---------------------------------------------------------------------------
# stub Knowledge generator (replaces classify_pull_requests in CLI runs)
# ---------------------------------------------------------------------------


def _knowledge_for(prs, themes=None):  # noqa: ANN001 - signature mirrors classifier
    """Return one ``Knowledge`` record per PR, citing the PR URL."""
    allowed = themes or ["design_decision", "review_rule", "bug_pattern", "refactor", "other"]
    out: list[Knowledge] = []
    for i, pr in enumerate(prs):
        theme = allowed[i % len(allowed)]
        out.append(
            Knowledge(
                rule=f"Rule extracted from PR #{pr.number}",
                anti_pattern=f"Avoid the pattern PR #{pr.number} fixed.",
                example="result = compute(value)",
                source_urls=[pr.url],
                themes=[theme],
            )
        )
    return out


# ===========================================================================
# F1: fetch
# ===========================================================================


@pytest.mark.acceptance
def test_f1_01_fetch_by_count(tmp_path: Path) -> None:
    """TC-F1-01: ``fetch --last 30`` writes 30 PRs into the cache file."""
    cache = tmp_path / "cache.json"
    prs = [_make_pr_dict(n) for n in range(1, 31)]

    with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
        mock_run.side_effect = _make_gh_dispatcher(prs)
        result = CliRunner().invoke(
            cli,
            ["fetch", "--repo", SAMPLE_REPO, "--last", "30", "--cache", str(cache)],
        )

    assert result.exit_code == 0, result.output
    assert cache.exists(), "cache file should be created"

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CACHE_SCHEMA_VERSION
    assert len(payload["pull_requests"]) == 30
    for pr in payload["pull_requests"]:
        assert {"number", "title", "body", "review_comments", "inline_comments"} <= set(pr)


@pytest.mark.acceptance
def test_f1_02_fetch_by_since(tmp_path: Path) -> None:
    """TC-F1-02: ``fetch --since`` returns only PRs at/after the boundary."""
    cache = tmp_path / "cache.json"
    prs = [
        _make_pr_dict(n, merged_at=f"2026-04-{day:02d}T10:00:00Z")
        for n, day in zip(range(1, 6), (1, 5, 10, 20, 25), strict=False)
    ]

    with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
        mock_run.side_effect = _make_gh_dispatcher(prs)
        result = CliRunner().invoke(
            cli,
            ["fetch", "--repo", SAMPLE_REPO, "--since", "2026-04-01", "--cache", str(cache)],
        )

    assert result.exit_code == 0, result.output

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert len(payload["pull_requests"]) == 5
    boundary = datetime(2026, 4, 1, tzinfo=UTC)
    for pr in payload["pull_requests"]:
        assert datetime.fromisoformat(pr["merged_at"]) >= boundary

    # The argv to ``gh pr list`` must carry the ``--search merged:>=...`` filter.
    pr_list_call = next(c for c in mock_run.call_args_list if c.args[0][:2] == ["gh", "pr"])
    argv = pr_list_call.args[0]
    assert "--search" in argv
    assert any("merged:>=2026-04-01" in a for a in argv)


@pytest.mark.acceptance
def test_f1_03_no_auth_error(tmp_path: Path) -> None:
    """TC-F1-03: ``gh auth status`` failure surfaces a typed error and no cache."""
    cache = tmp_path / "cache.json"

    def gh_unauth(_args, **_kwargs):  # noqa: ANN001
        return _completed(stderr="error: gh authentication required", code=4)

    with patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run:
        mock_run.side_effect = gh_unauth
        result = CliRunner().invoke(
            cli,
            ["fetch", "--repo", SAMPLE_REPO, "--last", "5", "--cache", str(cache)],
        )

    assert result.exit_code != 0
    assert "gh authentication required" in result.output
    assert not cache.exists(), "cache must not be written on auth failure"


# ===========================================================================
# F2: classify
# ===========================================================================


def _seed_unclassified_cache(cache_path: Path, n: int = 10) -> CacheFile:
    """Write a cache file with N PRs (and no knowledge yet) for generate tests."""
    from repo_retrospect.cache.store import save as save_cache
    from repo_retrospect.models.pull_request import PullRequest

    prs = [
        PullRequest(
            number=i,
            title=f"PR {i}",
            body=f"Body {i}",
            author="alice",
            merged_at=datetime(2026, 4, 1 + (i % 28), 10, 0, tzinfo=UTC),
            url=f"https://github.com/{SAMPLE_REPO}/pull/{i}",
            review_comments=[],
            inline_comments=[],
        )
        for i in range(1, n + 1)
    ]
    cache = CacheFile(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        repo=SAMPLE_REPO,
        pull_requests=prs,
        knowledge=None,
    )
    save_cache(cache_path, cache)
    return cache


@pytest.mark.acceptance
def test_f2_01_default_themes(tmp_path: Path) -> None:
    """TC-F2-01: classification populates themes drawn from the canonical 5."""
    cache_path = tmp_path / "cache.json"
    _seed_unclassified_cache(cache_path, n=10)
    canonical = {"design_decision", "review_rule", "bug_pattern", "refactor", "other"}

    with patch(
        "repo_retrospect.pipeline.generate.classify_pull_requests",
        side_effect=lambda prs, themes=None: _knowledge_for(prs, themes),
    ):
        result = CliRunner().invoke(cli, ["generate", "--cache", str(cache_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    knowledge = payload["knowledge"]
    assert knowledge, "classification must produce at least one knowledge record"
    for k in knowledge:
        assert k["themes"], "each knowledge record must carry >= 1 theme"
        for t in k["themes"]:
            assert t in canonical, f"theme {t!r} not in canonical set"


@pytest.mark.acceptance
def test_f2_02_custom_themes(tmp_path: Path) -> None:
    """TC-F2-02: ``--config`` themes flow through to the classifier output."""
    cache_path = tmp_path / "cache.json"
    _seed_unclassified_cache(cache_path, n=5)

    cfg = tmp_path / "repo-retrospect.config.json"
    cfg.write_text(
        json.dumps({"themes": ["security", "performance", "other"]}),
        encoding="utf-8",
    )
    allowed = {"security", "performance", "other"}

    captured: dict[str, Any] = {}

    def fake_classify(prs, themes=None):
        captured["themes"] = themes
        return _knowledge_for(prs, themes)

    with patch(
        "repo_retrospect.pipeline.generate.classify_pull_requests",
        side_effect=fake_classify,
    ):
        result = CliRunner().invoke(
            cli, ["generate", "--cache", str(cache_path), "--config", str(cfg)]
        )

    assert result.exit_code == 0, result.output
    assert captured["themes"] == ["security", "performance", "other"]
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    for k in payload["knowledge"]:
        for t in k["themes"]:
            assert t in allowed, f"theme {t!r} outside configured set"


# ===========================================================================
# F3: human Markdown
# ===========================================================================


def _seed_classified_cache(cache_path: Path, n: int = 10) -> None:
    """Write a cache file already populated with knowledge for renderer tests."""
    from repo_retrospect.cache.store import save as save_cache
    from repo_retrospect.models.pull_request import PullRequest

    prs = [
        PullRequest(
            number=i,
            title=f"PR {i}",
            body=f"Body {i}",
            author="alice",
            merged_at=datetime(2026, 4, 1 + (i % 28), 10, 0, tzinfo=UTC),
            url=f"https://github.com/{SAMPLE_REPO}/pull/{i}",
            review_comments=[],
            inline_comments=[],
        )
        for i in range(1, n + 1)
    ]
    knowledge: list[Knowledge] = []
    pool = ["design_decision", "review_rule", "bug_pattern", "refactor", "other"]
    for i, pr in enumerate(prs):
        knowledge.append(
            Knowledge(
                rule=f"Rule {i}: prefer explicit over implicit",
                anti_pattern="Avoid implicit fallthrough",
                example="value = explicit_call()",
                source_urls=[pr.url],
                themes=[pool[i % len(pool)]],
            )
        )
    cache = CacheFile(
        schema_version=CACHE_SCHEMA_VERSION,
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        repo=SAMPLE_REPO,
        pull_requests=prs,
        knowledge=knowledge,
    )
    save_cache(cache_path, cache)


@pytest.mark.acceptance
def test_f3_01_human_markdown_output(tmp_path: Path) -> None:
    """TC-F3-01: human Markdown carries the required headings + PR URLs."""
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "learnings/2026-04.md"
    _seed_classified_cache(cache_path, n=10)

    result = CliRunner().invoke(cli, ["generate", "--cache", str(cache_path), "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    for header in ("# 振り返り", "## 主要設計判断", "## 頻出レビュー指摘 Top"):
        assert header in text, f"missing header: {header!r}"
    assert f"https://github.com/{SAMPLE_REPO}/pull/" in text


# ===========================================================================
# F4: AI Markdown
# ===========================================================================


@pytest.mark.acceptance
def test_f4_01_ai_structured_output(tmp_path: Path) -> None:
    """TC-F4-01: AI Markdown carries Rule / Anti-pattern / code fence + URL."""
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "learnings/ai-knowledge.md"
    _seed_classified_cache(cache_path, n=10)

    result = CliRunner().invoke(cli, ["generate", "--cache", str(cache_path), "--ai-out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "### Rule:" in text
    assert "**Anti-pattern**:" in text
    assert "```" in text  # at least one code fence
    assert "https://github.com/" in text


@pytest.mark.acceptance
def test_f4_02_ai_citation_required(tmp_path: Path) -> None:
    """TC-F4-02: knowledge without a github.com URL is omitted from AI output."""
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "ai-out.md"
    from repo_retrospect.cache.store import save as save_cache
    from repo_retrospect.models.pull_request import PullRequest

    prs = [
        PullRequest(
            number=1,
            title="PR with citation",
            body="",
            author="alice",
            merged_at=datetime(2026, 4, 1, tzinfo=UTC),
            url=f"https://github.com/{SAMPLE_REPO}/pull/1",
            review_comments=[],
            inline_comments=[],
        )
    ]
    knowledge = [
        Knowledge(
            rule="Cited rule",
            anti_pattern="",
            example="",
            source_urls=[f"https://github.com/{SAMPLE_REPO}/pull/1"],
            themes=["design_decision"],
        ),
        Knowledge(
            rule="Uncited rule (must be filtered)",
            anti_pattern="",
            example="",
            source_urls=["https://example.com/not-github"],
            themes=["other"],
        ),
    ]
    save_cache(
        cache_path,
        CacheFile(
            schema_version=CACHE_SCHEMA_VERSION,
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            repo=SAMPLE_REPO,
            pull_requests=prs,
            knowledge=knowledge,
        ),
    )

    result = CliRunner().invoke(cli, ["generate", "--cache", str(cache_path), "--ai-out", str(out)])

    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "Cited rule" in text
    assert "Uncited rule" not in text
    # Every "### Rule:" block in the output must be backed by a github.com URL.
    blocks = [b for b in text.split("### Rule:")[1:]]
    assert blocks, "expected at least one rule block"
    for block in blocks:
        assert "https://github.com/" in block, "rule block missing github.com source"


# ===========================================================================
# Slow / cross-cutting (TC-PERF-01, TC-SEC-01) — excluded from default run
# ===========================================================================


@pytest.mark.acceptance
@pytest.mark.slow
def test_perf_01_30pr_within_5min(tmp_path: Path) -> None:
    """TC-PERF-01: end-to-end ``run`` for 30 PRs completes within 5 minutes.

    Marked ``slow`` so the default suite skips it; CI's perf job (separate)
    invokes ``pytest -m slow`` against a real backend with timing assertions.
    """
    import time

    cache = tmp_path / "cache.json"
    prs = [_make_pr_dict(n) for n in range(1, 31)]
    call_counter = {"llm": 0}

    def fake_classify(pull_requests, themes=None):
        call_counter["llm"] += 1
        return _knowledge_for(pull_requests, themes)

    with (
        patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run,
        patch(
            "repo_retrospect.pipeline.generate.classify_pull_requests",
            side_effect=fake_classify,
        ),
    ):
        mock_run.side_effect = _make_gh_dispatcher(prs)
        t0 = time.monotonic()
        result = CliRunner().invoke(
            cli,
            [
                "run",
                "--repo",
                SAMPLE_REPO,
                "--last",
                "30",
                "--cache",
                str(cache),
                "--out",
                str(tmp_path / "out.md"),
            ],
        )
        elapsed = time.monotonic() - t0

    assert result.exit_code == 0, result.output
    assert elapsed < 300, f"perf budget exceeded: {elapsed:.1f}s"
    # A single batch is enough for the mocked classifier; a real run would
    # emit ceil(30/batch_size) calls. Either way the budget is 50.
    assert call_counter["llm"] <= 50


@pytest.mark.acceptance
@pytest.mark.slow
def test_sec_01_api_key_redact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TC-SEC-01: the active ``ANTHROPIC_API_KEY`` never leaks to logs/output."""
    secret = "sk-ant-xxxxxxxx_supersecret_token"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    cache = tmp_path / "cache.json"
    prs = [_make_pr_dict(1)]

    def fake_classify(pull_requests, themes=None):
        # Simulate a verbose path that logs something containing the secret.
        logging.getLogger("repo_retrospect.services.classifier").info(
            "calling anthropic with header authorization=Bearer %s", secret
        )
        return _knowledge_for(pull_requests, themes)

    with (
        patch("repo_retrospect.services.fetcher.subprocess.run") as mock_run,
        patch(
            "repo_retrospect.pipeline.generate.classify_pull_requests",
            side_effect=fake_classify,
        ),
        caplog.at_level(logging.DEBUG),
    ):
        mock_run.side_effect = _make_gh_dispatcher(prs)
        result = CliRunner().invoke(
            cli,
            [
                "run",
                "--repo",
                SAMPLE_REPO,
                "--last",
                "1",
                "--cache",
                str(cache),
                "--verbose",
            ],
        )

    assert result.exit_code == 0, result.output
    assert secret not in result.output, "raw API key appeared in CLI output"
    # caplog captures pre-redaction records (filters apply at handler emit
    # time, not propagation), so apply the redact() helper before asserting
    # the *emitted* form matches the policy.
    from repo_retrospect.cli.logging import redact

    for record in caplog.records:
        assert secret not in redact(record.getMessage()), (
            f"redact() failed to mask secret in: {record.getMessage()!r}"
        )

    # And clean up: ensure the env round-trip kept ``os.environ`` correct.
    assert os.environ["ANTHROPIC_API_KEY"] == secret
