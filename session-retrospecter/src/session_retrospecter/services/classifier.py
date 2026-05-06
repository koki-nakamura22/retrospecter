"""services.classifier — ExtractionCandidate[] を Knowledge[] へ LLM 分類する."""

from __future__ import annotations

import json
import logging
import os
import re

import anthropic
from anthropic.types import TextBlock

from session_retrospecter.models.extraction import ExtractionCandidate
from session_retrospecter.models.knowledge import DEFAULT_THEMES, Knowledge
from session_retrospecter.services.exceptions import ClassifierError, RateLimitError

__all__ = ["DEFAULT_SYSTEM_PROMPT", "classify"]

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-4-5"

DEFAULT_SYSTEM_PROMPT = """\
You are session-retrospecter, a tool that distills judgment signals from Claude Code \
session dialog logs.

session-retrospecter analyzes AI-human conversations to extract four types of signals:
- correction: the user corrected the AI's behavior
- validated_pattern: the user confirmed a good pattern
- tool_pitfall: a tool call failed and was retried with different input
- decision_rationale: the AI explained a significant decision in depth

For each ExtractionCandidate provided, output one Knowledge JSON object with these \
exact fields:
  "rule"         — positive guideline: what TO do (non-empty string)
  "anti_pattern" — what NOT to do (non-empty string)
  "example"      — concrete example from the session context (non-empty string)
  "sources"      — list of citations, MUST include ≥1 from the candidate
  "themes"       — list of applicable themes

Known themes: correction, validated_pattern, tool_pitfall, decision_rationale, other
Citation format: session://<session-id>#L<line-no>

CRITICAL: sources MUST contain at least 1 citation from the input candidate.
Output ONLY a valid JSON array of Knowledge objects. No markdown, no explanations."""


def _build_user_message(candidates: list[ExtractionCandidate], themes: list[str]) -> str:
    parts = [
        f"Available themes: {', '.join(themes)}\n",
        "Classify each candidate into a Knowledge item. Return a JSON array.\n",
    ]
    for i, c in enumerate(candidates, 1):
        parts.append(
            f"\nCandidate {i}:\n"
            f"  kind: {c.kind}\n"
            f"  citation: {c.citation}\n"
            f"  context: {c.context}\n"
            f"  metadata: {json.dumps(c.metadata)}\n"
        )
    return "".join(parts)


def _extract_json_array(text: str) -> list[dict[str, object]]:
    """Extract a JSON array from LLM response text."""
    try:
        data = json.loads(text.strip())
        if isinstance(data, list):
            return data  # type: ignore[return-value]
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        data = json.loads(match.group())
        if isinstance(data, list):
            return data  # type: ignore[return-value]

    raise ClassifierError(f"LLM response is not a JSON array: {text[:200]}")


def _parse_knowledge(
    raw: str,
    candidates: list[ExtractionCandidate],
) -> list[Knowledge]:
    items = _extract_json_array(raw)
    results: list[Knowledge] = []
    for i, item in enumerate(items):
        try:
            k = Knowledge.model_validate(item)
            results.append(k)
        except Exception as exc:
            citation = candidates[i].citation if i < len(candidates) else "unknown"
            logger.warning("knowledge parse failed for %s: %s", citation, exc)
    return results


def _api_call(
    client: anthropic.Anthropic,
    candidates: list[ExtractionCandidate],
    themes: list[str],
) -> list[Knowledge]:
    system_content: list[anthropic.types.TextBlockParam] = [
        {
            "type": "text",
            "text": DEFAULT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    user_text = _build_user_message(candidates, themes)

    response = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=system_content,
        messages=[{"role": "user", "content": user_text}],
    )

    text = ""
    for block in response.content:
        if isinstance(block, TextBlock):
            text = block.text
            break

    return _parse_knowledge(text, candidates)


def classify(
    candidates: list[ExtractionCandidate],
    *,
    themes: list[str] | None = None,
    cached_citations: set[str] | None = None,
) -> list[Knowledge]:
    """ExtractionCandidate[] を LLM 分類して Knowledge[] を返す.

    cached_citations に含まれる candidate は LLM call をスキップ.
    Anthropic 429 は RateLimitError, それ以外の API 失敗は WARN ログ + 空リスト返却.
    """
    if cached_citations is None:
        cached_citations = set()
    if themes is None:
        themes = list(DEFAULT_THEMES)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClassifierError("ANTHROPIC_API_KEY を環境変数か .env に設定してください")

    new_candidates = [c for c in candidates if c.citation not in cached_citations]
    if not new_candidates:
        logger.debug("all %d candidates cached; skipping LLM call", len(candidates))
        return []

    client = anthropic.Anthropic(api_key=api_key)

    try:
        return _api_call(client, new_candidates, themes)
    except anthropic.RateLimitError as exc:
        raise RateLimitError(str(exc)) from exc
    except anthropic.APIError as exc:
        logger.warning("API call failed (1/2): %s — retrying", exc)
        try:
            return _api_call(client, new_candidates, themes)
        except anthropic.RateLimitError as exc2:
            raise RateLimitError(str(exc2)) from exc2
        except anthropic.APIError as exc2:
            logger.warning(
                "API call failed (2/2): %s — skipping %d candidates",
                exc2,
                len(new_candidates),
            )
            return []
