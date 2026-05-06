"""tests.test_cli — CLI (click CliRunner) テスト (TC-CL-CLI-01〜06)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from freezegun import freeze_time

from session_retrospecter.cli.main import main
from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import Knowledge
from session_retrospecter.models.summary import FetchSummary
from session_retrospecter.models.target import TargetSpec

# ---------------------------------------------------------------------------
# Helpers / shared stubs
# ---------------------------------------------------------------------------

_CORRECTION_EVENTS: list[dict[str, str]] = [
    {"type": "assistant", "text": "Let me refactor the entire codebase."},
    {"type": "user", "text": "no don't do that"},
]

_FROZEN_DATE = "2024-06-15"


def _write_jsonl(path: Path, events: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def _fake_fetch_summary(spec: TargetSpec, cache_path: Path) -> FetchSummary:
    return FetchSummary(target=spec, session_count=0, event_count=0, cache_path=cache_path)


def _fake_classify(
    candidates: list[ExtractionCandidate],
    *,
    themes: list[str] | None = None,
    cached_citations: set[str] | None = None,
) -> list[Knowledge]:
    _cached = cached_citations or set()
    return [
        Knowledge(
            rule=f"rule for {c.citation}",
            anti_pattern="stub anti_pattern",
            example="stub example",
            sources=[c.citation],
            themes=[c.kind],
        )
        for c in candidates
        if c.citation not in _cached
    ]


@pytest.fixture()
def stub_fetch_run(monkeypatch: pytest.MonkeyPatch) -> list[TargetSpec]:
    """pipeline.fetch.run を stub し、呼ばれた TargetSpec を記録して返す."""
    captured: list[TargetSpec] = []

    def _stub(spec: TargetSpec, *, cache_path: Path, redact_opts: object = None) -> FetchSummary:
        captured.append(spec)
        return _fake_fetch_summary(spec, cache_path)

    monkeypatch.setattr("session_retrospecter.pipeline.fetch.run", _stub)
    return captured


# ---------------------------------------------------------------------------
# TC-CL-CLI-01: --help が 4 サブコマンドを表示する (R-CLI-1 / R-AC-1)
# ---------------------------------------------------------------------------


def test_help_shows_four_subcommands() -> None:
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(main, ["--help"])

    # Assert
    assert result.exit_code == 0
    for sub in ("run", "fetch", "extract", "generate"):
        assert sub in result.output, f"サブコマンド {sub!r} が --help に表示されない"


@pytest.mark.parametrize("sub", ["run", "fetch", "extract", "generate"])
def test_each_subcommand_help_exits_zero(sub: str) -> None:
    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(main, [sub, "--help"])

    # Assert
    assert result.exit_code == 0, f"{sub} --help が非ゼロ: {result.output}"


# ---------------------------------------------------------------------------
# TC-CL-CLI-03: 既存 md + --force なし + --append なし → ClickException + exit 1
# ---------------------------------------------------------------------------


def test_generate_existing_output_no_force_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — cache + 既存出力ファイル
    session_file = tmp_path / "project" / "sess01.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    from session_retrospecter.pipeline import extract, fetch

    fetch.run(TargetSpec(mode="session", session=session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)
    out.write_text("既存コンテンツ", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _fake_classify)

    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        ["generate", "--cache", str(cache_path), "--out", str(out), "--ai-out", str(ai_out)],
    )

    # Assert
    assert result.exit_code != 0
    # ClickException はエラーメッセージを出力する
    assert result.output


def test_run_existing_output_no_force_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess02.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"
    out.write_text("既存コンテンツ", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _fake_classify)

    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_file),
            "--cache",
            str(cache_path),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
        ],
    )

    # Assert
    assert result.exit_code != 0
    assert result.output  # エラーメッセージが出力される


def test_generate_with_force_overwrites_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — --force があれば既存ファイルを上書きできる
    session_file = tmp_path / "project" / "sess03.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    from session_retrospecter.pipeline import extract, fetch

    fetch.run(TargetSpec(mode="session", session=session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)
    out.write_text("古いコンテンツ", encoding="utf-8")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _fake_classify)

    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "generate",
            "--cache",
            str(cache_path),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert "generate 完了" in result.output
    # ファイルが上書きされた
    assert out.read_text(encoding="utf-8") != "古いコンテンツ"


def test_generate_with_append_skips_overwrite_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — --append があれば既存ファイルの上書きガードをスキップ
    session_file = tmp_path / "project" / "sessA.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    from session_retrospecter.pipeline import extract, fetch

    fetch.run(TargetSpec(mode="session", session=session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _fake_classify)

    runner = CliRunner()

    # 1回目: 正常生成
    runner.invoke(
        main,
        [
            "generate",
            "--cache",
            str(cache_path),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
        ],
    )

    # Act — 2回目: --append で再実行 (既存ファイルあり)
    result = runner.invoke(
        main,
        [
            "generate",
            "--cache",
            str(cache_path),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--append",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# TC-CL-CLI-04: ANTHROPIC_API_KEY 不在 → ClickException (generate / run のみ)
# ---------------------------------------------------------------------------


def test_generate_without_api_key_exits_with_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "generate",
            "--cache",
            str(tmp_path / "cache.json"),
            "--out",
            str(tmp_path / "out.md"),
            "--ai-out",
            str(tmp_path / "ai.md"),
        ],
    )

    # Assert
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_run_without_api_key_exits_with_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "run",
            "--session",
            str(session_file),
            "--cache",
            str(tmp_path / "cache.json"),
            "--out",
            str(tmp_path / "out.md"),
            "--ai-out",
            str(tmp_path / "ai.md"),
        ],
    )

    # Assert
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_fetch_without_api_key_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — LLM 不要の fetch は API key 不在でも動く
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        ["fetch", "--session", str(session_file), "--cache", str(tmp_path / "cache.json")],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert "fetch 完了" in result.output


def test_extract_without_api_key_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    from session_retrospecter.pipeline import fetch

    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    fetch.run(TargetSpec(mode="session", session=session_file), cache_path=cache_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()

    # Act
    result = runner.invoke(main, ["extract", "--cache", str(cache_path)])

    # Assert
    assert result.exit_code == 0, result.output
    assert "extract 完了" in result.output


# ---------------------------------------------------------------------------
# TC-CL-CLI-05: FetchError → ClickException 翻訳 / --since パース (R-INPUT-4)
# ---------------------------------------------------------------------------


def test_fetch_error_translated_to_click_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange — fetcher が FetchError を raise するよう stub
    from session_retrospecter.services.exceptions import FetchError

    def _raise_fetch_error(*args: object, **kwargs: object) -> list[object]:
        raise FetchError("JSONL 読み込みに失敗しました")

    monkeypatch.setattr("session_retrospecter.services.fetcher.read_target", _raise_fetch_error)
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        ["fetch", "--session", str(session_file), "--cache", str(tmp_path / "cache.json")],
    )

    # Assert
    assert result.exit_code != 0
    assert "JSONL 読み込みに失敗しました" in result.output


@freeze_time(_FROZEN_DATE)
def test_since_relative_days_builds_correct_target_spec(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--session",
            str(session_file),
            "--cache",
            str(tmp_path / "cache.json"),
            "--since",
            "7d",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert len(stub_fetch_run) == 1
    expected_since = date(2024, 6, 15) - timedelta(days=7)
    assert stub_fetch_run[0].since == expected_since


@freeze_time(_FROZEN_DATE)
def test_since_absolute_date_builds_correct_target_spec(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--session",
            str(session_file),
            "--cache",
            str(tmp_path / "cache.json"),
            "--since",
            "2024-01-15",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert len(stub_fetch_run) == 1
    assert stub_fetch_run[0].since == date(2024, 1, 15)


@freeze_time(_FROZEN_DATE)
def test_since_zero_days_returns_today(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange — 境界値: 0d は「今日から」= 今日の日付
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--session",
            str(session_file),
            "--cache",
            str(tmp_path / "cache.json"),
            "--since",
            "0d",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert stub_fetch_run[0].since == date(2024, 6, 15)


def test_since_invalid_format_exits_with_error(tmp_path: Path) -> None:
    # Arrange
    session_file = tmp_path / "project" / "sess.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--session",
            str(session_file),
            "--cache",
            str(tmp_path / "cache.json"),
            "--since",
            "not-a-date",
        ],
    )

    # Assert
    assert result.exit_code != 0


def test_fetch_without_target_exits_with_usageerror(tmp_path: Path) -> None:
    # Arrange — --project / --session / --all のいずれも指定しない
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        ["fetch", "--cache", str(tmp_path / "cache.json")],
    )

    # Assert
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# TC-CL-CLI-06: --exclude-projects が decode / encoded 両形式を受理 (OQ-06 / R-PRIVACY-4)
# ---------------------------------------------------------------------------


def test_exclude_projects_decode_form_normalised_to_encoded(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange — decode 形 /home/user/secret を指定
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--all",
            "--cache",
            str(tmp_path / "cache.json"),
            "--exclude-projects",
            "/home/user/secret",
        ],
    )

    # Assert — encoded-cwd 形 -home-user-secret が exclude_projects に入っている
    assert result.exit_code == 0, result.output
    assert len(stub_fetch_run) == 1
    assert "-home-user-secret" in stub_fetch_run[0].exclude_projects


def test_exclude_projects_encoded_form_passed_through(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange — encoded-cwd 形 -home-user-secret を直接指定
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--all",
            "--cache",
            str(tmp_path / "cache.json"),
            "--exclude-projects",
            "-home-user-secret",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert len(stub_fetch_run) == 1
    assert "-home-user-secret" in stub_fetch_run[0].exclude_projects


def test_exclude_projects_multiple_csv_accepted(
    tmp_path: Path,
    stub_fetch_run: list[TargetSpec],
) -> None:
    # Arrange — カンマ区切りで複数指定
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "fetch",
            "--all",
            "--cache",
            str(tmp_path / "cache.json"),
            "--exclude-projects",
            "/home/user/secret,-home-other-proj",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert len(stub_fetch_run) == 1
    exclude = stub_fetch_run[0].exclude_projects
    assert "-home-user-secret" in exclude
    assert "-home-other-proj" in exclude


# ---------------------------------------------------------------------------
# show-paths: session:// URI が実ファイルパスに展開される (AC3 / OQ-07(a))
# ---------------------------------------------------------------------------


def test_show_paths_expands_session_uris_in_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    from session_retrospecter.pipeline import extract, fetch

    session_file = tmp_path / "project" / "abc123.jsonl"
    _write_jsonl(session_file, _CORRECTION_EVENTS)
    cache_path = tmp_path / "cache.json"
    out = tmp_path / "human.md"
    ai_out = tmp_path / "ai.md"

    fetch.run(TargetSpec(mode="session", session=session_file), cache_path=cache_path)
    extract.run(cache_path=cache_path)

    monkeypatch.setattr("session_retrospecter.services.classifier.classify", _fake_classify)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    runner = CliRunner()

    # Act
    result = runner.invoke(
        main,
        [
            "generate",
            "--cache",
            str(cache_path),
            "--out",
            str(out),
            "--ai-out",
            str(ai_out),
            "--show-paths",
            "--force",
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    out_content = out.read_text(encoding="utf-8")
    # session:// URI は展開済みで残っていない
    assert "session://abc123" not in out_content
    # 実パスが含まれている
    assert str(session_file) in out_content
