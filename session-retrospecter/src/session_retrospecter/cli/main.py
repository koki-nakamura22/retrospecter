"""cli.main — click エントリポイント (4 サブコマンド + 共通フラグ)."""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

import click
from dotenv import load_dotenv

from ..models.knowledge import DEFAULT_THEMES
from ..models.redact import RedactOptions
from ..models.target import TargetSpec
from ..services.exceptions import FetchError

__all__ = ["main"]

_DEFAULT_THEMES_CSV = ",".join(DEFAULT_THEMES)
_DEFAULT_CACHE = ".retrospect/cache.json"
_DEFAULT_PROJECTS_ROOT = "~/.claude/projects"
_DEFAULT_OUT = ".retrospect/retrospect.md"
_DEFAULT_AI_OUT = ".retrospect/retrospect-ai.md"

_SESSION_URI_RE = re.compile(r"session://([A-Za-z0-9_\-]+)#L(\d+)")

logger = logging.getLogger(__name__)


def _setup_logging(verbose: int, quiet: int) -> None:
    level = logging.DEBUG if verbose > 0 else (logging.WARNING if quiet > 0 else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _parse_since(since: str | None) -> date | None:
    if since is None:
        return None
    if since.endswith("d"):
        try:
            days = int(since[:-1])
            return date.today() - timedelta(days=days)
        except ValueError:
            raise click.BadParameter(f"不正な since 値: {since!r}", param_hint="'--since'")
    try:
        return date.fromisoformat(since)
    except ValueError:
        raise click.BadParameter(f"不正な since 値: {since!r}", param_hint="'--since'")


def _build_exclude_set(csv: str | None) -> frozenset[str]:
    if not csv:
        return frozenset()
    return frozenset(p.strip() for p in csv.split(",") if p.strip())


def _normalize_exclude_projects(raw: frozenset[str]) -> frozenset[str]:
    """OQ-06: decode 形 /home/x/y と encoded-cwd 形 -home-x-y の両方を受け付ける."""
    result: set[str] = set()
    for name in raw:
        result.add(name)
        if name.startswith("/"):
            result.add(name.replace("/", "-"))
    return frozenset(result)


def _build_target(
    project: str | None,
    session: str | None,
    all_projects: bool,
    since: str | None,
    projects_root: str,
    exclude_projects: str | None,
) -> TargetSpec:
    if session is not None:
        mode = "session"
    elif project is not None:
        mode = "project"
    elif all_projects:
        mode = "all"
    else:
        raise click.UsageError("--project / --session / --all のいずれかを指定してください")

    exclude_raw = _build_exclude_set(exclude_projects)
    exclude_set = _normalize_exclude_projects(exclude_raw)

    return TargetSpec(
        mode=mode,  # type: ignore[arg-type]
        projects_root=Path(projects_root).expanduser(),
        project=Path(project) if project else None,
        session=Path(session) if session else None,
        since=_parse_since(since),
        exclude_projects=exclude_set,
    )


def _check_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise click.ClickException("ANTHROPIC_API_KEY を環境変数か .env に設定してください")


def _expand_session_uris(text: str, path_map: dict[str, Path]) -> str:
    """--show-paths: session:// URI を実ファイルパスに展開する (AC3 / OQ-07(a))."""

    def _replace(m: re.Match[str]) -> str:
        sid = m.group(1)
        line_no = m.group(2)
        if sid in path_map:
            return f"{path_map[sid]}#L{line_no}"
        return m.group(0)

    return _SESSION_URI_RE.sub(_replace, text)


def _apply_show_paths(out: Path, ai_out: Path, cache_path: Path) -> None:
    from ..cache import store as cache_store

    cache = cache_store.load(cache_path)
    path_map = {s.session_id: s.source_path for s in cache.sessions}
    for p in (out, ai_out):
        if p.exists():
            expanded = _expand_session_uris(p.read_text(encoding="utf-8"), path_map)
            p.write_text(expanded, encoding="utf-8", newline="\n")


@click.group()  # type: ignore[misc]
@click.version_option()
@click.option("-v", "--verbose", count=True, help="DEBUG ログを有効化 (複数指定可)")
@click.option("-q", "--quiet", count=True, help="WARNING 以上のみ表示")
@click.option(
    "--config",
    type=click.Path(exists=False, dir_okay=False),
    default=None,
    help="設定ファイルパス (未実装: 将来拡張用)",
)
@click.pass_context
def main(ctx: click.Context, verbose: int, quiet: int, config: str | None) -> None:
    """Distill judgment signals from Claude Code session logs."""
    load_dotenv()
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    _setup_logging(verbose, quiet)


@main.command("run")  # type: ignore[misc]
@click.option("--project", metavar="DIR", default=None, help="単一プロジェクトディレクトリ")
@click.option("--session", "session_file", metavar="FILE", default=None, help="単一 JSONL セッションファイル")
@click.option("--all", "all_projects", is_flag=True, default=False, help="全プロジェクト横断")
@click.option("--since", metavar="7d|YYYY-MM-DD", default=None, help="指定日以降のセッションのみ")
@click.option(
    "--projects-root",
    metavar="PATH",
    default=_DEFAULT_PROJECTS_ROOT,
    show_default=True,
    help="プロジェクトルートディレクトリ",
)
@click.option("--out", metavar="PATH", default=_DEFAULT_OUT, show_default=True, help="人間向け Markdown 出力先")
@click.option("--ai-out", metavar="PATH", default=_DEFAULT_AI_OUT, show_default=True, help="AI 向け Knowledge 出力先")
@click.option(
    "--cache",
    metavar="PATH",
    default=_DEFAULT_CACHE,
    show_default=True,
    help="キャッシュファイルパス",
)
@click.option(
    "--themes",
    metavar="csv",
    default=_DEFAULT_THEMES_CSV,
    show_default=True,
    help="テーマ軸 (カンマ区切り)",
)
@click.option("--redact-tokens/--no-redact-tokens", default=True, help="API トークンをマスク (default: ON)")
@click.option("--redact-paths/--no-redact-paths", default=False, help="ファイルパスをマスク (default: OFF)")
@click.option("--exclude-tools", metavar="csv", default=None, help="除外ツール名 (カンマ区切り)")
@click.option("--exclude-projects", metavar="csv", default=None, help="除外プロジェクト (カンマ区切り)")
@click.option("--show-paths", is_flag=True, default=False, help="出典 URI を実パスに展開 (AC3)")
@click.option("--append", is_flag=True, default=False, help="既存 cache に追記 (session_id merge)")
@click.option("--force", is_flag=True, default=False, help="既存出力ファイルを強制上書き")
def cmd_run(  # type: ignore[misc]
    project: str | None,
    session_file: str | None,
    all_projects: bool,
    since: str | None,
    projects_root: str,
    out: str,
    ai_out: str,
    cache: str,
    themes: str,
    redact_tokens: bool,
    redact_paths: bool,
    exclude_tools: str | None,
    exclude_projects: str | None,
    show_paths: bool,
    append: bool,
    force: bool,
) -> None:
    """fetch → extract → generate を一気通貫で実行する."""
    _check_api_key()
    from ..pipeline import run as run_pipeline

    spec = _build_target(project, session_file, all_projects, since, projects_root, exclude_projects)
    redact_opts = RedactOptions(
        mask_tokens=redact_tokens,
        mask_paths=redact_paths,
        exclude_tools=_build_exclude_set(exclude_tools),
    )
    out_path = Path(out)
    ai_out_path = Path(ai_out)
    cache_path = Path(cache)
    themes_list = [t.strip() for t in themes.split(",") if t.strip()]

    try:
        summary = run_pipeline.run(
            spec,
            cache_path=cache_path,
            out=out_path,
            ai_out=ai_out_path,
            redact_opts=redact_opts,
            themes=themes_list,
            append=append,
            force=force,
        )
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    except FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    if show_paths:
        _apply_show_paths(out_path, ai_out_path, cache_path)

    click.echo(
        f"run 完了: sessions={summary.fetch.session_count}"
        f" candidates={summary.extract.candidate_count}"
        f" knowledge={summary.generate.knowledge_count}"
    )


@main.command("fetch")  # type: ignore[misc]
@click.option("--project", metavar="DIR", default=None, help="単一プロジェクトディレクトリ")
@click.option("--session", "session_file", metavar="FILE", default=None, help="単一 JSONL セッションファイル")
@click.option("--all", "all_projects", is_flag=True, default=False, help="全プロジェクト横断")
@click.option("--since", metavar="7d|YYYY-MM-DD", default=None, help="指定日以降のセッションのみ")
@click.option(
    "--projects-root",
    metavar="PATH",
    default=_DEFAULT_PROJECTS_ROOT,
    show_default=True,
    help="プロジェクトルートディレクトリ",
)
@click.option(
    "--cache",
    metavar="PATH",
    default=_DEFAULT_CACHE,
    show_default=True,
    help="キャッシュファイルパス",
)
@click.option("--redact-tokens/--no-redact-tokens", default=True, help="API トークンをマスク (default: ON)")
@click.option("--redact-paths/--no-redact-paths", default=False, help="ファイルパスをマスク (default: OFF)")
@click.option("--exclude-tools", metavar="csv", default=None, help="除外ツール名 (カンマ区切り)")
@click.option("--exclude-projects", metavar="csv", default=None, help="除外プロジェクト (カンマ区切り)")
def cmd_fetch(  # type: ignore[misc]
    project: str | None,
    session_file: str | None,
    all_projects: bool,
    since: str | None,
    projects_root: str,
    cache: str,
    redact_tokens: bool,
    redact_paths: bool,
    exclude_tools: str | None,
    exclude_projects: str | None,
) -> None:
    """セッションを取得してキャッシュに保存する (LLM 不要)."""
    from ..pipeline import fetch as fetch_pipeline

    spec = _build_target(project, session_file, all_projects, since, projects_root, exclude_projects)
    redact_opts = RedactOptions(
        mask_tokens=redact_tokens,
        mask_paths=redact_paths,
        exclude_tools=_build_exclude_set(exclude_tools),
    )

    try:
        summary = fetch_pipeline.run(spec, cache_path=Path(cache), redact_opts=redact_opts)
    except FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"fetch 完了: sessions={summary.session_count} events={summary.event_count}")


