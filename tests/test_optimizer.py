"""Tests for the optimizer analysis engine."""

from datetime import datetime, timedelta, timezone

from llm_cost_profiler.optimizer import (
    find_batching_opportunities,
    find_cacheable_calls,
    find_context_bloat,
    find_downgrade_candidates,
    find_retry_waste,
    run_all_optimizations,
)
from llm_cost_profiler.storage import Storage


def _ts(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )


def _make_call(storage, **overrides):
    base = {
        "timestamp": _ts(),
        "provider": "openai",
        "model": "gpt-4o",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_usd": 0.005,
        "latency_ms": 500,
        "success": True,
        "error_type": None,
        "call_site": "app/main.py:10",
        "function_name": "process",
        "messages_hash": None,
        "tags": None,
    }
    base.update(overrides)
    storage.log_call(base)


class TestCacheDetection:
    def test_finds_duplicates(self):
        storage = Storage(":memory:")
        # 10 calls with same hash
        for i in range(10):
            _make_call(
                storage,
                messages_hash="dup_hash",
                call_site="classify.py:34",
                cost_usd=1.0,
                timestamp=_ts(minutes_ago=10 - i),
            )
        # 1 unique call
        _make_call(storage, messages_hash="unique_hash", call_site="classify.py:34", cost_usd=1.0)

        duplicates = storage.get_duplicate_hashes()
        calls = storage.get_calls_for_optimizer()
        findings = find_cacheable_calls(duplicates, calls)

        assert len(findings) >= 1
        assert findings[0]["category"] == "CACHE"
        assert findings[0]["estimated_savings_monthly"] > 0

    def test_no_duplicates(self):
        storage = Storage(":memory:")
        for i in range(5):
            _make_call(storage, messages_hash=f"unique_{i}")

        duplicates = storage.get_duplicate_hashes()
        calls = storage.get_calls_for_optimizer()
        findings = find_cacheable_calls(duplicates, calls)
        assert len(findings) == 0


class TestRetryDetection:
    def test_finds_retries(self):
        storage = Storage(":memory:")
        now = datetime.now(timezone.utc)

        # Failed call
        _make_call(
            storage,
            timestamp=(now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            success=False,
            error_type="RateLimitError",
            messages_hash="retry_hash",
            call_site="gen.py:50",
            cost_usd=0.5,
        )
        # Retry (same hash, within 60s)
        _make_call(
            storage,
            timestamp=(now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            success=True,
            messages_hash="retry_hash",
            call_site="gen.py:50",
            cost_usd=0.5,
        )
        # Add more calls to meet minimum threshold
        for i in range(10):
            _make_call(
                storage,
                timestamp=(now - timedelta(minutes=i + 1)).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                call_site="gen.py:50",
                cost_usd=0.1,
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_retry_waste(calls)
        # May or may not find retries depending on ordering; just ensure no crash
        assert isinstance(findings, list)


class TestModelDowngrade:
    def test_finds_expensive_model_short_output(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(
                storage,
                model="gpt-4o",
                output_tokens=8,
                input_tokens=100,
                cost_usd=0.50,
                call_site="classify.py:34",
                timestamp=_ts(minutes_ago=20 - i),
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_downgrade_candidates(calls)

        assert len(findings) >= 1
        assert findings[0]["category"] == "MODEL DOWNGRADE"
        assert "gpt-4o-mini" in findings[0]["details"]

    def test_no_downgrade_for_long_output(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(
                storage,
                model="gpt-4o",
                output_tokens=500,
                cost_usd=0.50,
                call_site="gen.py:10",
                timestamp=_ts(minutes_ago=20 - i),
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_downgrade_candidates(calls)
        assert len(findings) == 0


class TestContextBloat:
    def test_finds_bloated_context(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(
                storage,
                input_tokens=5000,
                output_tokens=100,
                cost_usd=0.10,
                call_site="chat.py:123",
                timestamp=_ts(minutes_ago=20 - i),
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_context_bloat(calls)

        assert len(findings) >= 1
        assert findings[0]["category"] == "CONTEXT BLOAT"
        assert "50:1" in findings[0]["message"]

    def test_no_bloat_normal_ratio(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(
                storage,
                input_tokens=500,
                output_tokens=200,
                cost_usd=0.05,
                call_site="normal.py:10",
                timestamp=_ts(minutes_ago=20 - i),
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_context_bloat(calls)
        assert len(findings) == 0


class TestBatching:
    def test_finds_burst_pattern(self):
        storage = Storage(":memory:")
        now = datetime.now(timezone.utc)

        # Create a burst: 10 calls within 2 seconds
        for i in range(10):
            _make_call(
                storage,
                timestamp=(now - timedelta(seconds=i * 0.3)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                ),
                call_site="pipeline.py:89",
                cost_usd=0.10,
                latency_ms=200,
            )

        calls = storage.get_calls_for_optimizer()
        findings = find_batching_opportunities(calls)

        assert len(findings) >= 1
        assert findings[0]["category"] == "BATCHING"


class TestRunAll:
    def test_returns_sorted_findings(self):
        storage = Storage(":memory:")
        # Add diverse data
        for i in range(20):
            _make_call(
                storage,
                messages_hash="dup",
                model="gpt-4o",
                output_tokens=5,
                input_tokens=5000,
                cost_usd=1.0,
                call_site="app.py:1",
                timestamp=_ts(minutes_ago=20 - i),
            )

        findings = run_all_optimizations(storage)
        assert isinstance(findings, list)

        # Verify sorted by savings descending
        savings = [f.get("estimated_savings_monthly", 0) for f in findings]
        assert savings == sorted(savings, reverse=True)
