"""Theme model: classification axis for PR review knowledge.

OQ-02 default: fixed 5 canonical themes; users may override via config.
The `Theme` alias accepts any string, but `CanonicalTheme` documents the
default set used when no config override is supplied.
"""

from __future__ import annotations

from typing import Literal

CanonicalTheme = Literal[
    "design_decision",
    "review_rule",
    "bug_pattern",
    "refactor",
    "other",
]

CANONICAL_THEMES: tuple[CanonicalTheme, ...] = (
    "design_decision",
    "review_rule",
    "bug_pattern",
    "refactor",
    "other",
)

Theme = str

__all__ = ["CANONICAL_THEMES", "CanonicalTheme", "Theme"]
