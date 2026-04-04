"""Tests for the CLI commands."""

import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from llm_cost_profiler.cli import main
from llm_cost_profiler.storage import Storage


def _seed_data(storage):
    """Seed the storage with test data."""
    now = datetime.now(timezone.utc)
    for i in range(20):
        ts = (now - timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")
        storage.log_call({
            "timestamp": ts,
            "provider": "openai",
            "model": "gpt-4o" if i % 3 != 0 else "gpt-4o-mini",
            "input_tokens": 1000 + i * 100,
            "output_tokens": 200 + i * 10,
            "cost_usd": 0.05 * (i + 1),
            "latency_ms": 500 + i * 50,
            "success": True if i % 5 != 0 else False,
            "error_type": "RateLimitError" if i % 5 == 0 else None,
            "call_site": f"app/module{i % 3}.py:{10 + i}",
            "function_name": f"func_{i % 3}",
            "messages_hash": f"hash_{i % 5}",
            "tags": {"feature": ["chat", "summarizer", "classifier"][i % 3]},
        })


class TestReportCommand:
    def test_report_output(self):
        storage = Storage(":memory:")
        _seed_data(storage)

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["report", "--days", "7"])

            output = out.getvalue()
            assert "LLM Cost Report" in output
            assert "By Model:" in output

    def test_report_empty_db(self):
        storage = Storage(":memory:")

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["report"])

            output = out.getvalue()
            assert "LLM Cost Report" in output


class TestHotspotsCommand:
    def test_hotspots_output(self):
        storage = Storage(":memory:")
        _seed_data(storage)

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["hotspots", "--days", "7"])

            output = out.getvalue()
            assert "Top Cost Hotspots" in output

    def test_hotspots_empty(self):
        storage = Storage(":memory:")

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["hotspots"])

            output = out.getvalue()
            assert "No data found" in output


class TestCompareCommand:
    def test_compare_output(self):
        storage = Storage(":memory:")
        _seed_data(storage)

        with patch("llm_cost_profiler.cli.Storage", return_value=storage):
            out = StringIO()
            with patch("sys.stdout", out):
                main(["compare", "--days", "3"])

            output = out.getvalue()
            assert "Comparison" in output


class TestNoCommand:
    def test_no_command_shows_help(self):
        out = StringIO()
        with patch("sys.stdout", out):
            try:
                main([])
            except SystemExit as e:
                assert e.code == 1
            else:
                assert False, "Expected SystemExit"
