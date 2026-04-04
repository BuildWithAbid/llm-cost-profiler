"""OpenAI-specific adapter for the wrapper."""

from typing import Any, Optional, Tuple

from llm_cost_profiler.wrapper import ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    """Extracts token usage and model info from OpenAI API responses."""

    provider = "openai"
    tracked_methods = {"create"}

    def extract_tokens(self, response: Any) -> Tuple[int, int]:
        """Extract token counts from an OpenAI response.

        OpenAI responses have response.usage.prompt_tokens and
        response.usage.completion_tokens.
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return 0, 0
            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
            output_tokens = getattr(usage, "completion_tokens", 0) or 0
            return input_tokens, output_tokens
        except Exception:
            return 0, 0
