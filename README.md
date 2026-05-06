**English** | [日本語](README.ja.md)

# retrospecter

> Tools that retrospect on engineering history and turn it into Knowledge that humans **and** AI coding agents can re-use.

## Philosophy

PR reviews, commit messages, and AI-coding-agent transcripts are full of project-specific judgment — "we redirect via middleware not `_redirects` on Cloudflare Pages", "skip mocks in this integration suite, we got burned last quarter". That judgment normally evaporates after the merge or the session ends:

- Humans skim the history once and forget
- New teammates start at zero
- AI coding agents repeat the exact mistakes humans previously corrected, every session

`retrospecter` is a family of CLI tools that read engineering history from various sources and **distill** it into two parallel artifacts: a human-facing retrospective note, and a machine-parsable knowledge file (`Rule` / `Anti-pattern` / `Example` records, with mandatory citation URLs). The AI artifact is shaped to drop straight into a `CLAUDE.md`, `SKILL.md`, or any agent-instruction file, so judgment compounds instead of evaporating.

## Apps

| App | Source of history | Status |
|---|---|---|
| [repo-retrospecter](./repo-retrospecter) | GitHub PRs / commits / review comments (via `gh`) | Released |
| [session-retrospecter](./session-retrospecter) | Claude Code session logs (`~/.claude/projects/*.jsonl`) | Released |
| retrospect-core | Shared library: `Knowledge` model, classifier wrapper, cache base, renderer base | Phase 3 (extracted only after the second app's shared surface stabilizes) |

Each app is independently installable (`pipx install <app>`); the umbrella is a workspace, not a product.

## Repository layout

```
retrospecter/
├── README.md
├── repo-retrospecter/        ← First app: GitHub history → retrospective + AI knowledge
│   ├── README.md             ← Detailed usage / install / examples
│   ├── pyproject.toml
│   ├── src/repo_retrospecter/
│   └── tests/
└── session-retrospecter/     ← Second app: Claude Code session logs → retrospective + AI knowledge
    ├── README.md
    ├── pyproject.toml
    ├── src/session_retrospecter/
    └── tests/
```

If a `retrospect-core` package is extracted (per the policy below), it will sit at the same level as the apps.

## Shared core extraction policy

We deliberately do **not** extract a shared `retrospect-core` package up-front. The current single app should not pay an abstraction tax for a sibling that does not exist yet.

The plan progresses through three phases:

1. **Phase 1 (done)** — Single app under the umbrella. No core.
2. **Phase 2 (done)** — `repo-retrospecter` shipped on its own; still no core.
3. **Phase 3 (current)** — `session-retrospecter` has landed alongside `repo-retrospecter`. The two apps are intentionally still duplicating their shared parts so the genuinely-common surface can be observed from two real call sites before being extracted. Per the "rule of three", `retrospect-core` will only be carved out as a uv workspace member once that duplication has stabilized — not on speculation.

Likely shared surface (kept on the watchlist for Phase 3):

- `Knowledge` model
- Anthropic SDK wrapper (system-prompt + cache_control + JSON parsing)
- Cache base (schema_version + `--append` semantics)
- Renderer base (`Protocol` + jinja2 helpers)
- CLI plumbing (dotenv loading, logging redact, exception → `ClickException` translation)

Likely never-shared (will stay per-app):

- The fetcher — each app's source format is fundamentally different (`gh` subprocess vs JSONL transcript reader)
- Domain models — `PullRequest` / `Commit` vs `ConversationTurn` / `ToolCall`
- Classifier system prompt and theme axes
- Renderer template structure
- Privacy posture (session logs are far more sensitive than public PRs)

## Privacy / Security

These tools send engineering history to a third-party LLM provider (Anthropic) for classification.

Common rules every app in this family must enforce:

- **GitHub authentication is delegated to `gh auth login`.** No app in this family stores or reads a GitHub token directly.
- **Anthropic API key is read from `ANTHROPIC_API_KEY` only.** It is redacted from all log output before emission.
- **PII is stripped at the boundary.** GitHub `login` / Claude Code transcript-author identifiers are kept; emails and other identifying fields are not propagated into the cache or output.
- **Do not run this on history you cannot share with a third-party LLM.** This applies especially to `session-retrospecter`, where transcripts may contain operational paths, error messages, and inadvertently-pasted secrets.

App-specific privacy details (e.g. transcript redaction options) live in each app's README.

## Install / Usage

Each app installs and runs independently. See the per-app README:

- [repo-retrospecter — README](./repo-retrospecter/README.md)
- [session-retrospecter — README](./session-retrospecter/README.md)

## Contributing

TBD. The umbrella is currently a single-author workspace; contribution guidelines will be added when external contributors arrive (or when the umbrella stops being a hobby).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
