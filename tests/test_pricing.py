"""Tests for the pricing module."""

from llm_cost_profiler.pricing import get_cheaper_alternative, get_cost, get_savings_for_downgrade


class TestGetCost:
    def test_exact_model_match(self):
        cost = get_cost("gpt-4o", 1_000_000, 0)
        assert cost == 2.50

    def test_output_tokens(self):
        cost = get_cost("gpt-4o", 0, 1_000_000)
        assert cost == 10.00

    def test_both_tokens(self):
        cost = get_cost("gpt-4o", 1000, 500)
        expected = (1000 * 2.50 + 500 * 10.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_prefix_match(self):
        # gpt-4o-2024-08-06 should match gpt-4o
        cost = get_cost("gpt-4o-2024-08-06", 1_000_000, 0)
        assert cost == 2.50

    def test_unknown_model_returns_zero(self):
        cost = get_cost("totally-unknown-model", 1000, 500)
        assert cost == 0.0

    def test_mini_exact_match(self):
        cost = get_cost("gpt-4o-mini", 1_000_000, 0)
        assert cost == 0.15

    def test_anthropic_model(self):
        cost = get_cost("claude-3.5-sonnet", 1_000_000, 0)
        assert cost == 3.00


class TestCheaperAlternative:
    def test_gpt4o_downgrade(self):
        assert get_cheaper_alternative("gpt-4o") == "gpt-4o-mini"

    def test_claude_opus_downgrade(self):
        assert get_cheaper_alternative("claude-3-opus") == "claude-3-haiku"

    def test_no_downgrade_for_cheap_model(self):
        assert get_cheaper_alternative("gpt-4o-mini") is None

    def test_savings_calculation(self):
        savings = get_savings_for_downgrade("gpt-4o", "gpt-4o-mini", 1_000_000, 500_000)
        assert savings > 0
        # gpt-4o: 2.50 + 5.00 = 7.50
        # gpt-4o-mini: 0.15 + 0.30 = 0.45
        expected = 7.50 - 0.45
        assert abs(savings - expected) < 1e-10
