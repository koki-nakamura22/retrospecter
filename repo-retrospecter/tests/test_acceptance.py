"""Acceptance tests (pending) corresponding to docs/test-cases/acceptance.md.

These tests are stubs to satisfy DoR gate item 10 (受け入れテストコードが pending 状態で記述 + コンパイル通過).
Remove `pytest.skip` and implement once the corresponding feature lands.
"""

from __future__ import annotations

import pytest


@pytest.mark.acceptance
def test_f1_01_fetch_by_count() -> None:
    pytest.skip("pending: F1 fetch (--last) not implemented yet")


@pytest.mark.acceptance
def test_f1_02_fetch_by_since() -> None:
    pytest.skip("pending: F1 fetch (--since) not implemented yet")


@pytest.mark.acceptance
def test_f1_03_no_auth_error() -> None:
    pytest.skip("pending: F1 auth error handling not implemented yet")


@pytest.mark.acceptance
def test_f2_01_default_themes() -> None:
    pytest.skip("pending: F2 default-themes classification not implemented yet")


@pytest.mark.acceptance
def test_f2_02_custom_themes() -> None:
    pytest.skip("pending: F2 custom-themes classification not implemented yet")


@pytest.mark.acceptance
def test_f3_01_human_markdown_output() -> None:
    pytest.skip("pending: F3 human renderer not implemented yet")


@pytest.mark.acceptance
def test_f4_01_ai_structured_output() -> None:
    pytest.skip("pending: F4 ai renderer not implemented yet")


@pytest.mark.acceptance
def test_f4_02_ai_citation_required() -> None:
    pytest.skip("pending: F4 citation enforcement not implemented yet")


@pytest.mark.acceptance
def test_perf_01_30pr_within_5min() -> None:
    pytest.skip("pending: NFR perf benchmark not implemented yet")


@pytest.mark.acceptance
def test_sec_01_api_key_redact() -> None:
    pytest.skip("pending: NFR security log redaction not implemented yet")
