"""Tagging context manager for annotating LLM calls with metadata."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict

_active_tags: ContextVar[Dict[str, Any]] = ContextVar("llmcost_tags", default={})


@contextmanager
def tag(**kwargs: Any):
    """Context manager to tag LLM calls with key-value metadata.

    Usage:
        with tag(feature="summarizer", customer="acme_corp"):
            response = client.chat.completions.create(...)

    Tags nest — inner tags merge with (and override) outer tags.
    """
    current = _active_tags.get()
    merged = {**current, **kwargs}
    token = _active_tags.set(merged)
    try:
        yield
    finally:
        _active_tags.reset(token)


def get_current_tags() -> Dict[str, Any]:
    """Return the currently active tags (empty dict if none)."""
    return _active_tags.get().copy()
