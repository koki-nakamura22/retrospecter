"""Intermediate JSON cache layer (ADR-0003)."""

from repo_retrospect.cache.store import JSON_INDENT, load, save

__all__ = ["JSON_INDENT", "load", "save"]
