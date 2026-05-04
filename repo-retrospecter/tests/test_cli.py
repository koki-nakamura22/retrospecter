"""Unit tests for repo_retrospecter.cli (T008)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from repo_retrospecter.cli.logging import (
    RedactFilter,
    configure_logging,
    redact,
)
from repo_retrospecter.cli.main import DEFAULT_CACHE_PATH, cli
from repo_retrospecter.config.settings import Settings, load_settings
from repo_retrospecter.models.cache import CACHE_SCHEMA_VERSION, CacheFile
from repo_retrospecter.pipeline.fetch import FetchSummary
from repo_retrospecter.pipeline.generate import GenerateSummary
from repo_retrospecter.pipeline.run import RunSummary
from repo_retrospecter.services.exceptions import AuthError, RateLimitError


# ---------------------------------------------------------------------------
# config.settings
# ---------------------------------------------------------------------------


class TestLoadSettings:
    def test_loads_json(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text(
            '{"repo": "owner/name", "themes": ["security", "perf"], "last": 5}',
            encoding="utf-8",
        )

        settings = load_settings(cfg)

        assert settings.repo == "owner/name"
        assert settings.themes == ["security", "perf"]
        assert settings.last == 5

    def test_loads_toml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.toml"
        cfg.write_text(
            'repo = "owner/name"\nthemes = ["a", "b"]\n', encoding="utf-8"
        )

        settings = load_settings(cfg)

        assert settings.repo == "owner/name"
        assert settings.themes == ["a", "b"]

    def test_unknown_extension_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.yaml"
        cfg.write_text("repo: x", encoding="utf-8")

        with pytest.raises(ValueError, match="unsupported config extension"):
            load_settings(cfg)

    def test_json_top_level_must_be_object(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(ValueError, match="JSON object"):
            load_settings(cfg)

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "missing.json")

    def test_extra_fields_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text('{"unknown_key": 1}', encoding="utf-8")

        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            load_settings(cfg)

    def test_empty_themes_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.json"
        cfg.write_text('{"themes": []}', encoding="utf-8")

        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
            load_settings(cfg)

    def test_default_settings_are_all_none(self) -> None:
        s = Settings()
        assert s.repo is None
        assert s.themes is None
        assert s.last is None
        assert s.since is None
        assert s.out is None
        assert s.ai_out is None
        assert s.cache is None


# ---------------------------------------------------------------------------
# cli.logging.redact / RedactFilter
# ---------------------------------------------------------------------------


class TestRedact:
    def test_masks_anthropic_key_pattern(self) -> None:
        out = redact("token=sk-ant-AbCdEf123456 trailing")
        assert "sk-ant-AbCdEf123456" not in out
        assert "***" in out

    def test_masks_github_pat_pattern(self) -> None:
        out = redact("auth ghp_" + "A" * 30)
        assert "ghp_" not in out

    def test_masks_active_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "supersecret-12345678")
        out = redact("header: supersecret-12345678 done")
        assert "supersecret-12345678" not in out
        assert "***" in out

    def test_preserves_unrelated_text(self) -> None:
        assert redact("nothing to mask here") == "nothing to mask here"


class TestRedactFilter:
    def test_filter_redacts_msg(self) -> None:
        filt = RedactFilter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="x",
            lineno=1,
            msg="key=sk-ant-XYZ123456",
            args=None,
            exc_info=None,
        )
        assert filt.filter(record) is True
        assert "sk-ant-XYZ123456" not in record.getMessage()

    def test_filter_redacts_string_args(self) -> None:
        filt = RedactFilter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="x",
            lineno=1,
            msg="hit %s",
            args=("sk-ant-XYZ123456",),
            exc_info=None,
        )
        filt.filter(record)
        assert "sk-ant-XYZ123456" not in record.getMessage()

    def test_filter_passes_non_string_args_through(self) -> None:
        filt = RedactFilter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="x",
            lineno=1,
            msg="count %d",
            args=(42,),
            exc_info=None,
        )
        filt.filter(record)
        assert record.getMessage() == "count 42"


class TestConfigureLogging:
    def test_default_level_is_info(self) -> None:
        configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_verbose_sets_debug(self) -> None:
        configure_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_quiet_sets_warning(self) -> None:
        configure_logging(quiet=True)
        assert logging.getLogger().level == logging.WARNING

    def test_verbose_and_quiet_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            configure_logging(verbose=True, quiet=True)

    def test_repeated_calls_do_not_accumulate_handlers(self) -> None:
        configure_logging()
        first = list(logging.getLogger().handlers)
        configure_logging()
        second = list(logging.getLogger().handlers)
        assert len(second) == 1
        assert second != first  # new handler instance

    def test_redact_filter_attached_to_handler(self) -> None:
        configure_logging()
        handler = logging.getLogger().handlers[0]
        assert any(isinstance(f, RedactFilter) for f in handler.filters)


# ---------------------------------------------------------------------------
# CLI surface (--help / --version / unknown)
# ---------------------------------------------------------------------------


class TestCliHelp:
    def test_root_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "fetch" in result.output
        assert "generate" in result.output

    def test_version_flag(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "repo-retrospecter" in result.output

    def test_run_help_lists_required_options(self) -> None:
        result = CliRunner().invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        for opt in ("--repo", "--last", "--since", "--out", "--ai-out", "--cache",
                    "--config", "--force", "--verbose", "--quiet"):
            assert opt in result.output

    def test_fetch_help(self) -> None:
        result = CliRunner().invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        for opt in ("--repo", "--last", "--since", "--cache", "--config",
                    "--force", "--verbose", "--quiet"):
            assert opt in result.output

    def test_generate_help(self) -> None:
        result = CliRunner().invoke(cli, ["generate", "--help"])
        assert result.exit_code == 0
        for opt in ("--cache", "--out", "--ai-out", "--config", "--force",
                    "--verbose", "--quiet"):
            assert opt in result.output


# ---------------------------------------------------------------------------
# cli run / fetch / generate (mock pipeline functions)
# ---------------------------------------------------------------------------


def _make_run_summary() -> RunSummary:
    return RunSummary(
        fetch=FetchSummary(repo="o/r", cache_path=Path("c.json"), pr_count=3),
        generate=GenerateSummary(
            cache_path=Path("c.json"),
            pr_count=3,
            knowledge_count=4,
            classified=True,
            rendered_outputs=(Path("o.md"),),
        ),
    )


class TestCmdRun:
    def test_invokes_run_pipeline_with_options(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                [
                    "run",
                    "--repo", "owner/name",
                    "--last", "10",
                    "--cache", str(cache),
                ],
            )

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["repo"] == "owner/name"
        assert kwargs["last"] == 10
        assert kwargs["cache_path"] == cache
        assert kwargs["since"] is None
        assert "run done" in result.output

    def test_since_parsed_to_date(self, tmp_path: Path) -> None:
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                ["run", "--repo", "o/r", "--since", "2026-04-01",
                 "--cache", str(tmp_path / "c.json")],
            )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["since"] == date(2026, 4, 1)

    def test_invalid_since_rejected(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli,
            ["run", "--repo", "o/r", "--since", "not-a-date",
             "--cache", str(tmp_path / "c.json")],
        )
        assert result.exit_code != 0
        assert "YYYY-MM-DD" in result.output

    def test_missing_repo_errors(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli, ["run", "--cache", str(tmp_path / "c.json")]
        )
        assert result.exit_code != 0
        assert "--repo" in result.output

    def test_default_cache_path_when_omitted(self) -> None:
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(cli, ["run", "--repo", "o/r"])
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["cache_path"] == DEFAULT_CACHE_PATH

    def test_config_file_supplies_repo(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text(
            '{"repo": "from/config", "themes": ["a", "b"]}', encoding="utf-8"
        )
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                ["run", "--config", str(cfg), "--cache", str(tmp_path / "c.json")],
            )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["repo"] == "from/config"
        assert mock_run.call_args.kwargs["themes"] == ["a", "b"]

    def test_cli_repo_overrides_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text('{"repo": "from/config"}', encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                [
                    "run",
                    "--config", str(cfg),
                    "--repo", "from/cli",
                    "--cache", str(tmp_path / "c.json"),
                ],
            )
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["repo"] == "from/cli"

    def test_missing_config_file_errors(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "run",
                "--repo", "o/r",
                "--config", str(tmp_path / "missing.json"),
                "--cache", str(tmp_path / "c.json"),
            ],
        )
        assert result.exit_code != 0
        assert "config file not found" in result.output

    def test_existing_output_blocked_without_force(self, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        out.write_text("existing", encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                [
                    "run", "--repo", "o/r",
                    "--out", str(out),
                    "--cache", str(tmp_path / "c.json"),
                ],
            )
        assert result.exit_code != 0
        assert "--force" in result.output
        mock_run.assert_not_called()

    def test_existing_output_allowed_with_force(self, tmp_path: Path) -> None:
        out = tmp_path / "out.md"
        out.write_text("existing", encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_pipeline") as mock_run:
            mock_run.return_value = _make_run_summary()
            result = CliRunner().invoke(
                cli,
                [
                    "run", "--repo", "o/r",
                    "--out", str(out),
                    "--force",
                    "--cache", str(tmp_path / "c.json"),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()

    def test_auth_error_translated_to_clickexception(self, tmp_path: Path) -> None:
        with patch("repo_retrospecter.cli.main.run_pipeline", side_effect=AuthError("login")):
            result = CliRunner().invoke(
                cli,
                ["run", "--repo", "o/r", "--cache", str(tmp_path / "c.json")],
            )
        assert result.exit_code != 0
        assert "gh authentication required" in result.output

    def test_rate_limit_error_translated(self, tmp_path: Path) -> None:
        with patch(
            "repo_retrospecter.cli.main.run_pipeline",
            side_effect=RateLimitError("retry in 60s"),
        ):
            result = CliRunner().invoke(
                cli,
                ["run", "--repo", "o/r", "--cache", str(tmp_path / "c.json")],
            )
        assert result.exit_code != 0
        assert "rate limit" in result.output.lower()

    def test_verbose_quiet_mutually_exclusive(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "run", "--repo", "o/r",
                "--verbose", "--quiet",
                "--cache", str(tmp_path / "c.json"),
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


class TestCmdFetch:
    def test_invokes_run_fetch(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        with patch("repo_retrospecter.cli.main.run_fetch") as mock_fetch:
            mock_fetch.return_value = FetchSummary(repo="o/r", cache_path=cache, pr_count=2)
            result = CliRunner().invoke(
                cli, ["fetch", "--repo", "o/r", "--last", "5", "--cache", str(cache)]
            )

        assert result.exit_code == 0, result.output
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["repo"] == "o/r"
        assert kwargs["last"] == 5
        assert kwargs["cache_path"] == cache
        assert "fetch done" in result.output

    def test_missing_repo_errors(self) -> None:
        result = CliRunner().invoke(cli, ["fetch"])
        assert result.exit_code != 0
        assert "--repo" in result.output

    def test_existing_cache_blocked_without_force(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        cache.write_text("{}", encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_fetch") as mock_fetch:
            result = CliRunner().invoke(
                cli, ["fetch", "--repo", "o/r", "--cache", str(cache)]
            )
        assert result.exit_code != 0
        assert "--force" in result.output
        mock_fetch.assert_not_called()


class TestCmdGenerate:
    def _make_cache_file(self, path: Path) -> None:
        cache = CacheFile(
            schema_version=CACHE_SCHEMA_VERSION,
            generated_at=datetime(2026, 5, 4, tzinfo=UTC),
            repo="o/r",
            pull_requests=[],
            knowledge=None,
        )
        path.write_text(cache.model_dump_json(), encoding="utf-8")

    def test_missing_cache_errors(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli, ["generate", "--cache", str(tmp_path / "missing.json")]
        )
        assert result.exit_code != 0
        assert "cache file not found" in result.output

    def test_invokes_run_generate(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        self._make_cache_file(cache)
        with patch("repo_retrospecter.cli.main.run_generate") as mock_gen:
            mock_gen.return_value = GenerateSummary(
                cache_path=cache,
                pr_count=1,
                knowledge_count=2,
                classified=True,
                rendered_outputs=(),
            )
            result = CliRunner().invoke(
                cli, ["generate", "--cache", str(cache)]
            )

        assert result.exit_code == 0, result.output
        kwargs = mock_gen.call_args.kwargs
        assert kwargs["cache_path"] == cache
        assert kwargs["human_out"] is None
        assert kwargs["ai_out"] is None
        assert "generate done" in result.output

    def test_existing_output_blocked_without_force(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        self._make_cache_file(cache)
        out = tmp_path / "o.md"
        out.write_text("existing", encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_generate") as mock_gen:
            result = CliRunner().invoke(
                cli, ["generate", "--cache", str(cache), "--out", str(out)]
            )
        assert result.exit_code != 0
        assert "--force" in result.output
        mock_gen.assert_not_called()

    def test_themes_from_config_passed_through(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.json"
        self._make_cache_file(cache)
        cfg = tmp_path / "cfg.json"
        cfg.write_text('{"themes": ["security", "perf"]}', encoding="utf-8")
        with patch("repo_retrospecter.cli.main.run_generate") as mock_gen:
            mock_gen.return_value = GenerateSummary(
                cache_path=cache,
                pr_count=0,
                knowledge_count=0,
                classified=False,
                rendered_outputs=(),
            )
            result = CliRunner().invoke(
                cli,
                [
                    "generate",
                    "--cache", str(cache),
                    "--config", str(cfg),
                ],
            )
        assert result.exit_code == 0, result.output
        assert mock_gen.call_args.kwargs["themes"] == ["security", "perf"]
