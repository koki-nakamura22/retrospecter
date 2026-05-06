"""services.extractor — Session から 4 種シグナル候補を抽出."""

from __future__ import annotations

import logging
from typing import Any

from session_retrospecter.models.event import Session, SessionEvent
from session_retrospecter.models.extraction import ExtractionCandidate, Kind

__all__ = [
    "APPROVAL_LEXICON",
    "APPROVAL_MAX_CHARS",
    "CORRECTION_LEXICON",
    "DECISION_KEYWORDS",
    "DECISION_MIN_CHARS",
    "extract",
]

logger = logging.getLogger(__name__)

CORRECTION_LEXICON: frozenset[str] = frozenset(
    {"don't", "no", "stop", "違う", "そうじゃ", "やめて", "ダメ"}
)
APPROVAL_LEXICON: frozenset[str] = frozenset(
    {"yes", "perfect", "exactly", "good", "その通り", "OK"}
)
DECISION_KEYWORDS: frozenset[str] = frozenset({"because", "since", "だから", "理由", "trade-off"})

DECISION_MIN_CHARS: int = 800
APPROVAL_MAX_CHARS: int = 80

# Event types that are not conversation turns (skipped when looking for preceding turn)
_NON_TURN_TYPES: frozenset[str] = frozenset(
    {
        "attachment",
        "file-history-snapshot",
        "system",
        "permission-mode",
        "last-prompt",
        "ai-title",
    }
)


def _text_blocks(ev: SessionEvent) -> str:
    """Concatenate text content from an event (text field + content[].type=="text" blocks)."""
    parts: list[str] = []
    if ev.text:
        parts.append(ev.text)
    if ev.content:
        for block in ev.content:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(str(text))
    return "\n".join(parts)


def _format_turn(ev: SessionEvent) -> str:
    text = _text_blocks(ev)
    if not text and ev.content:
        types = [str(b.get("type", "?")) for b in ev.content]
        text = f"[content: {', '.join(types)}]"
    return f"[{ev.type}:L{ev.line_no}] {text}"


def _lexicon_hits_in_order(lexicon: frozenset[str], text: str) -> list[str]:
    """Return lexicon words found in text, ordered by first occurrence position."""
    return sorted(
        (w for w in lexicon if w in text),
        key=lambda w: text.find(w),
    )


def _find_preceding_assistant(events: list[SessionEvent], idx: int) -> SessionEvent | None:
    """Return the nearest preceding assistant event, skipping non-turn event types."""
    for j in range(idx - 1, -1, -1):
        ev_type = events[j].type
        if ev_type in _NON_TURN_TYPES:
            continue
        if ev_type == "assistant":
            return events[j]
        return None
    return None


def _make_candidate(
    sid: str,
    kind: Kind,
    line_no: int,
    context: str,
    metadata: dict[str, Any],
) -> ExtractionCandidate:
    return ExtractionCandidate(
        kind=kind,
        session_id=sid,
        line_no=line_no,
        context=context,
        metadata=metadata,
        citation=f"session://{sid}#L{line_no}",
    )


def extract(session: Session) -> list[ExtractionCandidate]:
    """Session 内 events から 4 種シグナル候補を抽出 (line_no 昇順)."""
    candidates: list[ExtractionCandidate] = []
    events = session.events
    sid = session.session_id

    for i, ev in enumerate(events):
        # --- correction ---
        if ev.type == "user" and ev.text is not None:
            hits = _lexicon_hits_in_order(CORRECTION_LEXICON, ev.text)
            if hits:
                prev = _find_preceding_assistant(events, i)
                if prev is not None:
                    candidates.append(
                        _make_candidate(
                            sid,
                            "correction",
                            ev.line_no,
                            "\n".join([_format_turn(prev), _format_turn(ev)]),
                            {
                                "lexicon_hit": hits,
                                "preceding_assistant_line": prev.line_no,
                            },
                        )
                    )

        # --- validated_pattern ---
        if ev.type == "user" and ev.text is not None:
            text = ev.text
            if len(text) <= APPROVAL_MAX_CHARS:
                hits = _lexicon_hits_in_order(APPROVAL_LEXICON, text)
                if hits:
                    prev = _find_preceding_assistant(events, i)
                    if prev is not None:
                        candidates.append(
                            _make_candidate(
                                sid,
                                "validated_pattern",
                                ev.line_no,
                                "\n".join([_format_turn(prev), _format_turn(ev)]),
                                {
                                    "lexicon_hit": hits,
                                    "preceding_assistant_line": prev.line_no,
                                },
                            )
                        )

        # --- decision_rationale ---
        if ev.type == "assistant":
            full_text = _text_blocks(ev)
            if len(full_text) >= DECISION_MIN_CHARS:
                kw_hits = _lexicon_hits_in_order(DECISION_KEYWORDS, full_text)
                if kw_hits:
                    candidates.append(
                        _make_candidate(
                            sid,
                            "decision_rationale",
                            ev.line_no,
                            _format_turn(ev),
                            {"keywords_hit": kw_hits},
                        )
                    )

    # --- tool_pitfall ---
    # For each user turn with a tool_result.is_error, find the preceding assistant
    # that issued the failing tool_use, then look forward for a retry with same name
    # but different input.
    for i, ev in enumerate(events):
        if ev.type != "user" or not ev.content:
            continue

        for block in ev.content:
            if block.get("type") != "tool_result":
                continue
            if not block.get("is_error"):
                continue

            tool_use_id: str = str(block.get("tool_use_id", ""))

            # Locate the assistant turn that issued the failing tool_use
            failing_asst: SessionEvent | None = None
            failed_name = ""
            failed_input: Any = None

            for j in range(i - 1, -1, -1):
                prev_ev = events[j]
                if prev_ev.type != "assistant" or not prev_ev.content:
                    continue
                for b in prev_ev.content:
                    if b.get("type") == "tool_use" and b.get("id") == tool_use_id:
                        failing_asst = prev_ev
                        failed_name = str(b.get("name", ""))
                        failed_input = b.get("input")
                        break
                if failing_asst is not None:
                    break

            if failing_asst is None:
                continue

            # Find the next assistant turn with same tool name but different input
            found_fix = False
            for j in range(i + 1, len(events)):
                next_ev = events[j]
                if next_ev.type != "assistant" or not next_ev.content:
                    continue
                for b in next_ev.content:
                    if (
                        b.get("type") == "tool_use"
                        and b.get("name") == failed_name
                        and b.get("input") != failed_input
                    ):
                        fixed_input = b.get("input")
                        candidates.append(
                            _make_candidate(
                                sid,
                                "tool_pitfall",
                                failing_asst.line_no,
                                "\n".join(
                                    [
                                        _format_turn(failing_asst),
                                        _format_turn(ev),
                                        _format_turn(next_ev),
                                    ]
                                ),
                                {
                                    "tool": failed_name,
                                    "failed_input": failed_input,
                                    "fixed_input": fixed_input,
                                },
                            )
                        )
                        found_fix = True
                        break
                if found_fix:
                    break

    candidates.sort(key=lambda c: c.line_no)
    return candidates
