"""Human-facing retrospective renderer (PRD F3 / TC-F3-01).

Produces a Markdown note targeted at the weekly retrospective audience:

- "# 振り返り" header,
- "## 主要設計判断" — Knowledge tagged ``design_decision``,
- "## 頻出レビュー指摘 Top N" — Knowledge tagged ``review_rule``,
  ranked by source-URL count (a proxy for "how many places this came
  up").

Each item carries the originating PR / comment URL so the reader can
jump back to the conversation. Empty fields are skipped per
decision-defaults.md §null/欠損値 rather than printed as blanks.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from repo_retrospecter.models.cache import CacheFile
from repo_retrospecter.models.knowledge import Knowledge

DEFAULT_TOP_N: int = 5
DESIGN_DECISION_THEME: str = "design_decision"
REVIEW_RULE_THEME: str = "review_rule"


class HumanRenderer:
    """Render a ``CacheFile`` as a human retrospective note."""

    template_name: str = "human.md.j2"

    def __init__(self, *, top_n: int = DEFAULT_TOP_N) -> None:
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self.top_n = top_n
        self._env = Environment(
            loader=PackageLoader("repo_retrospecter.services.renderer", "templates"),
            # Markdown output: html escaping would corrupt the body.
            autoescape=select_autoescape(disabled_extensions=("md", "j2")),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def render(self, cache: CacheFile, out_path: Path) -> None:
        knowledge = list(cache.knowledge or [])
        design_decisions = [k for k in knowledge if DESIGN_DECISION_THEME in k.themes]
        review_rules = [k for k in knowledge if REVIEW_RULE_THEME in k.themes]
        # Rank "frequent" review issues by how many sources cite them — a
        # rough proxy that doesn't require a separate aggregation pass.
        top_review_rules: list[Knowledge] = sorted(
            review_rules, key=lambda k: len(k.source_urls), reverse=True
        )[: self.top_n]

        template = self._env.get_template(self.template_name)
        text = template.render(
            cache=cache,
            knowledge=knowledge,
            design_decisions=design_decisions,
            top_review_rules=top_review_rules,
            top_n=self.top_n,
        )
        if not text.endswith("\n"):
            text += "\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8", newline="\n")


__all__ = ["DEFAULT_TOP_N", "HumanRenderer"]
