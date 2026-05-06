"""Shared test fixtures for session-retrospecter tests."""

from __future__ import annotations

import pytest

from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import Knowledge


@pytest.fixture()
def fake_classify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out services.classifier.classify so tests never hit the real Anthropic API.

    Returns a single Knowledge derived from the first candidate's citation.
    Import and call this fixture in any test module that exercises pipeline/CLI
    code that internally calls services.classifier.classify.
    """

    def _stub(
        candidates: list[ExtractionCandidate],
        *,
        themes: list[str] | None = None,
        cached_citations: set[str] | None = None,
    ) -> list[Knowledge]:
        _cached = cached_citations or set()
        new = [c for c in candidates if c.citation not in _cached]
        return [
            Knowledge(
                rule=f"stub rule for {c.citation}",
                anti_pattern="stub anti_pattern",
                example="stub example",
                sources=[c.citation],
                themes=[c.kind],
            )
            for c in new
        ]

    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _stub,
    )


@pytest.fixture()
def sample_candidate() -> ExtractionCandidate:
    """A minimal ExtractionCandidate for classifier tests."""
    return ExtractionCandidate(
        kind="correction",
        session_id="test-session",
        line_no=5,
        context="User: stop\nAssistant: understood, halting.",
        citation="session://test-session#L5",
    )


@pytest.fixture()
def vcr_cassette_name(request: pytest.FixtureRequest) -> str:
    """Use cassette_name from @pytest.mark.vcr(cassette_name=...) if provided."""
    marker = request.node.get_closest_marker("vcr")
    if marker and "cassette_name" in marker.kwargs:
        return str(marker.kwargs["cassette_name"])
    return str(request.node.name)