@main.command("extract")  # type: ignore[misc]
@click.option(
    "--cache",
    metavar="PATH",
    default=_DEFAULT_CACHE,
    show_default=True,
    help="キャッシュファイルパス",
)
def cmd_extract(cache: str) -> None:  # type: ignore[misc]
    """キャッシュ内セッションから候補を抽出する (LLM 不要)."""
    from ..pipeline import extract as extract_pipeline

    try:
        summary = extract_pipeline.run(cache_path=Path(cache))
    except FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"extract 完了: candidates={summary.candidate_count} by_kind={summary.by_kind}")


@main.command("generate")  # type: ignore[misc]
@click.option(
    "--cache",
    metavar="PATH",
    default=_DEFAULT_CACHE,
    show_default=True,
    help="キャッシュファイルパス",
)
@click.option("--out", metavar="PATH", default=_DEFAULT_OUT, show_default=True, help="人間向け Markdown 出力先")
@click.option("--ai-out", metavar="PATH", default=_DEFAULT_AI_OUT, show_default=True, help="AI 向け Knowledge 出力先")
@click.option(
    "--themes",
    metavar="csv",
    default=_DEFAULT_THEMES_CSV,
    show_default=True,
    help="テーマ軸 (カンマ区切り)",
)
@click.option("--show-paths", is_flag=True, default=False, help="出典 URI を実パスに展開 (AC3)")
@click.option("--append", is_flag=True, default=False, help="既存 cache に追記 (session_id merge)")
@click.option("--force", is_flag=True, default=False, help="既存出力ファイルを強制上書き")
def cmd_generate(  # type: ignore[misc]
    cache: str,
    out: str,
    ai_out: str,
    themes: str,
    show_paths: bool,
    append: bool,
    force: bool,
) -> None:
    """候補を分類して Markdown を生成する (LLM 使用)."""
    _check_api_key()
    from ..pipeline import generate as generate_pipeline

    out_path = Path(out)
    ai_out_path = Path(ai_out)
    cache_path = Path(cache)
    themes_list = [t.strip() for t in themes.split(",") if t.strip()]

    try:
        summary = generate_pipeline.run(
            cache_path=cache_path,
            out=out_path,
            ai_out=ai_out_path,
            themes=themes_list,
            append=append,
            force=force,
        )
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    except FetchError as exc:
        raise click.ClickException(str(exc)) from exc

    if show_paths:
        _apply_show_paths(out_path, ai_out_path, cache_path)

    click.echo(
        f"generate 完了: candidates={summary.candidate_count}"
        f" knowledge={summary.knowledge_count}"
        f" classified={summary.classified}"
    )


