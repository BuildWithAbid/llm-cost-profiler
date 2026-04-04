"""Caching decorator for LLM API responses."""

import functools
import hashlib
import json
from typing import Any, Callable, Optional

from llm_cost_profiler.storage import get_storage


def cache(ttl: int = 3600, db_path: Optional[str] = None) -> Callable:
    """Decorator that caches LLM responses in SQLite.

    Usage:
        @cache(ttl=3600)
        def classify_text(text):
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": text}]
            )
            return response

    The decorator intercepts the wrapped function, hashes the arguments,
    and returns a cached response if available. The cache is stored in
    the same SQLite database used for call tracking.
    """
    storage = get_storage(db_path)

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Build cache key from all arguments
            key_data = json.dumps(
                {"func": func.__qualname__, "args": args, "kwargs": kwargs},
                sort_keys=True,
                default=str,
            )
            cache_key = hashlib.sha256(key_data.encode()).hexdigest()

            # Try cache hit
            cached = storage.cache_get(cache_key)
            if cached is not None:
                return cached

            # Cache miss — call the real function
            result = func(*args, **kwargs)

            # Store result
            try:
                storage.cache_set(cache_key, result, ttl)
            except Exception:
                pass  # Never break the user's code

            return result

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            key_data = json.dumps(
                {"func": func.__qualname__, "args": args, "kwargs": kwargs},
                sort_keys=True,
                default=str,
            )
            cache_key = hashlib.sha256(key_data.encode()).hexdigest()

            cached = storage.cache_get(cache_key)
            if cached is not None:
                return cached

            result = await func(*args, **kwargs)

            try:
                storage.cache_set(cache_key, result, ttl)
            except Exception:
                pass

            return result

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator
