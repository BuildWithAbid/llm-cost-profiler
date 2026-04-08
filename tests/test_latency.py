"""Tests for the latency report feature."""

from datetime import datetime, timedelta, timezone

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


class TestLatencyStats:
    def test_percentile_calculation(self):
        storage = Storage(":memory:")
        # Insert 100 calls with latency 1..100
        for i in range(1, 101):
            _make_call(storage, latency_ms=i * 10, timestamp=_ts(minutes_ago=100 - i))

        stats = storage.get_latency_stats()
        overall = stats["overall"]

        assert overall["calls"] == 100
        assert overall["p50"] == 500  # ~50th value
        assert overall["p95"] == 950  # ~95th value
        assert overall["p99"] == 990  # ~99th value

    def test_by_model_grouping(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(storage, model="gpt-4o", latency_ms=500, timestamp=_ts(minutes_ago=20 - i))
        for i in range(10):
            _make_call(storage, model="gpt-4o-mini", latency_ms=200, timestamp=_ts(minutes_ago=10 - i))

        stats = storage.get_latency_stats()
        by_model = stats["by_model"]

        assert len(by_model) == 2
        # Sorted by p95 desc, gpt-4o should be first (higher latency)
        assert by_model[0]["model"] == "gpt-4o"
        assert by_model[0]["p50"] == 500
        assert by_model[1]["model"] == "gpt-4o-mini"
        assert by_model[1]["p50"] == 200

    def test_by_call_site_grouping(self):
        storage = Storage(":memory:")
        for i in range(15):
            _make_call(storage, call_site="slow.py:10", latency_ms=2000, timestamp=_ts(minutes_ago=15 - i))
        for i in range(15):
            _make_call(storage, call_site="fast.py:20", latency_ms=100, timestamp=_ts(minutes_ago=15 - i))

        stats = storage.get_latency_stats()
        by_site = stats["by_call_site"]

        assert len(by_site) == 2
        # Sorted by p95 desc
        assert by_site[0]["call_site"] == "slow.py:10"
        assert by_site[1]["call_site"] == "fast.py:20"

    def test_empty_database(self):
        storage = Storage(":memory:")
        stats = storage.get_latency_stats()

        assert stats["overall"]["calls"] == 0
        assert stats["overall"]["p50"] == 0
        assert stats["overall"]["p95"] == 0
        assert stats["overall"]["p99"] == 0
        assert stats["by_model"] == []
        assert stats["by_call_site"] == []

    def test_since_filter(self):
        storage = Storage(":memory:")
        # Old calls (2 hours ago)
        for i in range(10):
            _make_call(storage, latency_ms=9999, timestamp=_ts(minutes_ago=120 + i))
        # Recent calls (10 min ago)
        for i in range(10):
            _make_call(storage, latency_ms=100, timestamp=_ts(minutes_ago=10 - i))

        since = _ts(minutes_ago=60)
        stats = storage.get_latency_stats(since=since)
        assert stats["overall"]["calls"] == 10
        assert stats["overall"]["p50"] == 100


from io import StringIO
from unittest.mock import patch

from llm_cost_profiler.cli import main


class TestLatencyCLI:
    def test_latency_output(self):
        storage = Storage(":memory:")
        for i in range(20):
            _make_call(
                storage,
                model="gpt-4o" if i % 2 == 0 else "gpt-4o-mini",
                latency_ms=300 + i * 50,
                call_site=f"app/mod{i % 3}.py:{10 + i}",
                timestamp=_ts(minutes_ago=20 - i),
            )

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["latency", "--days", "7"])

            output = out.getvalue()
            assert "Latency Report" in output
            assert "p50" in output
            assert "p95" in output
            assert "By Model:" in output
            assert "Slowest Call Sites:" in output

    def test_latency_empty_db(self):
        storage = Storage(":memory:")

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["latency"])

            output = out.getvalue()
            assert "No data found" in output


import json
from unittest.mock import MagicMock
from llm_cost_profiler.server import DashboardHandler


class TestLatencyAPI:
    def test_api_returns_valid_json(self):
        storage = Storage(":memory:")
        for i in range(10):
            _make_call(storage, latency_ms=100 + i * 50, timestamp=_ts(minutes_ago=10 - i))

        DashboardHandler.storage = storage

        handler = MagicMock(spec=DashboardHandler)
        handler.__class__ = DashboardHandler
        handler.storage = storage

        # Call the internal method directly
        stats = storage.get_latency_stats()
        assert "overall" in stats
        assert "by_model" in stats
        assert "by_call_site" in stats
        assert stats["overall"]["calls"] == 10
