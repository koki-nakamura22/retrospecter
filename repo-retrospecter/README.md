# repo-retrospecter

[![CI](https://github.com/OWNER/repo-retrospecter/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/repo-retrospecter/actions/workflows/ci.yml)

> Replace `OWNER` in the CI badge URL above once the repository is published.

LLM-powered retrospective generator. Turns a GitHub repository's PR / commit / review-comment history into:

- **Human-facing retrospective notes** — themed monthly Markdown for the team to skim
- **AI-consumable knowledge** — Rule / Anti-pattern / Example records that paste cleanly into `CLAUDE.md`, `SKILL.md`, or any agent-instruction file

so neither the people nor the AI coding agents on the project keep re-learning the same lessons.

## Why

PR reviews and commit decisions encode a ton of project-specific judgment ("we use middleware not `_redirects` for Cloudflare Pages domain redirects"). That judgment normally evaporates after the merge:

- Humans skim the PR list once and forget
- New teammates start at zero
- AI agents repeat the exact mistakes humans previously corrected, every session

`repo-retrospecter` extracts that judgment in a form that is **simultaneously useful for human review and parsable as agent context**, with citation URLs back to the originating PR / commit so nothing is hallucinated.

## Features

- **PR + comment-thread ingestion** — body, review comments, inline comments, and `gh suggestion` blocks (delegates auth to `gh auth`, no token in this tool)
- **Loose-commit support** — also covers default-branch commits that bypassed PR review (great for solo / direct-push repos)
- **Themed classification** — default 5 axes (`design_decision` / `review_rule` / `bug_pattern` / `refactor` / `other`); fully overridable per project
- **Two-stream output** — human Markdown + AI-shaped knowledge from the same run, with mandatory source URLs (TC-F4-02)
- **Incremental updates (`--append`)** — re-running fetches only the delta and skips re-classifying items already covered (ADR-0005)
- **Cache-first** — intermediate JSON cache so re-rendering or switching output formats does not re-call the LLM (ADR-0003)
- **Plugin-shaped renderers** — `human` / `ai` today, easy to add `skill` / custom Jinja2 templates later (ADR-0004)

## Requirements

- Python **3.11+**
- [`gh` CLI](https://cli.github.com/) **2.0+** with `gh auth login` completed (the tool delegates all GitHub auth to `gh`; no PAT or env var here)
- An Anthropic API key (`ANTHROPIC_API_KEY`)
- macOS / Linux / WSL2. Windows native is best-effort (CI runs Linux).

## Install

```bash
# Recommended: pipx for global install
pipx install repo-retrospecter

# Or with uv
uv tool install repo-retrospecter
```

For local development:

```bash
git clone https://github.com/OWNER/repo-retrospecter.git
cd repo-retrospecter
uv sync                    # creates .venv with deps + dev deps
uv run repo-retrospecter --help
```

## Setup

Copy the example env file and add your Anthropic API key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

`python-dotenv` is bundled, so the CLI reads `.env` from the current directory automatically. Alternatively, export the variable in your shell or use a tool like [`direnv`](https://direnv.net/).

## Quick start

```bash
# Fetch the last 30 merged PRs + 30 default-branch commits, classify, render both outputs
uv run repo-retrospecter run \
  --repo OWNER/example-repo \
  --out learnings/2026-05.md \
  --ai-out learnings/ai-knowledge.md
```

That single call:

1. `gh` fetches PRs + their review/inline/suggestion comments → `.retrospect/cache.json`
2. `gh api repos/.../commits` fetches the most recent commits, drops those tied to a collected PR
3. Anthropic Claude classifies the bundle into themes and extracts `Knowledge` records (with mandatory source URLs)
4. Two Markdown files are rendered from the cache

## Subcommands

| Command | Purpose |
|---|---|
| `run`      | Fetch + classify + render in one shot |
| `fetch`    | Just persist PR / commit data to the cache JSON (no LLM call) |
| `generate` | Read an existing cache, classify any new items, render Markdown |

`fetch` + `generate` lets you separate cost (LLM is only in `generate`) and iterate on `--out` / `--ai-out` paths or theme settings without re-fetching.

## Common options

```text
--repo OWNER/NAME          Target repository (required unless set in --config)
--last N                   Cap on merged PRs (default 30)
--last-commits N           Cap on default-branch commits scanned for loose-commit detection (default 30)
--since YYYY-MM-DD         Lower bound for both PRs and commits (date granularity)
--no-loose-commits         Skip the loose-commit pass entirely (PR-only mode)
--append                   Incremental update — see below
--cache PATH               Cache JSON path (default .retrospect/cache.json)
--out PATH                 Human-facing Markdown output
--ai-out PATH              AI-facing Markdown output
--config PATH              JSON / TOML defaults file
--force                    Overwrite existing output files
-v / --verbose             DEBUG logging (API keys are redacted)
-q / --quiet               WARN+ only
```

## Incremental updates (`--append`)

After the first full run, subsequent runs can take only the delta:

```bash
# Initial fetch (full)
uv run repo-retrospecter run --repo X --out h.md --ai-out a.md

# Days/weeks later: pick up only what's new since the last run
uv run repo-retrospecter run --repo X --append --out h.md --ai-out a.md
```

What `--append` does:

- Reads existing `cache.json`, derives a `since` from the latest `merged_at` / `committed_at`
- Fetches only newer PRs / commits, merges by `number` / `sha` (existing entries win)
- Calls the LLM only for items whose URL is not already covered by an existing `Knowledge.source_urls`
- Falls back to a full fetch (with a warning) if the cache file is missing
- An explicit `--since` overrides the auto-derived bound

So `repo-retrospecter run --repo X --append` is the steady-state weekly command.

## Configuration file

Any flag can be put into a JSON or TOML file:

```toml
# repo-retrospecter.config.toml
repo = "OWNER/example-repo"
last = 30
last_commits = 50
themes = ["security", "performance", "design_decision", "other"]
out = "docs/learnings/latest.md"
ai_out = "docs/learnings/ai-knowledge.md"
```

```bash
uv run repo-retrospecter run --config repo-retrospecter.config.toml
```

CLI flags always override the config file.

## Output examples

### Human Markdown

```markdown
# 振り返り — OWNER/example-repo
- 対象 PR 数: 5
- 対象 直 push コミット数: 0
- 抽出ナレッジ数: 12

## 主要設計判断

### Use middleware or functions for domain redirects on Cloudflare Pages instead of static _redirects files.
- 避けるべき: Creating a _redirects file for domain redirects on Cloudflare Pages, which is not supported.
- 例: ...
- 出典: https://github.com/.../commit/b3f553c

## 頻出レビュー指摘 Top 5
1. **Use pre-commit hooks (ruff lint, ruff format, mypy, pytest) ...**
   - https://github.com/.../pull/15
```

### AI Markdown

```markdown
### Rule: Use middleware or functions for domain redirects on Cloudflare Pages.
- Themes: design_decision, bug_pattern
- Anti-pattern: Creating a _redirects file (not supported by the platform).
- Example:
  ```js
  if (url.hostname.endsWith('.pages.dev')) { return Response.redirect(...); }
  ```
- Sources:
  - https://github.com/.../commit/b3f553c
```

Drop the AI file (or the relevant section) into `CLAUDE.md` / a `SKILL.md` and your agent picks up project-specific judgment without you re-typing it.

## Privacy / security

- GitHub auth: delegated to `gh auth login`. This tool **never sees a PAT or token**.
- Anthropic API key: read from `ANTHROPIC_API_KEY` only. Redacted from all logs (decision-defaults §ログ).
- PII: GitHub `login` is kept; `email` and other identifying fields are stripped before reaching the cache or output.
- LLM data flow: PR bodies and comment text are sent to Anthropic for classification. Use `--no-loose-commits` and the `themes` config to scope what gets sent. **Don't run this on a repo whose PR contents you cannot share with a third-party LLM provider.**

## Notes

A CLI was a deliberate choice over packaging this as a Claude Code skill. The interesting bits are: the tool collects PR / commit / comment data via `gh` outside the LLM and only the trimmed, normalized payload is sent to Claude — so the heavy raw text never enters an interactive session's context window. The system prompt is held constant per run and reused via prompt caching across batches, which makes repeat invocations and `--append` runs cheap. Both effects also mean the work runs on its own metered Anthropic budget rather than against an interactive Claude Code rate limit, and the same binary fits naturally into cron / CI / multi-user distribution. A skill remains a great fit for one-shot, conversational "summarize last week" questions; this CLI is the better fit when the same retrospective will be re-run, scheduled, or shared.

## License

TBD.
