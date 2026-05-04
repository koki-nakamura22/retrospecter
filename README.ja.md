[English](README.md) | **日本語**

# retrospecter

> エンジニアリングの履歴を振り返り、人間と AI コーディングエージェントの双方が再利用できる Knowledge へ蒸留するツール群。

## Philosophy

PR レビュー / コミットメッセージ / AI コーディングエージェントの会話履歴には、プロジェクト固有の判断が大量に埋もれています — 「Cloudflare Pages では `_redirects` ではなく middleware でリダイレクトする」「この統合テストでは mock を使わない、前期それで痛い目に遭ったから」といった類の。merge やセッション終了と同時に、その判断は普通は蒸発します:

- 人間は履歴を一度眺めて忘れる
- 新しいメンバーはゼロから始める
- AI コーディングエージェントは、人間が以前直したミスを毎セッション繰り返す

`retrospecter` は CLI ツール群で、様々なソースからエンジニアリング履歴を読み取り、2 つの並行成果物に**蒸留**します: 人間向けの振り返りノートと、機械可読な Knowledge ファイル (`Rule` / `Anti-pattern` / `Example` レコード、出典 URL 必須)。AI 向け成果物は `CLAUDE.md` / `SKILL.md` / その他エージェント指示ファイルへそのまま貼れる形に整形済みなので、判断が蒸発せず**蓄積**していきます。

## Apps

| App | 履歴のソース | ステータス |
|---|---|---|
| [repo-retrospecter](./repo-retrospecter) | GitHub PR / コミット / レビューコメント (`gh` 経由) | Released |
| session-retrospecter | Claude Code セッションログ (`~/.claude/projects/*.jsonl`) | Planned |
| retrospect-core | 共通ライブラリ: `Knowledge` モデル / classifier ラッパ / cache 基底 / renderer 基底 | Phase 3 (2 つ目アプリ着手後に抽出) |

各アプリは独立して install 可能 (`pipx install <app>`) です。umbrella はプロダクトではなくワークスペースです。

## Repository layout

```
retrospecter/
├── README.md
└── repo-retrospecter/        ← 1 つ目のアプリ (現状唯一)
    ├── README.md             ← 詳細な使い方 / install / 例
    ├── pyproject.toml
    ├── src/repo_retrospecter/
    └── tests/
```

`session-retrospecter` が登場したら兄弟ディレクトリとして並びます。`retrospect-core` パッケージを抽出する場合 (下記ポリシー参照) も同階層に配置します。

## Shared core extraction policy

意図的に `retrospect-core` パッケージは**最初から抽出しません**。今ある単一アプリが、まだ存在しない兄弟のために抽象化コストを支払うべきではないという判断です。

3 段階で漸進的に進めます:

1. **Phase 1 (現在)** — umbrella 配下にアプリ 1 つ。core なし。umbrella の唯一のコンテンツはこの README。
2. **Phase 2 (公開時)** — `repo-retrospecter` を単独で出荷 (PyPI / GitHub)。依然 core なし。
3. **Phase 3 (2 つ目アプリ着手時)** — `session-retrospecter` の構築が始まり、本当に共通する部分が 2 つの実コードから見えた段階で `retrospect-core` を uv workspace member として抽出。"rule of three" — 推測ではなく、2 箇所で実際に重複が観測されてから抽象化する原則に従う。

#### 共通化候補 (Phase 3 で抽出を検討)

- `Knowledge` モデル
- Anthropic SDK ラッパ (system prompt + cache_control + JSON 解析)
- Cache 基底 (schema_version + `--append` 挙動)
- Renderer 基盤 (`Protocol` + jinja2 ヘルパ)
- CLI 共通部 (dotenv 読込 / logging redact / 例外 → `ClickException` 変換)

#### 共通化されない部分 (各アプリで独自実装)

- fetcher — ソース形式が根本的に違う (`gh` subprocess vs JSONL transcript reader)
- ドメインモデル — `PullRequest` / `Commit` vs `ConversationTurn` / `ToolCall`
- classifier の system prompt と分類軸
- renderer のテンプレート構成
- プライバシー姿勢 (セッションログは公開 PR より遥かに機密度が高い)

## Privacy / Security

これらのツールは、エンジニアリング履歴を分類のため第三者 LLM プロバイダ (Anthropic) へ送信します。

family 共通の必須ルール:

- **GitHub 認証は `gh auth login` に委譲。** family のどのアプリも GitHub トークンを直接保持・読み取りしない。
- **Anthropic API キーは `ANTHROPIC_API_KEY` 環境変数からのみ読込。** 全ログ出力前に redact 済み。
- **境界で PII を除去。** GitHub `login` / Claude Code transcript の author 識別子は保持。email その他の識別フィールドは cache や出力に伝播させない。
- **第三者 LLM へ共有できない履歴に対しては実行しない。** 特に `session-retrospecter` で重要 (transcript には運用パス / エラーメッセージ / うっかり貼られた秘密情報等が含まれる可能性)。

アプリ固有のプライバシー詳細 (例: transcript redaction オプション) は各アプリの README に記載。

## Install / Usage

各アプリは独立して install / 実行可能です。アプリごとの README を参照:

- [repo-retrospecter — README](./repo-retrospecter/README.ja.md)

## Contributing

TBD。umbrella は現在シングル作者のワークスペース。外部コントリビュータが現れたタイミング (または umbrella が趣味でなくなったタイミング) でガイドラインを追加予定。

## License

TBD。
