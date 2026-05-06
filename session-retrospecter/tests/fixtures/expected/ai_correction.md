# Knowledge

## Knowledge: 不要な try/except を全関数に追加しない

- **Rule**: 不要な try/except を全関数に追加しない
- **Anti-pattern**: 全関数を try/except でラップする防御的プログラミング
- **Example**: `Wrapped every function in try/except.` → ユーザーが「don't add try/except where it's not needed」と訂正 → 必要な箇所だけに残す。
- **Sources**:
  - `session://correction-en-fixture#L3`
- **Themes**: `correction`
