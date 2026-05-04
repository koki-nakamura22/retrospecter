"""Unit tests for repo_retrospecter.services.renderer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_retrospecter.models.cache import CACHE_SCHEMA_VERSION, CacheFile
from repo_retrospecter.models.knowledge import Knowledge
from repo_retrospecter.models.pull_request import PullRequest
from repo_retrospecter.services.renderer import (
    AiRenderer,
    HumanRenderer,
    Renderer,
    get_renderer,
)
from repo_retrospecter.services.renderer.ai import GITHUB_URL_PREFIX, _has_github_source
from repo_retrospecter.services.renderer.human import (
    DEFAULT_TOP_N,
    DESIGN_DECISION_THEME,
    REVIEW_RULE_THEME,
)

# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pr(number: int = 1, *, title: str = "t", url: str | None = None) -> PullRequest:
    return PullRequest(
        number=number,
        title=title,
        body="",
        author="alice",
        merged_at=datetime(2026, 5, 3, tzinfo=UTC),
        url=url or f"https://github.com/owner/repo/pull/{number}",
    )


def _make_knowledge(
    *,
    rule: str = "Prefer dependency injection",
    anti_pattern: str = "Global singletons",
    example: str = "def f(client): ...",
    source_urls: list[str] | None = None,
    themes: list[str] | None = None,
) -> Knowledge:
    return Knowledge(
        rule=rule,
        anti_pattern=anti_pattern,
        example=example,
        source_urls=source_urls if source_urls is not None else ["https://github.com/owner/repo/pull/1"],
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
# get_renderer factory
# ---------------------------------------------------------------------------


class TestGetRenderer:
    def test_returns_human_renderer_for_human_name(self) -> None:
        # Arrange / Act
        renderer = get_renderer("human")

        # Assert
        assert isinstance(renderer, HumanRenderer)

    def test_returns_ai_renderer_for_ai_name(self) -> None:
        renderer = get_renderer("ai")

        assert isinstance(renderer, AiRenderer)

    def test_unknown_name_raises_value_error(self) -> None:
        # Bypass the Literal hint to simulate a CLI-layer programming error.
        with pytest.raises(ValueError, match="unknown renderer"):
            get_renderer("skill")  # type: ignore[arg-type]

    def test_human_renderer_satisfies_protocol(self) -> None:
        # Renderer is runtime_checkable; isinstance must agree with structural typing.
        assert isinstance(get_renderer("human"), Renderer)

    def test_ai_renderer_satisfies_protocol(self) -> None:
        assert isinstance(get_renderer("ai"), Renderer)


# ---------------------------------------------------------------------------
# HumanRenderer — TC-F3-01 required headings
# ---------------------------------------------------------------------------


class TestHumanRendererHeadings:
    def test_emits_top_level_retrospective_heading(self, tmp_path: Path) -> None:
        # Arrange
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        # Act
        HumanRenderer().render(cache, out)

        # Assert (TC-F3-01)
        text = out.read_text(encoding="utf-8")
        assert "# 振り返り" in text

    def test_emits_design_decisions_section_heading(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert "## 主要設計判断" in out.read_text(encoding="utf-8")

    def test_emits_top_review_rules_section_heading(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert "## 頻出レビュー指摘 Top" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HumanRenderer — content & equivalence partitioning
# ---------------------------------------------------------------------------


class TestHumanRendererContent:
    def test_includes_pr_url_in_pr_listing(self, tmp_path: Path) -> None:
        cache = _make_cache(
            pull_requests=[_make_pr(number=42, url="https://github.com/o/r/pull/42")],
            knowledge=[_make_knowledge()],
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert "https://github.com/o/r/pull/42" in out.read_text(encoding="utf-8")

    def test_design_decision_item_includes_source_url(self, tmp_path: Path) -> None:
        # TC-F3-01: each item must link back to the originating PR.
        url = "https://github.com/owner/repo/pull/7"
        cache = _make_cache(
            knowledge=[_make_knowledge(source_urls=[url], themes=["design_decision"])],
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert url in out.read_text(encoding="utf-8")

    def test_only_design_decision_themed_items_appear_in_decisions_section(
        self, tmp_path: Path
    ) -> None:
        # Equivalence: items without 'design_decision' theme go elsewhere.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="kept-rule", themes=["design_decision"]),
                _make_knowledge(rule="dropped-rule", themes=["bug_pattern"]),
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        # Split on the next section to bound the search to the decisions block.
        decisions_block = text.split("## 頻出レビュー指摘")[0]
        assert "kept-rule" in decisions_block
        assert "dropped-rule" not in decisions_block

    def test_review_rules_ranked_by_source_url_count_desc(self, tmp_path: Path) -> None:
        many_urls = [f"https://github.com/o/r/pull/{i}" for i in range(1, 6)]
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="few-sources", source_urls=["https://github.com/o/r/pull/1"], themes=["review_rule"]),
                _make_knowledge(rule="many-sources", source_urls=many_urls, themes=["review_rule"]),
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert text.index("many-sources") < text.index("few-sources")

    def test_top_n_truncates_review_rules(self, tmp_path: Path) -> None:
        # Boundary: top_n=2 with 3 review_rule items keeps only 2.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule=f"r{i}", source_urls=[f"https://github.com/o/r/pull/{i}"], themes=["review_rule"])
                for i in range(1, 4)
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer(top_n=2).render(cache, out)

        text = out.read_text(encoding="utf-8")
        review_block = text.split("## 取得した PR 一覧")[0].split("## 頻出レビュー指摘")[1]
        # Exactly two enumerated review rule items appear (lines starting "1. ", "2. ").
        assert "1. **" in review_block
        assert "2. **" in review_block
        assert "3. **" not in review_block

    def test_top_n_heading_includes_n_value(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge(themes=["review_rule"])])
        out = tmp_path / "out.md"

        HumanRenderer(top_n=3).render(cache, out)

        assert "Top 3" in out.read_text(encoding="utf-8")

    def test_default_top_n_constant_is_five(self) -> None:
        # Pin the architecture-stated default; if someone changes it, tests fail loudly.
        assert DEFAULT_TOP_N == 5

    def test_design_decision_theme_constant_is_canonical(self) -> None:
        assert DESIGN_DECISION_THEME == "design_decision"

    def test_review_rule_theme_constant_is_canonical(self) -> None:
        assert REVIEW_RULE_THEME == "review_rule"

    def test_invalid_top_n_raises(self) -> None:
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            HumanRenderer(top_n=0)

    def test_negative_top_n_raises(self) -> None:
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            HumanRenderer(top_n=-1)

    def test_renders_with_no_knowledge(self, tmp_path: Path) -> None:
        # Equivalence: knowledge=None (fetch-only run).
        cache = _make_cache(knowledge=None)
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "# 振り返り" in text
        assert "## 主要設計判断" in text
        assert "## 頻出レビュー指摘 Top" in text

    def test_renders_with_empty_knowledge_list(self, tmp_path: Path) -> None:
        # Equivalence: knowledge=[] (classifier ran but yielded nothing).
        cache = _make_cache(knowledge=[])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "# 振り返り" in text

    def test_renders_with_no_pull_requests(self, tmp_path: Path) -> None:
        cache = _make_cache(pull_requests=[], knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "# 振り返り" in text
        assert "対象 PR 数**: 0" in text

    def test_omits_anti_pattern_line_when_empty(self, tmp_path: Path) -> None:
        # decision-defaults.md §null/欠損値: empty fields are not printed as blanks.
        cache = _make_cache(
            knowledge=[_make_knowledge(anti_pattern="", themes=["design_decision"])]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert "避けるべき" not in out.read_text(encoding="utf-8")

    def test_omits_example_line_when_empty(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(example="", themes=["design_decision"])]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        # The literal "**例**:" prefix should not appear when example is blank.
        assert "**例**" not in text


# ---------------------------------------------------------------------------
# HumanRenderer — file I/O
# ---------------------------------------------------------------------------


class TestHumanRendererIO:
    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "missing" / "deep" / "out.md"
        cache = _make_cache(knowledge=[_make_knowledge()])

        HumanRenderer().render(cache, out)

        assert out.is_file()

    def test_writes_utf8_without_bom(self, tmp_path: Path) -> None:
        cache = _make_cache(repo="所有者/リポジトリ", knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        data = out.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf")
        assert "所有者/リポジトリ" in out.read_text(encoding="utf-8")

    def test_uses_lf_line_endings(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert b"\r" not in out.read_bytes()

    def test_ends_with_trailing_newline(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert out.read_bytes().endswith(b"\n")

    def test_rendering_twice_is_idempotent(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out1 = tmp_path / "first.md"
        out2 = tmp_path / "second.md"

        HumanRenderer().render(cache, out1)
        HumanRenderer().render(cache, out2)

        assert out1.read_bytes() == out2.read_bytes()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        out.write_text("stale content", encoding="utf-8")
        cache = _make_cache(knowledge=[_make_knowledge()])

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "stale content" not in text
        assert "# 振り返り" in text


# ---------------------------------------------------------------------------
# AiRenderer — TC-F4-01 structure markers
# ---------------------------------------------------------------------------


class TestAiRendererStructure:
    def test_each_item_has_rule_marker(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(rule="r1"), _make_knowledge(rule="r2")]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert text.count("### Rule:") == 2

    def test_includes_anti_pattern_marker(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(anti_pattern="don't do this")]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert "**Anti-pattern**:" in out.read_text(encoding="utf-8")

    def test_includes_code_fence_for_example(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(example="snippet()")]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "```" in text
        assert "snippet()" in text

    def test_includes_themes_when_present(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(themes=["design_decision", "review_rule"])]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "design_decision" in text
        assert "review_rule" in text

    def test_omits_anti_pattern_marker_when_empty(self, tmp_path: Path) -> None:
        # decision-defaults.md §null/欠損値: blank fields are not printed.
        cache = _make_cache(
            knowledge=[_make_knowledge(anti_pattern="")]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert "**Anti-pattern**:" not in out.read_text(encoding="utf-8")

    def test_omits_example_block_when_empty(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(example="")]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "**Example**:" not in text


# ---------------------------------------------------------------------------
# AiRenderer — TC-F4-02 GitHub URL filtering
# ---------------------------------------------------------------------------


class TestAiRendererCitationFiltering:
    def test_drops_record_with_no_source_urls(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="dropped", source_urls=[]),
                _make_knowledge(rule="kept", source_urls=["https://github.com/o/r/pull/1"]),
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "kept" in text
        assert "dropped" not in text

    def test_drops_record_whose_urls_are_all_non_github(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="dropped", source_urls=["https://example.com/a", "https://gitlab.com/b"]),
                _make_knowledge(rule="kept", source_urls=["https://github.com/o/r/pull/1"]),
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "kept" in text
        assert "dropped" not in text

    def test_keeps_record_with_mixed_url_origins(self, tmp_path: Path) -> None:
        # Equivalence: at least one https://github.com/ URL is sufficient.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(
                    rule="mixed",
                    source_urls=["https://example.com/x", "https://github.com/o/r/pull/9"],
                )
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert "mixed" in out.read_text(encoding="utf-8")

    def test_every_emitted_item_carries_a_github_url(self, tmp_path: Path) -> None:
        # TC-F4-02 (positive form): the file must not have a Rule line that
        # isn't followed by a github.com URL somewhere before the next Rule.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule=f"r{i}", source_urls=[f"https://github.com/o/r/pull/{i}"])
                for i in range(1, 4)
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        # Split into per-rule chunks; each must mention github.com.
        chunks = text.split("### Rule:")[1:]  # discard preamble
        assert chunks, "expected at least one rule chunk in output"
        for chunk in chunks:
            assert "https://github.com/" in chunk

    def test_all_records_filtered_yields_empty_section(self, tmp_path: Path) -> None:
        # Boundary: every record dropped → no Rule lines at all.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="a", source_urls=[]),
                _make_knowledge(rule="b", source_urls=["https://example.com/x"]),
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "### Rule:" not in text
        assert "ナレッジ数**: 0" in text


# ---------------------------------------------------------------------------
# AiRenderer — file I/O
# ---------------------------------------------------------------------------


class TestAiRendererIO:
    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "deeply" / "nested" / "ai.md"
        cache = _make_cache(knowledge=[_make_knowledge()])

        AiRenderer().render(cache, out)

        assert out.is_file()

    def test_uses_lf_line_endings(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert b"\r" not in out.read_bytes()

    def test_ends_with_trailing_newline(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert out.read_bytes().endswith(b"\n")

    def test_writes_utf8_without_bom(self, tmp_path: Path) -> None:
        cache = _make_cache(repo="所有者/リポジトリ", knowledge=[_make_knowledge()])
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        data = out.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf")
        assert "所有者/リポジトリ" in out.read_text(encoding="utf-8")

    def test_renders_with_no_knowledge(self, tmp_path: Path) -> None:
        cache = _make_cache(knowledge=None)
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "# AI 向けナレッジ" in text
        assert "### Rule:" not in text


# ---------------------------------------------------------------------------
# _has_github_source helper
# ---------------------------------------------------------------------------


class TestHasGithubSource:
    def test_true_for_record_with_github_url(self) -> None:
        assert _has_github_source(_make_knowledge(source_urls=["https://github.com/o/r/pull/1"]))

    def test_false_for_record_with_no_urls(self) -> None:
        assert not _has_github_source(_make_knowledge(source_urls=[]))

    def test_false_for_record_with_only_non_github_urls(self) -> None:
        assert not _has_github_source(
            _make_knowledge(source_urls=["https://gitlab.com/x", "https://example.com/y"])
        )

    def test_true_when_any_url_is_github(self) -> None:
        # Equivalence: at least one match is enough.
        assert _has_github_source(
            _make_knowledge(source_urls=["https://example.com/x", "https://github.com/o/r/pull/9"])
        )

    def test_github_url_prefix_constant(self) -> None:
        assert GITHUB_URL_PREFIX == "https://github.com/"

    def test_false_for_url_that_only_contains_github(self) -> None:
        # The check must be a prefix match, not substring — a URL like
        # `https://attacker.com/?u=https://github.com/foo` should not pass.
        assert not _has_github_source(
            _make_knowledge(source_urls=["https://attacker.com/?u=https://github.com/foo"])
        )

    def test_false_for_http_scheme_github_url(self) -> None:
        # Security-relevant: enforce HTTPS. A plain http:// URL must be rejected
        # so that an attacker-controlled redirect can't masquerade as a citation.
        assert not _has_github_source(
            _make_knowledge(source_urls=["http://github.com/owner/repo/pull/1"])
        )

    def test_false_for_github_subdomain_url(self) -> None:
        # Boundary: only the canonical https://github.com/ host counts. A
        # subdomain like api.github.com is a different origin and must not pass.
        assert not _has_github_source(
            _make_knowledge(source_urls=["https://api.github.com/repos/owner/repo/pulls/1"])
        )


# ---------------------------------------------------------------------------
# Renderer Protocol — runtime structural typing
# ---------------------------------------------------------------------------


class TestRendererProtocol:
    def test_object_without_render_method_is_not_a_renderer(self) -> None:
        # runtime_checkable Protocol must reject objects missing `render`.
        class Bare:
            pass

        assert not isinstance(Bare(), Renderer)


# ---------------------------------------------------------------------------
# HumanRenderer — additional boundary / multi-theme cases
# ---------------------------------------------------------------------------


class TestHumanRendererBoundaries:
    def test_top_n_one_keeps_only_first_review_rule(self, tmp_path: Path) -> None:
        # Boundary: minimum valid top_n. With 2 review_rule items, exactly 1 survives.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(
                    rule="winner",
                    source_urls=["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"],
                    themes=["review_rule"],
                ),
                _make_knowledge(
                    rule="loser",
                    source_urls=["https://github.com/o/r/pull/3"],
                    themes=["review_rule"],
                ),
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer(top_n=1).render(cache, out)

        text = out.read_text(encoding="utf-8")
        review_block = text.split("## 取得した PR 一覧")[0].split("## 頻出レビュー指摘")[1]
        assert "winner" in review_block
        assert "loser" not in review_block

    def test_top_n_larger_than_review_rule_count_keeps_all(self, tmp_path: Path) -> None:
        # Boundary: top_n > available items → no truncation, no padding.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="r1", themes=["review_rule"]),
                _make_knowledge(rule="r2", themes=["review_rule"]),
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer(top_n=99).render(cache, out)

        text = out.read_text(encoding="utf-8")
        review_block = text.split("## 取得した PR 一覧")[0].split("## 頻出レビュー指摘")[1]
        assert "r1" in review_block
        assert "r2" in review_block
        assert "3. **" not in review_block

    def test_multi_theme_knowledge_appears_in_both_sections(self, tmp_path: Path) -> None:
        # An item tagged with both themes is intentionally listed twice — it
        # is both a design decision AND a frequent review rule.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(
                    rule="dual-tagged",
                    themes=["design_decision", "review_rule"],
                )
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        decisions_block = text.split("## 頻出レビュー指摘")[0]
        review_block = text.split("## 取得した PR 一覧")[0].split("## 頻出レビュー指摘")[1]
        assert "dual-tagged" in decisions_block
        assert "dual-tagged" in review_block

    def test_empty_rule_falls_back_to_placeholder(self, tmp_path: Path) -> None:
        # decision-defaults.md §null/欠損値: empty string must not produce a
        # blank heading; the template substitutes "(無題)".
        cache = _make_cache(
            knowledge=[_make_knowledge(rule="", themes=["design_decision"])]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        decisions_block = out.read_text(encoding="utf-8").split("## 頻出レビュー指摘")[0]
        assert "(無題)" in decisions_block

    def test_no_design_decisions_renders_fallback_message(self, tmp_path: Path) -> None:
        # Equivalence: design_decisions == [] but knowledge non-empty.
        cache = _make_cache(
            knowledge=[_make_knowledge(rule="r", themes=["review_rule"])]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        decisions_block = out.read_text(encoding="utf-8").split("## 頻出レビュー指摘")[0]
        assert "該当する設計判断は抽出されませんでした" in decisions_block

    def test_no_review_rules_renders_fallback_message(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[_make_knowledge(rule="r", themes=["design_decision"])]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        review_block = text.split("## 取得した PR 一覧")[0].split("## 頻出レビュー指摘")[1]
        assert "頻出レビュー指摘は抽出されませんでした" in review_block

    def test_knowledge_count_includes_unrelated_themes(self, tmp_path: Path) -> None:
        # The抽出ナレッジ数 metric counts ALL knowledge, even items whose
        # themes don't surface in any visible section (bug_pattern here).
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="a", themes=["design_decision"]),
                _make_knowledge(rule="b", themes=["bug_pattern"]),
                _make_knowledge(rule="c", themes=["review_rule"]),
            ]
        )
        out = tmp_path / "out.md"

        HumanRenderer().render(cache, out)

        assert "抽出ナレッジ数**: 3" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AiRenderer — additional content / equivalence cases
# ---------------------------------------------------------------------------


class TestAiRendererAdditional:
    def test_renders_with_empty_knowledge_list(self, tmp_path: Path) -> None:
        # Equivalence with knowledge=None: both produce the no-knowledge state.
        cache = _make_cache(knowledge=[])
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert "### Rule:" not in text
        assert "ナレッジ数**: 0" in text

    def test_omits_themes_line_when_themes_empty(self, tmp_path: Path) -> None:
        # decision-defaults.md §null/欠損値: empty themes list → no "Themes" line.
        # Construct Knowledge directly so the helper doesn't substitute its default.
        cache = _make_cache(
            knowledge=[
                Knowledge(
                    rule="r",
                    anti_pattern="",
                    example="",
                    source_urls=["https://github.com/o/r/pull/1"],
                    themes=[],
                )
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert "**Themes**" not in out.read_text(encoding="utf-8")

    def test_includes_schema_version_in_output(self, tmp_path: Path) -> None:
        # Downstream consumers key off schema_version; it must round-trip.
        cache = _make_cache(knowledge=[_make_knowledge()])
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert f"**schema_version**: {CACHE_SCHEMA_VERSION}" in out.read_text(encoding="utf-8")

    def test_preserves_input_order_after_filtering(self, tmp_path: Path) -> None:
        # The renderer is order-preserving on the kept subset; consumers rely
        # on classifier-emitted ordering for ranking-by-recency.
        cache = _make_cache(
            knowledge=[
                _make_knowledge(rule="first-kept", source_urls=["https://github.com/o/r/pull/1"]),
                _make_knowledge(rule="dropped", source_urls=[]),
                _make_knowledge(rule="second-kept", source_urls=["https://github.com/o/r/pull/2"]),
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        text = out.read_text(encoding="utf-8")
        assert text.index("first-kept") < text.index("second-kept")
        assert "dropped" not in text

    def test_empty_rule_falls_back_to_placeholder(self, tmp_path: Path) -> None:
        cache = _make_cache(
            knowledge=[
                _make_knowledge(
                    rule="",
                    source_urls=["https://github.com/o/r/pull/1"],
                )
            ]
        )
        out = tmp_path / "ai.md"

        AiRenderer().render(cache, out)

        assert "### Rule: (no rule)" in out.read_text(encoding="utf-8")
