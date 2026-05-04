"""Classify PRs into themes and extract Knowledge via Anthropic Claude.

PRD F2 / OQ-02 / architecture.md §services/classifier.py:

- Drives the Anthropic Python SDK (``anthropic.Anthropic``) to invoke Claude
  on batches of ``models.PullRequest`` (default 5 PRs / call per architecture
  §パフォーマンス要件).
- The system prompt is split into two text blocks; the rules half carries
  ``cache_control={"type": "ephemeral"}`` so prompt caching can amortize the
  longest static segment across batches in a single ``run``.
- Themes are configurable (OQ-02): callers may pass an explicit list, or fall
  back to the canonical 5 axes (``CANONICAL_THEMES``). Anything Claude returns
  outside the allowed set is coerced to ``"other"``.
- Output ``Knowledge`` records must carry at least one ``source_urls`` entry
  (TC-F4-02). Records the model returns without citations are dropped.
- ``ANTHROPIC_API_KEY`` must be set when no client is injected; missing key
  raises ``AuthError`` from this module's exceptions.
- Per decision-defaults.md §ログ, any string we hand to ``logging`` is run
  through ``_redact`` to mask ``sk-ant-*`` / ``gh[ps]_*`` / known token-shaped
  values before emission.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, cast

from anthropic import Anthropic
from anthropic import AuthenticationError as _AnthropicAuthError

from repo_retrospecter.models.commit import Commit
from repo_retrospecter.models.knowledge import Knowledge
from repo_retrospecter.models.pull_request import PullRequest
from repo_retrospecter.models.theme import CANONICAL_THEMES
from repo_retrospecter.services.exceptions import AuthError, FetchError

if TYPE_CHECKING:
    from anthropic.types import Message, MessageParam, TextBlockParam

logger = logging.getLogger(__name__)

DEFAULT_MODEL: str = "claude-sonnet-4-5"
DEFAULT_BATCH_SIZE: int = 5
DEFAULT_MAX_TOKENS: int = 4096
DEFAULT_TIMEOUT_SEC: float = 120.0
API_KEY_ENV: str = "ANTHROPIC_API_KEY"

# Static framing for the system prompt. Kept module-level so callers can
# inspect / override in tests; treat as effectively constant at runtime.
SYSTEM_PROMPT_HEADER: str = (
    "You analyze merged GitHub pull requests (PR body + review thread + inline "
    "comments + suggestion blocks) and extract reusable engineering knowledge."
)

SYSTEM_PROMPT_RULES: str = """\
For each PR you receive, decide whether it carries any reusable knowledge. If so,
emit one or more Knowledge records. Each record MUST have:

- "rule": one sentence stating the rule, principle, or convention to follow.
- "anti_pattern": one sentence describing the wrong-way / failure mode the PR fixed
  or warned against. Use empty string only if truly inapplicable.
- "example": a short code snippet, command, or concrete illustration; use empty
  string if not applicable.
- "source_urls": non-empty list of GitHub URLs (PR or comment URLs) the rule was
  derived from. Records without at least one source URL are discarded by the
  caller, so always include the originating PR URL.
- "themes": list of theme tags drawn from the allowed theme set provided in the
  user message. Use "other" for anything that doesn't fit a more specific tag.

Output rules:

