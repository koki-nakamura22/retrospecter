# session-retrospecter

[![CI](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml/badge.svg)](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Claude Code のセッションログから判断シグナルを抽出し、振り返りに活用できる洞察を提供します。

## クイックスタート

```bash
pipx install .

session-retrospecter run \
  --project ~/.claude/projects/my-project \
  --out retrospective.md \
  --ai-out retrospective-ai.md
```

## プライバシー

> **警告**
> 機密プロジェクトには使用しないでください。セッションログには機密情報が含まれている場合があります。

- トークン (`sk-ant-*`、`ghp_*`、Bearer トークン) はデフォルトで自動的にマスクされます。
- ファイルパス (`/home/user/...` 等) はデフォルトではマスクされません。マスクするには `--redact-paths` を指定してください。
- **`.retrospect/` はバージョン管理にコミットしないでください** — `.gitignore` に追加してください。
