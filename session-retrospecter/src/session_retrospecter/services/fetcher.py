"""services.fetcher — JSONL reader + project/session discovery."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, TypeAlias, cast

from session_retrospecter.models.event import Session, SessionEvent
from session_retrospecter.models.target import TargetSpec

__all__ = [
    "ProjectDir",
    "SessionFile",
    "discover_projects",
    "discover_sessions",
    "read_session",
    "read_target",
]

logger = logging.getLogger(__name__)

ProjectDir: TypeAlias = Path
SessionFile: TypeAlias = Path

_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "user",
        "assistant",
        "system",
        "permission-mode",
        "attachment",
        "file-history-snapshot",
        "last-prompt",
        "ai-title",
    }
)

_DEFAULT_PROJECTS_ROOT: Path = Path("~/.claude/projects").expanduser()


def discover_projects(
    root: Path = _DEFAULT_PROJECTS_ROOT,
) -> list[ProjectDir]:
    """Enumerate subdirectories of root as ProjectDir entries."""
    if not root.exists() or not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def discover_sessions(project: ProjectDir) -> list[SessionFile]:
    """Enumerate *.jsonl files in a project directory."""
    return sorted(project.glob("*.jsonl"))


def read_session(path: Path, *, since: date | None = None) -> Session:
    """Parse a JSONL file into a Session, skipping unparseable lines with a warning."""
    session_id = path.stem
    project_dir = path.parent

    if since is not None:
        mtime = date.fromtimestamp(path.stat().st_mtime)
        if mtime < since:
            return Session(session_id=session_id, source_path=path, project_dir=project_dir)

    events: list[SessionEvent] = []
    warnings: list[str] = []

    with path.open(encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                parsed: Any = json.loads(stripped)
            except json.JSONDecodeError as exc:
                msg = f"line {line_no}: JSON parse failed — {exc}"
                logger.warning("JSON parse failed at line %d in <session>: %s", line_no, exc)
                warnings.append(msg)
                continue

            if not isinstance(parsed, dict):
                msg = f"line {line_no}: expected JSON object, got {type(parsed).__name__}"
                logger.warning("Unexpected JSON at line %d in <session>", line_no)
                warnings.append(msg)
                continue

            data = cast(dict[str, Any], parsed)
            event_type: str = data.get("type", "")
            is_unknown = event_type not in _KNOWN_EVENT_TYPES

            if is_unknown:
                logger.warning("Unknown event type %r at line %d in <session>", event_type, line_no)

            event_data: dict[str, Any] = dict(data)
            event_data["line_no"] = line_no
            event_data["session_id"] = session_id
            if is_unknown:
                event_data["raw"] = data

            events.append(SessionEvent.model_validate(event_data))

    return Session(
        session_id=session_id,
        source_path=path,
        project_dir=project_dir,
        events=events,
        parse_warnings=warnings,
    )


def _encode_path(decoded: str) -> str:
    """Convert a decoded absolute path to Claude Code's encoded-cwd format."""
    return decoded.replace("/", "-")


def _should_exclude(project: Path, exclude_projects: frozenset[str]) -> bool:
    """Return True if the project directory name matches any exclude entry."""
    name = project.name
    for exc in exclude_projects:
        if exc == name:
            return True
        if _encode_path(exc) == name:
            return True
    return False


def read_target(spec: TargetSpec) -> list[Session]:
    """Resolve a TargetSpec to a list of Sessions (session > project > all priority)."""
    paths: list[Path] = []

    if spec.mode == "session":
        if spec.session is not None:
            paths = [spec.session]
    elif spec.mode == "project":
        if spec.project is not None:
            paths = list(discover_sessions(spec.project))
    else:
        for project in discover_projects(spec.projects_root):
            if _should_exclude(project, spec.exclude_projects):
                continue
            paths.extend(discover_sessions(project))

    return [read_session(p, since=spec.since) for p in paths]