- Reply with raw JSON only. No prose, no markdown fences, no commentary.
- The top-level value is a JSON object: {"knowledge": [<record>, ...]}.
- If a PR has no transferable knowledge, contribute zero records (do not invent).
- Do not echo the PR text. Summarize.
- Never put email addresses, API keys, or tokens in any field; redact if present.
"""


# ---------------------------------------------------------------------------
# log redaction
# ---------------------------------------------------------------------------

# Patterns chosen per decision-defaults.md §ログ. Anchored to typical token shapes
# so unrelated text containing "sk-ant" or "ghp_" as a prefix still gets caught.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"gh[psoru]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)


def _redact(text: str) -> str:
    """Mask known credential shapes before they hit a logger."""
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub("***", out)
    # Also mask the active key value if it's in the environment, so accidental
    # echoes (e.g. error messages that include the header) don't leak it.
    key = os.environ.get(API_KEY_ENV)
    if key and len(key) >= 8 and key in out:
        out = out.replace(key, "***")
    return out


# ---------------------------------------------------------------------------
# client construction
# ---------------------------------------------------------------------------


def _build_client(timeout: float) -> Anthropic:
    """Construct an Anthropic client, mapping missing-key to ``AuthError``.

    The SDK reads ``ANTHROPIC_API_KEY`` itself, but its constructor only
    raises ``AnthropicError`` (without distinguishing missing-key from
    other init failures). We pre-check the environment so callers see a
    typed, redacted ``AuthError`` per decision-defaults.md §エラー処理.
    """
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise AuthError(
            f"{API_KEY_ENV} is not set. Export it before running classify "
            "(architecture.md §セキュリティアーキテクチャ)."
        )
    return Anthropic(api_key=key, timeout=timeout)


# ---------------------------------------------------------------------------
# prompt construction
# ---------------------------------------------------------------------------


def _resolve_themes(themes: list[str] | None) -> list[str]:
    """Apply OQ-02 default (canonical 5) when caller omits ``themes``."""
    if themes is None:
        return list(CANONICAL_THEMES)
    cleaned = [t.strip() for t in themes if t and t.strip()]
    return cleaned or list(CANONICAL_THEMES)


def _build_system_blocks(themes: list[str]) -> list[TextBlockParam]:
    """Two-block system prompt with prompt caching on the static rules half.

    The themes line is part of the cached block: a single ``run`` keeps the
    same theme list across all batches, so caching it is a clean win. Only
    the per-batch user message changes between calls.
    """
    rules = SYSTEM_PROMPT_RULES + "\nAllowed themes: " + ", ".join(themes) + "\n"
    return [
        {"type": "text", "text": SYSTEM_PROMPT_HEADER},
        {
            "type": "text",
            "text": rules,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _format_pr(pr: PullRequest) -> str:
    """Render one PR + its comment thread as plain text for the model."""
    lines: list[str] = [
        f"PR #{pr.number}: {pr.title}",
        f"URL: {pr.url}",
        f"Author: {pr.author}",
    ]
    body = pr.body.strip()
    if body:
        lines.append("Body:")
        lines.append(body)
    if pr.review_comments:
        lines.append("Review thread:")
        for c in pr.review_comments:
            lines.append(f"- [{c.kind}] {c.author}: {c.body.strip()}")
    if pr.inline_comments:
        lines.append("Inline / suggestion comments:")
        for c in pr.inline_comments:
            lines.append(f"- [{c.kind}] {c.author}: {c.body.strip()}")
    return "\n".join(lines)


def _build_user_message(batch: list[PullRequest], themes: list[str]) -> str:
    """User-turn payload: PR dump + explicit JSON contract reminder."""
    pr_blocks = "\n\n---\n\n".join(_format_pr(pr) for pr in batch)
    return (
        f"Allowed themes: {', '.join(themes)}\n\n"
        "Pull requests to analyze:\n\n"
        f"{pr_blocks}\n\n"
        'Respond with raw JSON: {"knowledge": [...]} only.'
    )


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------


def _extract_text(message: Message) -> str:
    """Concatenate text blocks from an Anthropic ``Message`` response."""
    chunks: list[str] = []
    for block in message.content:
        # ``block`` may be a TextBlock, ToolUseBlock, etc. We only want text.
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", ""))
    return "".join(chunks).strip()


def _strip_fences(text: str) -> str:
    """Tolerate models that wrap JSON in ```json fences despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence (``` or ```json) and the trailing fence.
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -len("```")]
    return stripped.strip()


def _coerce_themes(raw: Any, allowed: set[str]) -> list[str]:
    """Drop unknown themes; LLM-introduced labels collapse to ``other``."""
    if not isinstance(raw, list):
        return ["other"]
    out: list[str] = []
    for t in cast(list[Any], raw):
        if not isinstance(t, str):
            continue
        out.append(t if t in allowed else "other")
    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped = [t for t in out if not (t in seen or seen.add(t))]
    return deduped or ["other"]


