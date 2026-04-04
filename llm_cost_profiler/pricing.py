"""Model pricing lookup table. Prices are per 1M tokens (input, output)."""

from typing import Optional, Tuple

# (input_price_per_1M_tokens, output_price_per_1M_tokens)
PRICING: dict[str, Tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o1-pro": (150.00, 600.00),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # Anthropic
    "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3.5-sonnet": (3.00, 15.00),
    "claude-3.5-haiku": (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "claude-haiku-4": (0.80, 4.00),
}

# Cheaper alternatives for model downgrade suggestions
CHEAPER_ALTERNATIVES: dict[str, str] = {
    "gpt-4o": "gpt-4o-mini",
    "gpt-4-turbo": "gpt-4o-mini",
    "gpt-4": "gpt-4o-mini",
    "claude-3-opus": "claude-3-haiku",
    "claude-3-sonnet": "claude-3-haiku",
    "claude-3.5-sonnet": "claude-3.5-haiku",
    "claude-opus-4": "claude-haiku-4",
    "claude-sonnet-4": "claude-haiku-4",
    "o1": "o1-mini",
    "o3": "o3-mini",
}


def _resolve_model(model: str) -> Optional[Tuple[float, float]]:
    """Exact match first, then longest prefix match."""
    if model in PRICING:
        return PRICING[model]
    for key in sorted(PRICING, key=len, reverse=True):
        if model.startswith(key):
            return PRICING[key]
    return None


def get_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    pricing = _resolve_model(model)
    if pricing is None:
        return 0.0
    inp_per_m, out_per_m = pricing
    return (input_tokens * inp_per_m + output_tokens * out_per_m) / 1_000_000


def get_cheaper_alternative(model: str) -> Optional[str]:
    """Return a cheaper model alternative, if known."""
    # Exact match first
    if model in CHEAPER_ALTERNATIVES:
        return CHEAPER_ALTERNATIVES[model]
    # Don't suggest downgrade if the model is already a known cheap model
    cheap_models = set(CHEAPER_ALTERNATIVES.values())
    if model in PRICING and model in cheap_models:
        return None
    # Prefix match (longest first)
    for key in sorted(CHEAPER_ALTERNATIVES, key=len, reverse=True):
        if model.startswith(key):
            return CHEAPER_ALTERNATIVES[key]
    return None


def get_savings_for_downgrade(
    model: str, cheaper_model: str, input_tokens: int, output_tokens: int
) -> float:
    """Calculate savings from switching to a cheaper model."""
    current = get_cost(model, input_tokens, output_tokens)
    cheaper = get_cost(cheaper_model, input_tokens, output_tokens)
    return current - cheaper
