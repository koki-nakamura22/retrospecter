[English](README.md) | **日本語**

# repo-retrospecter

[![CI](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml/badge.svg)](https://github.com/koki-nakamura22/retrospecter/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](#license)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

LLM ベースの振り返り生成 CLI。GitHub リポジトリの PR / コミット / レビューコメント履歴を以下の 2 系統に変換します:

- **人間向け振り返りノート** — 月次でチームが眺めるためのテーマ別 Markdown
- **AI 向けナレッジ** — `CLAUDE.md` / `SKILL.md` 等のエージェント指示ファイルにそのまま貼れる Rule / Anti-pattern / Example レコード

これで人間も AI コーディングエージェントも、同じ学びを毎回ゼロからやり直さずに済みます。

## なぜ作ったか

PR レビューやコミットの判断には、プロジェクト固有の知見が大量に詰まっています (「Cloudflare Pages のドメインリダイレクトは `_redirects` ではなく middleware でやる」等)。merge と同時に普通は蒸発します:

- 人間は PR 一覧を一度眺めて忘れる
- 新メンバーはゼロから始める
- AI エージェントは、人間が以前訂正したミスを毎セッション繰り返す

`repo-retrospecter` はその判断を、**人間レビュー用としても agent コンテキストとしても両用可能な形**で抽出します。出典 URL を必ず PR / コミットへ紐付けるので、ハルシネーションは起きません。

## 機能

- **PR + コメントスレッド取得** — 本文 / review comment / inline comment / `gh suggestion` ブロック (認証は `gh auth` に委譲、本ツールはトークン非保持)
- **Loose commit 対応** — PR を経由しない default ブランチへの直 push commit も対象 (個人開発 / 直 push 中心リポに有効)
- **テーマ分類** — 既定 5 軸 (`design_decision` / `review_rule` / `bug_pattern` / `refactor` / `other`)、プロジェクト毎に上書き可
- **2 系統出力** — 同じ実行で human Markdown と AI 向けナレッジを生成、出典 URL 必須 (TC-F4-02)
- **差分更新 (`--append`)** — 再実行時は差分のみ取得 + 既分類項目はスキップ (ADR-0005)
- **Cache first** — 中間 JSON cache により再描画 / 出力形式切替で LLM を再呼び出ししない (ADR-0003)
- **Plugin 化された renderer** — 現状 `human` / `ai`、将来 `skill` や独自 jinja2 テンプレートを追加容易 (ADR-0004)

## 必要要件

- Python **3.11+**
- [`gh` CLI](https://cli.github.com/) **2.0+** + `gh auth login` 完了済み (本ツールは GitHub 認証を全て `gh` に委譲、PAT / 環境変数は不要)
- Anthropic API キー (`ANTHROPIC_API_KEY`)
- macOS / Linux / WSL2。Windows ネイティブは best-effort (CI は Linux のみ)

## インストール

```bash
# 推奨: pipx でグローバルインストール
pipx install repo-retrospecter

# または uv tool で
uv tool install repo-retrospecter
```

ローカル開発の場合:

```bash
git clone https://github.com/koki-nakamura22/retrospecter.git
cd repo-retrospecter
uv sync                    # .venv 作成 + 本体 / dev 依存をインストール
uv run repo-retrospecter --help
```

## セットアップ

`.env` テンプレートをコピーして API キーを設定:

```bash
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY=sk-ant-... を設定
```

`python-dotenv` を同梱しているので、CLI は CWD の `.env` を自動読込します。シェル `export` や [`direnv`](https://direnv.net/) でも代替可。

## クイックスタート

```bash
# 直近 30 件の merged PR + 30 件の default ブランチ commit を取得・分類・両系統出力
uv run repo-retrospecter run \
  --repo OWNER/example-repo \
  --out learnings/2026-05.md \
  --ai-out learnings/ai-knowledge.md
```

この 1 コマンドで:

1. `gh` が PR + review/inline/suggestion comment を取得 → `.retrospect/cache.json`
2. `gh api repos/.../commits` で直近 commit を取得、collected PR と紐付くものは除外
3. Anthropic Claude が一括分類して `Knowledge` レコード抽出 (出典 URL 必須)
4. cache から 2 つの Markdown をレンダリング

## サブコマンド

| コマンド | 用途 |
|---|---|
| `run`      | fetch + classify + render を一気通貫 |
| `fetch`    | PR / commit データを cache JSON に保存するだけ (LLM 呼び出しなし) |
| `generate` | 既存 cache を読込、新規項目だけ分類して Markdown レンダリング |

`fetch` + `generate` を分けることで、コスト発生 (LLM) を `generate` だけに局所化でき、`--out` / `--ai-out` パスやテーマ設定の試行錯誤を再 fetch なしで回せます。

## よく使うオプション

```text
--repo OWNER/NAME          対象リポジトリ (--config 指定がなければ必須)
--last N                   merged PR の上限 (既定 30)
--last-commits N           loose-commit 検出のため見る default ブランチ commit 数 (既定 30)
--since YYYY-MM-DD         PR / commit 両方の下限日付
--no-loose-commits         loose commit パスを完全スキップ (PR のみモード)
--append                   差分更新 (下記参照)
--cache PATH               cache JSON パス (既定 .retrospect/cache.json)
--out PATH                 人間向け Markdown 出力先
--ai-out PATH              AI 向け Markdown 出力先
--config PATH              JSON / TOML 設定ファイル
--force                    既存出力ファイルを上書き
-v / --verbose             DEBUG ログ (API キーは redact 済み)
-q / --quiet               WARN+ のみ
```

## 差分更新 (`--append`)

初回フル取得後の運用では差分だけ取り込めます:

```bash
# 初回 (フル取得)
uv run repo-retrospecter run --repo X --out h.md --ai-out a.md

# 数日 / 数週後: 前回以降の新着分だけを取り込み
uv run repo-retrospecter run --repo X --append --out h.md --ai-out a.md
```

`--append` の動作:

- 既存 `cache.json` を読込、最新 `merged_at` / `committed_at` から `since` を自動算出
- 新着 PR / commit のみ fetch、`number` / `sha` でマージ (既存上書きしない)
- 既出 `Knowledge.source_urls` に含まれる URL の項目はスキップ、新規のみ classifier 呼び出し
- cache が無ければ警告 + 全件取得にフォールバック
- 明示 `--since` は自動算出より優先

つまり週次運用は `repo-retrospecter run --repo X --append` の 1 コマンドで完結します。

## 設定ファイル

任意の flag を JSON / TOML で外出し可能:

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

CLI flag は常に config ファイルより優先されます。

## 出力例

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

````markdown
### Rule: Use middleware or functions for domain redirects on Cloudflare Pages.
- Themes: design_decision, bug_pattern
- Anti-pattern: Creating a _redirects file (not supported by the platform).
- Example:
  ```js
  if (url.hostname.endsWith('.pages.dev')) { return Response.redirect(...); }
  ```
- Sources:
  - https://github.com/.../commit/b3f553c
````

AI 向けファイル (または該当セクション) を `CLAUDE.md` / `SKILL.md` に貼れば、エージェントがプロジェクト固有の判断をこちらが書き直さずに拾えます。

## プライバシー / セキュリティ

- GitHub 認証: `gh auth login` に委譲。本ツールは **PAT / トークンを保持しません**。
- Anthropic API キー: `ANTHROPIC_API_KEY` 環境変数のみから読込。全ログ出力前に redact 済み (decision-defaults §ログ)。
- PII: GitHub `login` のみ保持。`email` その他の識別フィールドは cache / 出力前に除去。
- LLM データフロー: PR 本文とコメント本文が分類のため Anthropic に送信されます。送信範囲は `--no-loose-commits` や `themes` 設定で調整可能。**第三者 LLM プロバイダに共有できない PR 内容を持つリポでは実行しないでください。**

## Notes

Claude Code の skill としてではなく CLI として実装したのは意図的な選択です。要点としては、本ツールは PR / commit / コメントデータを `gh` 経由で LLM の外で集めて、整形・正規化済みのペイロードだけを Claude へ渡します — そのため大きな生テキストが対話セッションの context window に乗りません。system prompt は実行内で固定され prompt caching でバッチ間共有されるので、繰り返し実行や `--append` 実行が安く済みます。これらの効果は同時に、Claude Code の対話レート制限ではなく独立した Anthropic 課金枠で動くこと、同じバイナリが cron / CI / マルチユーザ配布に自然に乗ることも意味します。skill は会話の流れで「先週分まとめて」のような one-shot 用途には依然として良い選択です。本 CLI は同じ振り返りを再実行 / スケジュール / 共有する用途で勝ります。

## License

Apache License 2.0 — [LICENSE](../LICENSE) を参照。