def _parse_response(text: str, themes: list[str]) -> list[Knowledge]:
    """Decode the model's JSON envelope into Knowledge records.

    Records lacking ``source_urls`` are dropped here (TC-F4-02) so callers
    never have to second-guess citations. Malformed JSON raises so the
    caller sees a typed ``FetchError`` and can move on to the next batch.
    """
    if not text:
        return []
    payload_text = _strip_fences(text)
    try:
        payload: Any = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Anthropic response is not valid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise FetchError(
            f"Anthropic response top-level must be an object, got: {type(payload).__name__}"
        )
    raw_records = cast(dict[str, Any], payload).get("knowledge", [])
    if not isinstance(raw_records, list):
        raise FetchError("Anthropic response 'knowledge' field must be a list")

    allowed = set(themes)
    out: list[Knowledge] = []
    for raw in cast(list[Any], raw_records):
        if not isinstance(raw, dict):
            continue
        record = cast(dict[str, Any], raw)
        urls_raw = record.get("source_urls")
        if not isinstance(urls_raw, list):
            continue
        source_urls = [u for u in cast(list[Any], urls_raw) if isinstance(u, str) and u]
        if not source_urls:
            # TC-F4-02: skip records without provenance.
            continue
        rule = record.get("rule")
        anti = record.get("anti_pattern")
        example = record.get("example")
        out.append(
            Knowledge(
                rule=rule if isinstance(rule, str) else "",
                anti_pattern=anti if isinstance(anti, str) else "",
                example=example if isinstance(example, str) else "",
                source_urls=source_urls,
                themes=_coerce_themes(record.get("themes"), allowed),
            )
        )
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def classify_pull_requests(
    pull_requests: list[PullRequest],
    *,
    themes: list[str] | None = None,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    exclude_urls: set[str] | None = None,
) -> list[Knowledge]:
    """Classify ``pull_requests`` and extract Knowledge using Claude.

    Args:
        pull_requests: PRs (with comments) returned by ``services.fetcher``.
        themes: Allowed theme tags. ``None`` ⇒ canonical 5 (OQ-02 default).
        client: Optional pre-built ``Anthropic`` client (tests inject mocks).
        model: Claude model ID; defaults to the latest Sonnet.
        batch_size: PRs per LLM call (architecture.md §パフォーマンス: 5).
        max_tokens: Per-call response cap.
        timeout: Per-call timeout (decision-defaults.md §タイムアウト = 120s).

    Returns:
        Aggregated ``Knowledge`` records across all batches. Records without
        a source URL are filtered out (TC-F4-02).

    Raises:
        AuthError: ``ANTHROPIC_API_KEY`` is missing when ``client`` is None,
            or the API rejects the supplied credential.
        FetchError: Network / response parsing failure for any batch.
    """
    if not pull_requests:
        return []
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    resolved_themes = _resolve_themes(themes)
    system_blocks = _build_system_blocks(resolved_themes)

    api_client = client if client is not None else _build_client(timeout)

    skip = exclude_urls or set()
    targets = [pr for pr in pull_requests if pr.url not in skip]
    if not targets:
        return []

    knowledge: list[Knowledge] = []
    for start in range(0, len(targets), batch_size):
        batch = targets[start : start + batch_size]
        user_text = _build_user_message(batch, resolved_themes)
        messages: list[MessageParam] = [{"role": "user", "content": user_text}]
        try:
            response = api_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
            )
        except _AnthropicAuthError as exc:
            raise AuthError(f"Anthropic rejected the API key: {_redact(str(exc))}") from exc
        except Exception as exc:  # noqa: BLE001 - normalize to typed error
            raise FetchError(
                f"Anthropic call failed for PRs {[pr.number for pr in batch]}: {_redact(str(exc))}"
            ) from exc

        text = _extract_text(response)
        try:
            knowledge.extend(_parse_response(text, resolved_themes))
        except FetchError as exc:
            logger.warning(
                "skipping batch starting at PR #%s: %s",
                batch[0].number,
                _redact(str(exc)),
            )
            continue

    return knowledge


def _format_commit(commit: Commit) -> str:
    """Render one loose commit as plain text for the model.

    Loose commits have no comment thread; only sha / author / message are
    available. The model is told to extract Knowledge in the same shape as
    for PRs (Rule / Anti-pattern / Example / source URL).
    """
    body = commit.message.strip()
    return "\n".join(
        [
            f"Commit {commit.sha[:12]}",
            f"URL: {commit.url}",
            f"Author: {commit.author}",
            f"Committed at: {commit.committed_at.isoformat()}",
            "Message:",
            body,
        ]
    )


def _build_user_message_for_commits(batch: list[Commit], themes: list[str]) -> str:
    blocks = "\n\n---\n\n".join(_format_commit(c) for c in batch)
    return (
        f"Allowed themes: {', '.join(themes)}\n\n"
        "Loose commits (NOT associated with a merged PR) to analyze:\n\n"
        f"{blocks}\n\n"
        'Respond with raw JSON: {"knowledge": [...]} only. Use the commit URL '
        "as the source URL for each Knowledge record."
    )


def classify_commits(
    commits: list[Commit],
    *,
    themes: list[str] | None = None,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    exclude_urls: set[str] | None = None,
) -> list[Knowledge]:
    """Classify loose (PR-less) commits and extract Knowledge using Claude.

    Mirrors :func:`classify_pull_requests` but consumes ``Commit`` objects.
    Used for commits pushed directly to the default branch (no PR review),
    which still carry intent in their commit message.
    """
    if not commits:
        return []
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    resolved_themes = _resolve_themes(themes)
    system_blocks = _build_system_blocks(resolved_themes)
    api_client = client if client is not None else _build_client(timeout)

    skip = exclude_urls or set()
    targets = [c for c in commits if c.url not in skip]
    if not targets:
        return []

    knowledge: list[Knowledge] = []
    for start in range(0, len(targets), batch_size):
        batch = targets[start : start + batch_size]
        user_text = _build_user_message_for_commits(batch, resolved_themes)
        messages: list[MessageParam] = [{"role": "user", "content": user_text}]
        try:
            response = api_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
            )
        except _AnthropicAuthError as exc:
            raise AuthError(f"Anthropic rejected the API key: {_redact(str(exc))}") from exc
        except Exception as exc:  # noqa: BLE001 - normalize to typed error
            raise FetchError(
                f"Anthropic call failed for commits "
                f"{[c.sha[:7] for c in batch]}: {_redact(str(exc))}"
            ) from exc

        text = _extract_text(response)
        try:
            knowledge.extend(_parse_response(text, resolved_themes))
        except FetchError as exc:
            logger.warning(
                "skipping commit batch starting at %s: %s",
                batch[0].sha[:7],
                _redact(str(exc)),
            )
            continue

    return knowledge


__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SEC",
    "SYSTEM_PROMPT_HEADER",
    "SYSTEM_PROMPT_RULES",
    "classify_commits",
    "classify_pull_requests",
]
