"""click entry point for ``repo-retrospecter`` (PRD §CLI / architecture.md §CLI レイヤー).

Three subcommands map 1:1 to the pipeline orchestrators:

* ``run``      → :func:`pipeline.run.run_pipeline`
* ``fetch``    → :func:`pipeline.fetch.run_fetch`
* ``generate`` → :func:`pipeline.generate.run_generate`

The CLI layer is intentionally thin (architecture.md §CLI レイヤー禁止):
no business logic, no LLM calls; it only parses options, merges them
with the optional config file, and translates expected exceptions into
``click.ClickException`` per decision-defaults.md §エラー処理.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import click
from dotenv import load_dotenv

from repo_retrospecter import __version__
from repo_retrospecter.cli.logging import configure_logging
from repo_retrospecter.config.settings import Settings, load_settings
from repo_retrospecter.pipeline.fetch import run_fetch
from repo_retrospecter.pipeline.generate import run_generate
from repo_retrospecter.pipeline.run import run_pipeline
from repo_retrospecter.services.exceptions import AuthError, FetchError, RateLimitError

# Load .env once at CLI import; safe-after-imports because env vars are read
# lazily by classifier/auth code at call time, not at module top-level.
load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path(".retrospect/cache.json")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_optional_settings(config: Path | None) -> Settings:
    if config is None:
        return Settings()
    try:
        return load_settings(config)
    except FileNotFoundError as exc:
        raise click.ClickException(f"config file not found: {config}") from exc
    except (ValueError, OSError) as exc:
        raise click.ClickException(f"failed to load config {config}: {exc}") from exc


def _coerce_since(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise click.ClickException(f"--since must be YYYY-MM-DD (got {value!r})") from exc


def _check_overwrite(path: Path | None, *, force: bool) -> None:
    """Enforce decision-defaults.md §ユーザー対話: existing files need ``--force``."""
    if path is None or not path.exists():
        return
    if force:
        return
    raise click.ClickException(f"{path} already exists; pass --force to overwrite")


def _setup_logging(verbose: bool, quiet: bool) -> None:
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet are mutually exclusive")
    configure_logging(verbose=verbose, quiet=quiet)


# ---------------------------------------------------------------------------
# group + subcommands
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="repo-retrospecter")
def cli() -> None:
    """Generate retrospectives + AI knowledge from GitHub PR history."""


@cli.command("run")
@click.option("--repo", type=str, default=None, help="GitHub repo (owner/name).")
@click.option("--last", type=int, default=None, help="Fetch the most recent N merged PRs.")
@click.option(
    "--last-commits",
    type=int,
    default=None,
    help="Inspect the most recent N default-branch commits for loose-commit detection.",
)
@click.option("--since", type=str, default=None, help="Fetch PRs / commits on/after YYYY-MM-DD.")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Human-facing Markdown output path.",
)
@click.option(
    "--ai-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="AI-facing Markdown output path.",
)
@click.option(
    "--cache",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Intermediate cache JSON path (default: {DEFAULT_CACHE_PATH}).",
)
@click.option(
    "--config",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Optional JSON/TOML config file.",
)
@click.option("--force", is_flag=True, help="Overwrite existing output files.")
@click.option(
    "--no-loose-commits",
    is_flag=True,
    help="Skip default-branch commits not associated with any PR.",
)
@click.option(
    "--append",
    is_flag=True,
    help="Incremental update (ADR-0005): merge new PRs/commits into the existing cache.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging.")
@click.option("--quiet", "-q", is_flag=True, help="Limit logging to WARN+.")
def cmd_run(
    repo: str | None,
    last: int | None,
    last_commits: int | None,
    since: str | None,
    out: Path | None,
    ai_out: Path | None,
    cache: Path | None,
    config: Path | None,
    force: bool,
    no_loose_commits: bool,
    append: bool,
    verbose: bool,
    quiet: bool,
) -> None:
    """Fetch + classify + render in one go (PRD F1+F2+F3+F4)."""
    _setup_logging(verbose, quiet)
    settings = _load_optional_settings(config)

    repo_value = repo or settings.repo
    if not repo_value:
        raise click.UsageError("--repo is required (or set in --config).")

    last_value = last if last is not None else settings.last
    last_commits_value = last_commits if last_commits is not None else settings.last_commits
    since_value = _coerce_since(since if since is not None else settings.since)
    out_value = out or settings.out
    ai_out_value = ai_out or settings.ai_out
    cache_value = cache or settings.cache or DEFAULT_CACHE_PATH

    if not append:
        # In append mode the cache is intentionally read+rewritten; skip
        # the overwrite guard so users don't need --force every time.
        _check_overwrite(out_value, force=force)
        _check_overwrite(ai_out_value, force=force)

    try:
        summary = run_pipeline(
            repo=repo_value,
            cache_path=cache_value,
            last=last_value,
            last_commits=last_commits_value,
            since=since_value,
            human_out=out_value,
            ai_out=ai_out_value,
            themes=settings.themes,
            include_loose_commits=not no_loose_commits,
            append=append,
        )
    except AuthError as exc:
        raise click.ClickException(f"gh authentication required: {exc}") from exc
    except RateLimitError as exc:
        raise click.ClickException(f"GitHub rate limit hit: {exc}") from exc
    except FetchError as exc:
        raise click.ClickException(f"fetch failed: {exc}") from exc

    click.echo(
        f"run done: pr={summary.fetch.pr_count} "
        f"knowledge={summary.generate.knowledge_count} "
        f"outputs={len(summary.generate.rendered_outputs)}"
    )


@cli.command("fetch")
@click.option("--repo", type=str, default=None, help="GitHub repo (owner/name).")
@click.option("--last", type=int, default=None, help="Fetch the most recent N merged PRs.")
@click.option(
    "--last-commits",
    type=int,
    default=None,
    help="Inspect the most recent N default-branch commits for loose-commit detection.",
)
@click.option("--since", type=str, default=None, help="Fetch PRs / commits on/after YYYY-MM-DD.")
@click.option(
    "--cache",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Intermediate cache JSON path (default: {DEFAULT_CACHE_PATH}).",
)
@click.option(
    "--config",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Optional JSON/TOML config file.",
)
@click.option("--force", is_flag=True, help="Overwrite existing cache file.")
@click.option(
    "--no-loose-commits",
    is_flag=True,
    help="Skip default-branch commits not associated with any PR.",
)
@click.option(
    "--append",
    is_flag=True,
    help="Incremental update (ADR-0005): merge new PRs/commits into the existing cache.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging.")
@click.option("--quiet", "-q", is_flag=True, help="Limit logging to WARN+.")
def cmd_fetch(
    repo: str | None,
    last: int | None,
    last_commits: int | None,
    since: str | None,
    cache: Path | None,
    config: Path | None,
    force: bool,
    no_loose_commits: bool,
    append: bool,
    verbose: bool,
    quiet: bool,
) -> None:
    """Fetch PRs + comments and persist to the cache JSON (PRD F1)."""
    _setup_logging(verbose, quiet)
    settings = _load_optional_settings(config)

    repo_value = repo or settings.repo
    if not repo_value:
        raise click.UsageError("--repo is required (or set in --config).")

    last_value = last if last is not None else settings.last
    last_commits_value = last_commits if last_commits is not None else settings.last_commits
    since_value = _coerce_since(since if since is not None else settings.since)
    cache_value = cache or settings.cache or DEFAULT_CACHE_PATH

    if not append:
        _check_overwrite(cache_value, force=force)

    try:
        summary = run_fetch(
            repo=repo_value,
            cache_path=cache_value,
            last=last_value,
            last_commits=last_commits_value,
            since=since_value,
            include_loose_commits=not no_loose_commits,
            append=append,
        )
    except AuthError as exc:
        raise click.ClickException(f"gh authentication required: {exc}") from exc
    except RateLimitError as exc:
        raise click.ClickException(f"GitHub rate limit hit: {exc}") from exc
    except FetchError as exc:
        raise click.ClickException(f"fetch failed: {exc}") from exc

    click.echo(
        f"fetch done: repo={summary.repo} pr={summary.pr_count} "
        f"loose_commits={summary.loose_commit_count} cache={summary.cache_path}"
    )


@cli.command("generate")
@click.option(
    "--cache",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=f"Intermediate cache JSON path (default: {DEFAULT_CACHE_PATH}).",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Human-facing Markdown output path.",
)
@click.option(
    "--ai-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="AI-facing Markdown output path.",
)
@click.option(
    "--config",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default=None,
    help="Optional JSON/TOML config file.",
)
@click.option("--force", is_flag=True, help="Overwrite existing output files.")
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging.")
@click.option("--quiet", "-q", is_flag=True, help="Limit logging to WARN+.")
def cmd_generate(
    cache: Path | None,
    out: Path | None,
    ai_out: Path | None,
    config: Path | None,
    force: bool,
    verbose: bool,
    quiet: bool,
) -> None:
    """Classify (when needed) and render Markdown from cache (PRD F2+F3+F4)."""
    _setup_logging(verbose, quiet)
    settings = _load_optional_settings(config)

    cache_value = cache or settings.cache or DEFAULT_CACHE_PATH
    out_value = out or settings.out
    ai_out_value = ai_out or settings.ai_out

    if not cache_value.exists():
        raise click.ClickException(f"cache file not found: {cache_value}")

    _check_overwrite(out_value, force=force)
    _check_overwrite(ai_out_value, force=force)

    try:
        summary = run_generate(
            cache_path=cache_value,
            human_out=out_value,
            ai_out=ai_out_value,
            themes=settings.themes,
        )
    except FetchError as exc:
        raise click.ClickException(f"generate failed: {exc}") from exc

    click.echo(
        f"generate done: pr={summary.pr_count} "
        f"knowledge={summary.knowledge_count} "
        f"classified={summary.classified} "
        f"outputs={len(summary.rendered_outputs)}"
    )


def main() -> None:
    """Console-script entry point registered in ``pyproject.toml``."""
    cli()


if __name__ == "__main__":
    main()


__all__ = ["cli", "cmd_fetch", "cmd_generate", "cmd_run", "main"]
