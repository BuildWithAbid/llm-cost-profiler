"""Anthropic-specific adapter for the wrapper."""

from typing import Any, Optional, Tuple

from llm_cost_profiler.wrapper import ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    """Extracts token usage and model info from Anthropic API responses."""

    provider = "anthropic"
    tracked_methods = {"create"}

    def extract_tokens(self, response: Any) -> Tuple[int, int]:
        """Extract token counts from an Anthropic response.

        Anthropic responses have response.usage.input_tokens and
        response.usage.output_tokens.
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return 0, 0
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
            return input_tokens, output_tokens
        except Exception:
            return 0, 0

    def serialize_messages(self, kwargs: dict) -> Optional[Any]:
        """Serialize Anthropic messages for hashing.

        Anthropic uses 'messages' for the conversation and optionally
        'system' for the system prompt.
        """
        messages = kwargs.get("messages")
        system = kwargs.get("system")
        if system and messages:
            return {"system": system, "messages": messages}
        return messages
