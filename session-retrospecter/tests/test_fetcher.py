"""TC-FT-01〜06: services.fetcher の受け入れテスト."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from session_retrospecter.models.target import TargetSpec
from session_retrospecter.services.fetcher import (
    discover_projects,
    discover_sessions,
    read_session,
    read_target,
)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# TC-FT-01: 正常 jsonl → Session.events 件数一致
# ---------------------------------------------------------------------------


def test_read_session_normal_events(tmp_path: Path) -> None:
    session_file = tmp_path / "abc.jsonl"
    records = [
        {"type": "user", "text": "hello", "lineNo": 1},
        {"type": "assistant", "text": "hi", "lineNo": 2},
        {"type": "user", "text": "bye", "lineNo": 3},
    ]
    _write_jsonl(session_file, records)

    # Act
    session = read_session(session_file)

    # Assert
    assert session.session_id == "abc"
    assert len(session.events) == 3
    assert session.events[0].line_no == 1
    assert session.events[1].line_no == 2
    assert session.events[2].line_no == 3
    assert session.parse_warnings == []


def test_read_session_line_no_is_file_position(tmp_path: Path) -> None:
    """line_no は lineNo フィールドではなくファイル内の実位置 (1-origin)."""
    session_file = tmp_path / "s.jsonl"
    # lineNo in JSON doesn't match actual line position
    records = [
        {"type": "user", "text": "a", "lineNo": 99},
        {"type": "assistant", "text": "b", "lineNo": 100},
    ]
    _write_jsonl(session_file, records)

    session = read_session(session_file)

    assert session.events[0].line_no == 1
    assert session.events[1].line_no == 2


# ---------------------------------------------------------------------------
# TC-FT-02: parse 失敗行 skip + WARN
# ---------------------------------------------------------------------------


def test_read_session_skips_invalid_json_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session_file = tmp_path / "broken.jsonl"
    session_file.write_text(
        '{"type": "user", "text": "ok"}\n'
        "NOT_VALID_JSON\n"
        '{"type": "assistant", "text": "also ok"}\n',
        encoding="utf-8",
    )

    # Act
    with caplog.at_level(logging.WARNING):
        session = read_session(session_file)

    # Assert: 2 valid events, 1 bad line skipped
    assert len(session.events) == 2
    assert len(session.parse_warnings) == 1
    assert any("JSON parse failed" in w for w in session.parse_warnings)
    assert any("JSON parse failed" in msg for msg in caplog.messages)


def test_read_session_continues_after_bad_line(tmp_path: Path) -> None:
    """bad line の後の行も正常に読み込まれる."""
    session_file = tmp_path / "s.jsonl"
    session_file.write_text(
        'GARBAGE\n{"type": "user", "text": "recovered"}\n',
        encoding="utf-8",
    )

    session = read_session(session_file)

    assert len(session.events) == 1
    assert session.events[0].text == "recovered"
    assert session.events[0].line_no == 2


# ---------------------------------------------------------------------------
# TC-FT-03: 不明 type → raw 保持 (OQ-05 互換)
# ---------------------------------------------------------------------------


def test_read_session_unknown_type_raw_preserved(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session_file = tmp_path / "sess.jsonl"
    _write_jsonl(
        session_file,
        [{"type": "future-type", "custom_field": "kept", "lineNo": 1}],
    )

    # Act
    with caplog.at_level(logging.WARNING):
        session = read_session(session_file)

    # Assert: event preserved with raw
    assert len(session.events) == 1
    ev = session.events[0]
    assert ev.type == "future-type"
    assert ev.raw is not None
    assert ev.raw["custom_field"] == "kept"
    # WARNING logged
    assert any("Unknown event type" in msg for msg in caplog.messages)
    # No exception raised (parse_warnings は空)
    assert session.parse_warnings == []


def test_read_session_known_type_raw_is_none(tmp_path: Path) -> None:
    """known type は raw を設定しない."""
    session_file = tmp_path / "s.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "hi"}])

    session = read_session(session_file)

    assert session.events[0].raw is None


# ---------------------------------------------------------------------------
# TC-FT-04: --project / --session 切替
# ---------------------------------------------------------------------------


def test_read_target_session_mode(tmp_path: Path) -> None:
    project = tmp_path / "-home-user-proj"
    project.mkdir()
    session_file = project / "sess1.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "hi", "lineNo": 1}])

    # Arrange
    spec = TargetSpec(mode="session", session=session_file)

    # Act
    sessions = read_target(spec)

    # Assert
    assert len(sessions) == 1
    assert sessions[0].session_id == "sess1"
    assert len(sessions[0].events) == 1


def test_read_target_project_mode(tmp_path: Path) -> None:
    project = tmp_path / "-home-user-proj"
    project.mkdir()
    for name in ("alpha", "beta"):
        _write_jsonl(project / f"{name}.jsonl", [{"type": "user", "text": name}])

    # Arrange
    spec = TargetSpec(mode="project", project=project)

    # Act
    sessions = read_target(spec)

    # Assert
    assert len(sessions) == 2
    assert {s.session_id for s in sessions} == {"alpha", "beta"}


def test_read_target_session_mode_ignores_other_files(tmp_path: Path) -> None:
    """session モードでは指定ファイルのみ読む (同プロジェクトの他ファイルは無視)."""
    project = tmp_path / "-home-user-proj"
    project.mkdir()
    target = project / "target.jsonl"
    other = project / "other.jsonl"
    _write_jsonl(target, [{"type": "user", "text": "target"}])
    _write_jsonl(other, [{"type": "user", "text": "other"}])

    spec = TargetSpec(mode="session", session=target)
    sessions = read_target(spec)

    assert len(sessions) == 1
    assert sessions[0].session_id == "target"


# ---------------------------------------------------------------------------
# TC-FT-05: --all で複数 ProjectDir 横断
# ---------------------------------------------------------------------------


def test_read_target_all_mode_multiple_projects(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    for proj_name in ("-home-user-alpha", "-home-user-beta"):
        proj = root / proj_name
        proj.mkdir()
        _write_jsonl(proj / "session.jsonl", [{"type": "user", "text": proj_name}])

    # Arrange
    spec = TargetSpec(mode="all", projects_root=root)

    # Act
    sessions = read_target(spec)

    # Assert
    assert len(sessions) == 2
    texts = {s.events[0].text for s in sessions}
    assert texts == {"-home-user-alpha", "-home-user-beta"}


def test_discover_projects_returns_dirs_only(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (root / "proj-a").mkdir()
    (root / "proj-b").mkdir()
    (root / "not-a-dir.txt").write_text("x")

    projects = discover_projects(root)

    assert len(projects) == 2
    assert all(p.is_dir() for p in projects)


def test_discover_sessions_returns_jsonl_only(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.jsonl").write_text('{"type":"user","line_no":1,"session_id":"a"}')
    (proj / "b.jsonl").write_text('{"type":"user","line_no":1,"session_id":"b"}')
    (proj / "readme.txt").write_text("ignored")

    sessions = discover_sessions(proj)

    assert len(sessions) == 2
    assert all(str(s).endswith(".jsonl") for s in sessions)


# ---------------------------------------------------------------------------
# TC-FT-06: --exclude-projects skip
# ---------------------------------------------------------------------------


def test_read_target_exclude_projects_encoded_form(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    pub = root / "-home-user-public"
    pub.mkdir()
    _write_jsonl(pub / "s.jsonl", [{"type": "user", "text": "pub"}])
    prv = root / "-home-user-private"
    prv.mkdir()
    _write_jsonl(prv / "s.jsonl", [{"type": "user", "text": "prv"}])

    # Arrange: exclude using encoded-cwd form
    spec = TargetSpec(
        mode="all",
        projects_root=root,
        exclude_projects=frozenset({"-home-user-private"}),
    )

    # Act
    sessions = read_target(spec)

    # Assert
    assert len(sessions) == 1
    assert sessions[0].events[0].text == "pub"


def test_read_target_exclude_projects_decoded_form(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    pub = root / "-home-user-public"
    pub.mkdir()
    _write_jsonl(pub / "s.jsonl", [{"type": "user", "text": "pub"}])
    prv = root / "-home-user-private"
    prv.mkdir()
    _write_jsonl(prv / "s.jsonl", [{"type": "user", "text": "prv"}])

    # Arrange: exclude using decoded absolute path form
    spec = TargetSpec(
        mode="all",
        projects_root=root,
        exclude_projects=frozenset({"/home/user/private"}),
    )

    # Act
    sessions = read_target(spec)

    # Assert
    assert len(sessions) == 1
    assert sessions[0].events[0].text == "pub"


def test_read_target_exclude_all_projects(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    proj = root / "-home-user-proj"
    proj.mkdir()
    _write_jsonl(proj / "s.jsonl", [{"type": "user", "text": "x"}])

    spec = TargetSpec(
        mode="all",
        projects_root=root,
        exclude_projects=frozenset({"-home-user-proj"}),
    )
    sessions = read_target(spec)

    assert sessions == []


# ---------------------------------------------------------------------------
# discover_projects 追加エッジケース
# ---------------------------------------------------------------------------


def test_discover_projects_nonexistent_root_returns_empty() -> None:
    """存在しない root は空リストを返す (FileNotFoundError を上げない)."""
    projects = discover_projects(Path("/nonexistent/path/xyz"))

    assert projects == []


def test_discover_projects_empty_root_returns_empty(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()

    projects = discover_projects(empty_root)

    assert projects == []


def test_discover_projects_sorted(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("z-proj", "a-proj", "m-proj"):
        (root / name).mkdir()

    projects = discover_projects(root)

    assert [p.name for p in projects] == ["a-proj", "m-proj", "z-proj"]


# ---------------------------------------------------------------------------
# read_session — since フィルタリング (AC3)
# ---------------------------------------------------------------------------


def _set_mtime(path: Path, dt: datetime) -> None:
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def test_read_session_since_skips_file_older_than_since(tmp_path: Path) -> None:
    """mtime が since より古いファイルは空 Session を返す."""
    session_file = tmp_path / "old.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "should not appear"}])
    _set_mtime(session_file, datetime(2020, 1, 1))

    # Act
    session = read_session(session_file, since=date(2020, 6, 1))

    # Assert: skipped → no events
    assert session.events == []
    assert session.session_id == "old"


def test_read_session_since_reads_file_on_boundary(tmp_path: Path) -> None:
    """mtime == since の日付はスキップしない (境界値: since 当日は対象)."""
    session_file = tmp_path / "boundary.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "boundary"}])
    _set_mtime(session_file, datetime(2020, 6, 1, 12, 0, 0))

    session = read_session(session_file, since=date(2020, 6, 1))

    assert len(session.events) == 1


def test_read_session_since_reads_file_newer_than_since(tmp_path: Path) -> None:
    """mtime > since のファイルは通常通り読む."""
    session_file = tmp_path / "recent.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "hello"}])
    _set_mtime(session_file, datetime(2020, 12, 31))

    session = read_session(session_file, since=date(2020, 6, 1))

    assert len(session.events) == 1
    assert session.events[0].text == "hello"


# ---------------------------------------------------------------------------
# read_session — 空ファイル / ブランク行 / 非 dict JSON
# ---------------------------------------------------------------------------


def test_read_session_empty_file_returns_empty_session(tmp_path: Path) -> None:
    session_file = tmp_path / "empty.jsonl"
    session_file.write_text("", encoding="utf-8")

    session = read_session(session_file)

    assert session.events == []
    assert session.parse_warnings == []


def test_read_session_blank_lines_are_skipped(tmp_path: Path) -> None:
    session_file = tmp_path / "blanks.jsonl"
    session_file.write_text(
        '{"type": "user", "text": "a"}\n\n\n{"type": "assistant", "text": "b"}\n',
        encoding="utf-8",
    )

    session = read_session(session_file)

    assert len(session.events) == 2


def test_read_session_non_dict_json_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """JSON array など dict 以外の valid JSON はスキップ + WARN."""
    session_file = tmp_path / "array.jsonl"
    session_file.write_text(
        '[1, 2, 3]\n{"type": "user", "text": "ok"}\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        session = read_session(session_file)

    assert len(session.events) == 1
    assert len(session.parse_warnings) == 1
    assert any("Unexpected JSON" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# read_session — content / extra フィールド保持
# ---------------------------------------------------------------------------


def test_read_session_content_field_preserved(tmp_path: Path) -> None:
    """assistant の content ブロックは SessionEvent.content に格納される."""
    session_file = tmp_path / "tool.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "type": "assistant",
                "content": [{"type": "tool_use", "id": "a1", "name": "Bash", "input": {}}],
            }
        ],
    )

    session = read_session(session_file)

    assert len(session.events) == 1
    ev = session.events[0]
    assert ev.content is not None
    assert ev.content[0]["name"] == "Bash"


def test_read_session_extra_fields_preserved_for_known_type(tmp_path: Path) -> None:
    """known type の extra フィールド (lineNo 等) も extra="allow" で保持される."""
    session_file = tmp_path / "s.jsonl"
    _write_jsonl(session_file, [{"type": "user", "text": "hi", "lineNo": 5, "custom": "val"}])

    session = read_session(session_file)

    dumped = session.events[0].model_dump()
    assert dumped.get("lineNo") == 5
    assert dumped.get("custom") == "val"


# ---------------------------------------------------------------------------
# read_target — None session/project edge cases
# ---------------------------------------------------------------------------


def test_read_target_session_mode_none_returns_empty(tmp_path: Path) -> None:
    """mode=session かつ session=None は空リストを返す."""
    spec = TargetSpec(mode="session", session=None)

    sessions = read_target(spec)

    assert sessions == []


def test_read_target_project_mode_none_returns_empty(tmp_path: Path) -> None:
    """mode=project かつ project=None は空リストを返す."""
    spec = TargetSpec(mode="project", project=None)

    sessions = read_target(spec)

    assert sessions == []


def test_read_target_since_filters_via_read_session(tmp_path: Path) -> None:
    """read_target が since を read_session に伝播することを検証."""
    root = tmp_path / "projects"
    root.mkdir()
    proj = root / "-home-user-proj"
    proj.mkdir()
    old_file = proj / "old.jsonl"
    new_file = proj / "new.jsonl"
    _write_jsonl(old_file, [{"type": "user", "text": "old"}])
    _write_jsonl(new_file, [{"type": "user", "text": "new"}])
    _set_mtime(old_file, datetime(2020, 1, 1))
    _set_mtime(new_file, datetime(2020, 12, 31))

    # Act
    spec = TargetSpec(mode="project", project=proj, since=date(2020, 6, 1))
    sessions = read_target(spec)

    # Assert: old file is skipped (empty events), new file is read
    by_id = {s.session_id: s for s in sessions}
    assert len(by_id["old"].events) == 0
    assert len(by_id["new"].events) == 1


def test_read_target_all_mode_nonexistent_root_returns_empty(tmp_path: Path) -> None:
    """存在しない projects_root で mode=all は空リストを返す."""
    spec = TargetSpec(mode="all", projects_root=tmp_path / "nonexistent")

    sessions = read_target(spec)

    assert sessions == []


def test_discover_sessions_empty_project_returns_empty(tmp_path: Path) -> None:
    """jsonl ファイルが存在しない project dir は空リストを返す."""
    proj = tmp_path / "empty-proj"
    proj.mkdir()
    (proj / "readme.txt").write_text("no jsonl here")

    sessions = discover_sessions(proj)

    assert sessions == []
