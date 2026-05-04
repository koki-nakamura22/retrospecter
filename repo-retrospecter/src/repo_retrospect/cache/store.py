"""Read/write the intermediate JSON cache file (ADR-0003).

The cache is a single JSON document (``.retrospect/cache.json`` by default)
holding both fetched PRs and any subsequently extracted ``Knowledge``
records — the "unified management" choice from ADR-0003.

Per decision-defaults.md §I/O: UTF-8, LF, 2-space JSON indent, trailing
newline. ``schema_version`` is checked on load (OQ-03): mismatched
versions emit a warning log and raise ``ValueError`` so callers can
suggest a re-fetch.

The CLI layer is responsible for ``--force`` enforcement; ``save`` here
unconditionally writes to the supplied ``Path`` (creating parent
directories as needed).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from repo_retrospect.models.cache import CACHE_SCHEMA_VERSION, CacheFile

logger = logging.getLogger(__name__)

JSON_INDENT: int = 2


def save(path: Path, cache: CacheFile) -> None:
    """Write ``cache`` to ``path`` as pretty-printed JSON.

    Parent directories are created if missing. The file is written with
    UTF-8 encoding, LF line endings, 2-space indent, and a trailing
    newline (decision-defaults.md §I/O).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cache.model_dump(mode="json")
    text = json.dumps(payload, indent=JSON_INDENT, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load(path: Path) -> CacheFile:
    """Read ``path`` and return a validated ``CacheFile``.

    Raises ``ValueError`` (with a logged warning) if the on-disk
    ``schema_version`` does not match the current ``CACHE_SCHEMA_VERSION``
    — callers should suggest deleting the cache and re-fetching.
    """
    text = path.read_text(encoding="utf-8")
    cache = CacheFile.model_validate_json(text)
    if cache.schema_version != CACHE_SCHEMA_VERSION:
        logger.warning(
            "cache schema_version mismatch: file=%s expected=%s; "
            "delete %s and re-run fetch.",
            cache.schema_version,
            CACHE_SCHEMA_VERSION,
            path,
        )
        raise ValueError(
            f"cache schema_version mismatch: got {cache.schema_version!r}, "
            f"expected {CACHE_SCHEMA_VERSION!r}"
        )
    return cache


__all__ = ["JSON_INDENT", "load", "save"]
