"""Test-suite-wide fixtures.

ADR-0005 changed ``run_generate`` so that any cache with PRs / commits whose
URLs are not yet covered by ``Knowledge.source_urls`` triggers the classifier
again — even if ``cache.knowledge`` is non-None. To keep older tests focused
on the bits they actually exercise (and to prevent accidental real Anthropic
API calls when ANTHROPIC_API_KEY happens to be set in the dev shell), we
autouse-stub the leaf services that talk to the outside world:

- ``services.fetcher.fetch_loose_commits`` (added with the loose-commit feature)
- ``services.classifier.classify_pull_requests``
- ``services.classifier.classify_commits``

Stubs are also patched at the call sites in ``pipeline.fetch`` /
``pipeline.generate`` because Python re-binds the name at import time.

Tests that *do* want to exercise these functions override the patch
explicitly inside the test body — patches there take precedence because
``unittest.mock.patch`` stacks LIFO.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_external_services():
    with (
        patch(
            "repo_retrospecter.services.fetcher.fetch_loose_commits",
            return_value=[],
        ),
        patch(
            "repo_retrospecter.pipeline.fetch.fetch_loose_commits",
            return_value=[],
        ),
        patch(
            "repo_retrospecter.services.classifier.classify_pull_requests",
            return_value=[],
        ),
        patch(
            "repo_retrospecter.pipeline.generate.classify_pull_requests",
            return_value=[],
        ),
        patch(
            "repo_retrospecter.services.classifier.classify_commits",
            return_value=[],
        ),
        patch(
            "repo_retrospecter.pipeline.generate.classify_commits",
            return_value=[],
        ),
    ):
        yield
