"""Test-suite-wide fixtures.

The acceptance + pipeline tests pre-date the loose-commit feature
(2026-05-04 spec follow-up). They mock ``services.fetcher.fetch_pull_requests``
explicitly but assume any other ``gh`` calls don't happen. To keep those
tests focused on PR-only behavior, we autouse-mock ``fetch_loose_commits``
to return an empty list. Tests that exercise loose-commit behavior should
override this fixture by patching the same name explicitly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_fetch_loose_commits():
    with patch(
        "repo_retrospecter.services.fetcher.fetch_loose_commits",
        return_value=[],
    ), patch(
        "repo_retrospecter.pipeline.fetch.fetch_loose_commits",
        return_value=[],
    ):
        yield
