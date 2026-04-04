"""Core wrapper that intercepts LLM API calls for cost tracking."""

import hashlib
import inspect
import json
import sys
import time
from typing import Any, Optional

from llm_cost_profiler.pricing import get_cost
from llm_cost_profiler.storage import get_storage
from llm_cost_profiler.tags import get_current_tags

_PACKAGE_DIR = __name__.rsplit(".", 1)[0]


def _get_call_site() -> tuple[Optional[str], Optional[str]]:
    """Walk the call stack to find the first frame outside this package."""
    try:
        for frame_info in inspect.stack():
            module = frame_info.frame.f_globals.get("__name__", "")
            if module and not module.startswith(_PACKAGE_DIR):
                filename = frame_info.filename
                lineno = frame_info.lineno
                func_name = frame_info.frame.f_code.co_name
                return f"{filename}:{lineno}", func_name
    except Exception:
        pass
    return None, None


def _hash_messages(messages: Any) -> Optional[str]:
    """SHA256 hash of serialized messages for dedup detection."""
    if messages is None:
        return None
    try:
        raw = json.dumps(messages, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
    except Exception:
        return None


def _safe_log(record: dict, messages_str: Optional[str] = None) -> None:
    """Log a call record to storage. Never raises."""
    try:
        storage = get_storage()
        storage.log_call(record, messages=messages_str)
    except Exception:
        pass


class ProviderAdapter:
    """Base class for provider-specific adapters."""

    provider: str = "unknown"
    tracked_methods: set = {"create"}

    def extract_model(self, kwargs: dict) -> str:
        return kwargs.get("model", "unknown")

    def extract_tokens(self, response: Any) -> tuple[int, int]:
        return 0, 0

    def extract_error_type(self, exc: Exception) -> str:
        return type(exc).__name__

    def serialize_messages(self, kwargs: dict) -> Optional[Any]:
        return kwargs.get("messages")


class ClientProxy:
    """Transparent proxy that intercepts LLM SDK method calls."""

    def __init__(
        self,
        client: Any,
        adapter: ProviderAdapter,
        store_prompts: bool = False,
    ):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_adapter", adapter)
        object.__setattr__(self, "_store_prompts", store_prompts)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(object.__getattribute__(self, "_client"), name)
        adapter = object.__getattribute__(self, "_adapter")
        store_prompts = object.__getattribute__(self, "_store_prompts")

        # If it's a resource object from the SDK, wrap it recursively
        if _is_resource(attr):
            return ResourceProxy(attr, adapter, store_prompts)
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_client"), name, value)

    def __repr__(self) -> str:
        client = object.__getattribute__(self, "_client")
        return f"<ClientProxy wrapping {type(client).__name__}>"


class ResourceProxy:
    """Proxy for nested SDK resource objects (e.g., client.chat.completions)."""

    def __init__(self, resource: Any, adapter: ProviderAdapter, store_prompts: bool):
        object.__setattr__(self, "_resource", resource)
        object.__setattr__(self, "_adapter", adapter)
        object.__setattr__(self, "_store_prompts", store_prompts)

    def __getattr__(self, name: str) -> Any:
        resource = object.__getattribute__(self, "_resource")
        adapter = object.__getattribute__(self, "_adapter")
        store_prompts = object.__getattribute__(self, "_store_prompts")

        attr = getattr(resource, name)

        if _is_resource(attr):
            return ResourceProxy(attr, adapter, store_prompts)

        if callable(attr) and name in adapter.tracked_methods:
            return _make_interceptor(attr, adapter, store_prompts)

        return attr

    def __repr__(self) -> str:
        resource = object.__getattribute__(self, "_resource")
        return f"<ResourceProxy wrapping {type(resource).__name__}>"


def _is_resource(obj: Any) -> bool:
    """Check if an object is an SDK resource (not a primitive, not callable directly)."""
    if obj is None or isinstance(obj, (str, int, float, bool, list, dict, tuple)):
        return False
    module = getattr(type(obj), "__module__", "") or ""
    if module.startswith(("openai.resources", "anthropic.resources")):
        return True
    # Fallback: it's an object with attributes but not directly callable
    if hasattr(obj, "__class__") and not callable(obj) and hasattr(obj, "__dict__"):
        cls_module = getattr(obj.__class__, "__module__", "") or ""
        if "openai" in cls_module or "anthropic" in cls_module:
            return True
    return False


def _make_interceptor(method: Any, adapter: ProviderAdapter, store_prompts: bool):
    """Create a sync or async interceptor for an SDK method."""
    if inspect.iscoroutinefunction(method):

        async def async_interceptor(*args: Any, **kwargs: Any) -> Any:
            call_site, func_name = _get_call_site()
            model = adapter.extract_model(kwargs)
            messages = adapter.serialize_messages(kwargs)
            messages_hash = _hash_messages(messages)
            tags = get_current_tags()

            start = time.perf_counter()
            record: dict[str, Any] = {
                "provider": adapter.provider,
                "model": model,
                "call_site": call_site,
                "function_name": func_name,
                "messages_hash": messages_hash,
                "tags": tags or None,
                "success": True,
            }
            messages_str = None
            if store_prompts and messages is not None:
                try:
                    messages_str = json.dumps(messages, default=str)
                except Exception:
                    pass

            try:
                result = await method(*args, **kwargs)
                input_tokens, output_tokens = adapter.extract_tokens(result)
                record["input_tokens"] = input_tokens
                record["output_tokens"] = output_tokens
                record["cost_usd"] = get_cost(model, input_tokens, output_tokens)
                return result
            except Exception as exc:
                record["success"] = False
                record["error_type"] = adapter.extract_error_type(exc)
                raise
            finally:
                record["latency_ms"] = int((time.perf_counter() - start) * 1000)
                _safe_log(record, messages_str)

        return async_interceptor
    else:

        def sync_interceptor(*args: Any, **kwargs: Any) -> Any:
            call_site, func_name = _get_call_site()
            model = adapter.extract_model(kwargs)
            messages = adapter.serialize_messages(kwargs)
            messages_hash = _hash_messages(messages)
            tags = get_current_tags()

            start = time.perf_counter()
            record: dict[str, Any] = {
                "provider": adapter.provider,
                "model": model,
                "call_site": call_site,
                "function_name": func_name,
                "messages_hash": messages_hash,
                "tags": tags or None,
                "success": True,
            }
            messages_str = None
            if store_prompts and messages is not None:
                try:
                    messages_str = json.dumps(messages, default=str)
                except Exception:
                    pass

            try:
                result = method(*args, **kwargs)
                input_tokens, output_tokens = adapter.extract_tokens(result)
                record["input_tokens"] = input_tokens
                record["output_tokens"] = output_tokens
                record["cost_usd"] = get_cost(model, input_tokens, output_tokens)
                return result
            except Exception as exc:
                record["success"] = False
                record["error_type"] = adapter.extract_error_type(exc)
                raise
            finally:
                record["latency_ms"] = int((time.perf_counter() - start) * 1000)
                _safe_log(record, messages_str)

        return sync_interceptor


def wrap(client: Any, store_prompts: bool = False) -> ClientProxy:
    """Wrap an LLM SDK client for automatic cost tracking.

    Usage:
        from llm_cost_profiler import wrap
        from openai import OpenAI

        client = wrap(OpenAI())
        # All calls are now tracked automatically

    Args:
        client: An OpenAI or Anthropic client instance.
        store_prompts: If True, store prompt/message content (default False).

    Returns:
        A ClientProxy that behaves identically to the original client.
    """
    # Detect provider at runtime — no import-time dependency
    openai_mod = sys.modules.get("openai")
    anthropic_mod = sys.modules.get("anthropic")

    if openai_mod:
        openai_classes = []
        if hasattr(openai_mod, "OpenAI"):
            openai_classes.append(openai_mod.OpenAI)
        if hasattr(openai_mod, "AsyncOpenAI"):
            openai_classes.append(openai_mod.AsyncOpenAI)
        if openai_classes and isinstance(client, tuple(openai_classes)):
            from llm_cost_profiler.openai_wrapper import OpenAIAdapter
            return ClientProxy(client, OpenAIAdapter(), store_prompts)

    if anthropic_mod:
        anthropic_classes = []
        if hasattr(anthropic_mod, "Anthropic"):
            anthropic_classes.append(anthropic_mod.Anthropic)
        if hasattr(anthropic_mod, "AsyncAnthropic"):
            anthropic_classes.append(anthropic_mod.AsyncAnthropic)
        if anthropic_classes and isinstance(client, tuple(anthropic_classes)):
            from llm_cost_profiler.anthropic_wrapper import AnthropicAdapter
            return ClientProxy(client, AnthropicAdapter(), store_prompts)

    raise TypeError(
        f"Unsupported client type: {type(client).__name__}. "
        "Expected an OpenAI or Anthropic client instance."
    )
