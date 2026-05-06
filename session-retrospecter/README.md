**English** | [日本語](README.ja.md)

# session-retrospecter

[![CI](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml/badge.svg)](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Distill judgment signals from Claude Code session logs and surface actionable insights for retrospectives.

## Quick start

```bash
pipx install .

session-retrospecter run \
  --project ~/.claude/projects/my-project \
  --out retrospective.md \
  --ai-out retrospective-ai.md
```

## Privacy

> **Warning**
> Do **not** use this tool on confidential projects. Session logs may contain sensitive information.

- Token secrets (`sk-ant-*`, `ghp_*`, Bearer tokens) are redacted by default.
- File paths (e.g. `/home/user/...`) are **not** redacted by default; pass `--redact-paths` to mask them.
- **Do not commit `.retrospect/`** to version control — add it to `.gitignore`.
