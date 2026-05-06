"""tests.test_acceptance — エンドツーエンド受け入れテスト (TC-AC-01〜06)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from session_retrospecter.cli.main import main
from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import Knowledge

# ---------------------------------------------------------------------------
# module-level marks — -m "not slow" でスキップ、-m "acceptance" で実行
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.acceptance, pytest.mark.slow]

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"session://[A-Za-z0-9_\-]+#L\d+")

_CASSETTE_DIR = str(Path(__file__).parent / "fixtures" / "cassettes")

_SAMPLE_SECRETS_JSONL = (
    Path(__file__).parent.parent
    / "docs"
    / "contracts"
    / "fixtures"
    / "sessions"
    / "sample_with_secrets.jsonl"
)

# ---------------------------------------------------------------------------
# VCR 設定 (module-scope)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vcr_cassette_dir() -> str:
    return _CASSETTE_DIR


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    return {
        "record_mode": "none",
        "match_on": ["method", "uri"],
    }


# ---------------------------------------------------------------------------
# セッション fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_with_tokens(tmp_path: Path) -> Path:
    """correction context に API トークンを含むセッションファイル (sess_tokens.jsonl)."""
    events = [
        {
            "type": "assistant",
            "text": "Using sk-ant-abc123XYZ_456def to authenticate.",
            "lineNo": 1,
        },
        {"type": "user", "text": "no, don't hardcode API keys", "lineNo": 2},
    ]
    path = tmp_path / "sessions" / "sess_tokens.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


@pytest.fixture()
def session_with_paths(tmp_path: Path) -> Path:
    """correction context にファイルパスを含むセッションファイル (sess_paths.jsonl)."""
    events = [
        {
            "type": "assistant",
            "text": "Saved config to /home/testuser/project/config.py.",
            "lineNo": 1,
        },
        {
            "type": "user",
            "text": "no, don't use /home/testuser path — use a relative path",
            "lineNo": 2,
        },
    ]
    path = tmp_path / "sessions" / "sess_paths.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


@pytest.fixture()
def fixture_session_with_secrets(tmp_path: Path) -> Path:
    """TC-AC-06: sample_with_secrets.jsonl を tmp_path にコピーして返す."""
    dest = tmp_path / "sessions" / "secrets_session.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_SAMPLE_SECRETS_JSONL.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# テストダブル
# ---------------------------------------------------------------------------


def _fake_classify_with_context(
    candidates: list[ExtractionCandidate],
    *,
    themes: list[str] | None = None,
    cached_citations: set[str] | None = None,
) -> list[Knowledge]:
    """Stub: example=c.context でコンテキストをそのまま Knowledge に通す."""
    _cached = cached_citations or set()
    return [
        Knowledge(
            rule=f"rule for {c.citation}",
            anti_pattern="stub anti_pattern",
            example=c.context,
            sources=[c.citation],
            themes=[c.kind],
        )
        for c in candidates
        if c.citation not in _cached
    ]


# ---------------------------------------------------------------------------
# TC-AC-01: default 実行で出力に token 文字列が含まれない (R-PRIVACY-1)
# ---------------------------------------------------------------------------


def test_default_redacts_tokens(
    tmp_path: Path,
    session_with_tokens: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-AC-01: mask_tokens=True (default) で出力に sk-ant-/ghp_/Bearer が現れない."""
    # Arrange
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _fake_classify_with_context,
    )
    runner = CliRunner()
    cache = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_with_tokens),
            "--cache",
            str(cache),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert "sk-ant-" not in content
    assert "ghp_" not in content
    assert "Bearer " not in content


# ---------------------------------------------------------------------------
# TC-AC-02: default 実行で出力にパスが含まれる (R-PRIVACY-2 default OFF 確認)
# ---------------------------------------------------------------------------


def test_default_includes_paths(
    tmp_path: Path,
    session_with_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-AC-02: mask_paths=False (default) で出力に /home/testuser が現れる."""
    # Arrange
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _fake_classify_with_context,
    )
    runner = CliRunner()
    cache = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_with_paths),
            "--cache",
            str(cache),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert "/home/testuser" in content


# ---------------------------------------------------------------------------
# TC-AC-03: --redact-paths 付与でパスが含まれない
# ---------------------------------------------------------------------------


def test_redact_paths_removes_paths(
    tmp_path: Path,
    session_with_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-AC-03: --redact-paths 付与で出力に /home/testuser が現れない."""
    # Arrange
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _fake_classify_with_context,
    )
    runner = CliRunner()
    cache = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_with_paths),
            "--cache",
            str(cache),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
            "--redact-paths",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert "/home/testuser" not in content


# ---------------------------------------------------------------------------
# TC-AC-04: 出力 md に session:// 形式の出典が必ず含まれる (R-OUTPUT-1)
# ---------------------------------------------------------------------------


def test_output_contains_citation(
    tmp_path: Path,
    session_with_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-AC-04: 出力 md に session://<id>#L<n> 形式の出典 URI が含まれる (ADR-0001)."""
    # Arrange
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _fake_classify_with_context,
    )
    runner = CliRunner()
    cache = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_with_paths),
            "--cache",
            str(cache),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert _CITATION_RE.search(content), (
        f"出典 URI session://<id>#L<n> が出力 md に見つからない: {content!r}"
    )


# ---------------------------------------------------------------------------
# TC-AC-05: --help が 4 サブコマンドを表示する (R-AC-1)
# ---------------------------------------------------------------------------


def test_help_shows_four_subcommands() -> None:
    """TC-AC-05: session-retrospecter --help が run/fetch/extract/generate を表示する."""
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(main, ["--help"])

    # Assert
    assert result.exit_code == 0
    for sub in ("run", "fetch", "extract", "generate"):
        assert sub in result.output, f"サブコマンド {sub!r} が --help に表示されない"


# ---------------------------------------------------------------------------
# TC-AC-06 (任意): sample_with_secrets.jsonl の e2e テスト
# ---------------------------------------------------------------------------


def test_end_to_end_with_secrets_fixture(
    tmp_path: Path,
    fixture_session_with_secrets: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-AC-06: sample_with_secrets.jsonl の e2e — トークンが出力に現れない (TC-RD-13)."""
    # Arrange
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setattr(
        "session_retrospecter.services.classifier.classify",
        _fake_classify_with_context,
    )
    runner = CliRunner()
    cache = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(fixture_session_with_secrets),
            "--cache",
            str(cache),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    content = out.read_text(encoding="utf-8")
    assert "sk-ant-" not in content
    assert "ghp_" not in content
