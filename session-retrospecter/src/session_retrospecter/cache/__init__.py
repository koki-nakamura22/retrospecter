"""cache — Cache JSON round-trip と append merge."""

from .store import DEFAULT_CACHE_PATH, load, merge_append, save

__all__ = ["DEFAULT_CACHE_PATH", "load", "merge_append", "save"]
