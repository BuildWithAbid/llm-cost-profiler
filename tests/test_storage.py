"""Tests for the SQLite storage layer."""

import json
import time
from datetime import datetime, timedelta, timezone

from llm_cost_profiler.storage import Storage


def _make_record(**overrides):
    base = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "provider": "openai",
        "model": "gpt-4o",
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0075,
        "latency_ms": 500,
        "success": True,
        "error_type": None,
        "call_site": "app/main.py:42",
        "function_name": "process",
        "messages_hash": "abc123",
        "tags": None,
    }
    base.update(overrides)
    return base


class TestStorageBasic:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_log_and_retrieve(self):
        record = _make_record()
        row_id = self.storage.log_call(record)
        assert row_id is not None

        calls = self.storage.get_calls()
        assert len(calls) == 1
        assert calls[0]["model"] == "gpt-4o"
        assert calls[0]["input_tokens"] == 100

    def test_log_with_prompts(self):
        record = _make_record()
        messages = json.dumps([{"role": "user", "content": "hello"}])
        row_id = self.storage.log_call(record, messages=messages)
        assert row_id is not None

    def test_get_totals(self):
        for _ in range(5):
            self.storage.log_call(_make_record(cost_usd=1.0, input_tokens=1000, output_tokens=500))

        totals = self.storage.get_totals()
        assert totals["calls"] == 5
        assert totals["cost_usd"] == 5.0
        assert totals["input_tokens"] == 5000
        assert totals["output_tokens"] == 2500

    def test_get_totals_with_since(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S.%f")
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")

        self.storage.log_call(_make_record(timestamp=old, cost_usd=10.0))
        self.storage.log_call(_make_record(timestamp=recent, cost_usd=1.0))

        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")
        totals = self.storage.get_totals(since=since)
        assert totals["calls"] == 1
        assert totals["cost_usd"] == 1.0


class TestStorageSummary:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_summary_by_model(self):
        self.storage.log_call(_make_record(model="gpt-4o", cost_usd=2.0))
        self.storage.log_call(_make_record(model="gpt-4o", cost_usd=3.0))
        self.storage.log_call(_make_record(model="gpt-4o-mini", cost_usd=0.5))

        summary = self.storage.get_summary(group_by="model")
        assert len(summary) == 2
        assert summary[0]["model"] == "gpt-4o"
        assert summary[0]["cost_usd"] == 5.0
        assert summary[0]["calls"] == 2

    def test_summary_by_feature(self):
        self.storage.log_call(_make_record(tags={"feature": "chat"}, cost_usd=1.0))
        self.storage.log_call(_make_record(tags={"feature": "chat"}, cost_usd=2.0))
        self.storage.log_call(_make_record(tags={"feature": "classify"}, cost_usd=0.5))

        summary = self.storage.get_summary(group_by="feature")
        assert len(summary) == 2
        assert summary[0]["feature"] == "chat"
        assert summary[0]["cost_usd"] == 3.0


class TestStorageHotspots:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_hotspots_ordering(self):
        self.storage.log_call(_make_record(call_site="a.py:1", cost_usd=10.0))
        self.storage.log_call(_make_record(call_site="b.py:2", cost_usd=5.0))
        self.storage.log_call(_make_record(call_site="c.py:3", cost_usd=20.0))

        hotspots = self.storage.get_hotspots(top_n=2)
        assert len(hotspots) == 2
        assert hotspots[0]["call_site"] == "c.py:3"
        assert hotspots[0]["cost_usd"] == 20.0

    def test_hotspots_top_n(self):
        for i in range(20):
            self.storage.log_call(_make_record(call_site=f"file{i}.py:1", cost_usd=float(i)))

        hotspots = self.storage.get_hotspots(top_n=5)
        assert len(hotspots) == 5


class TestStorageTimeline:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_timeline_buckets(self):
        now = datetime.now(timezone.utc)
        for i in range(3):
            ts = (now - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")
            self.storage.log_call(_make_record(timestamp=ts, cost_usd=1.0))

        timeline = self.storage.get_timeline(bucket="day")
        assert len(timeline) == 3


class TestStorageCache:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_cache_set_and_get(self):
        self.storage.cache_set("key1", {"result": "hello"}, ttl=3600)
        cached = self.storage.cache_get("key1")
        assert cached == {"result": "hello"}

    def test_cache_miss(self):
        cached = self.storage.cache_get("nonexistent")
        assert cached is None

    def test_cache_clear(self):
        self.storage.cache_set("key1", {"a": 1})
        self.storage.cache_set("key2", {"b": 2})
        count = self.storage.cache_clear()
        assert count == 2
        assert self.storage.cache_get("key1") is None


class TestStorageDuplicates:
    def setup_method(self):
        self.storage = Storage(":memory:")

    def test_find_duplicates(self):
        for _ in range(5):
            self.storage.log_call(_make_record(messages_hash="same_hash", call_site="x.py:1"))
        self.storage.log_call(_make_record(messages_hash="unique_hash", call_site="x.py:1"))

        dups = self.storage.get_duplicate_hashes()
        assert len(dups) == 1
        assert dups[0]["call_count"] == 5
        assert dups[0]["messages_hash"] == "same_hash"
