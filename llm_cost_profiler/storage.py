"""SQLite storage layer for LLM call records and cache."""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    success       INTEGER NOT NULL DEFAULT 1,
    error_type    TEXT,
    call_site     TEXT,
    function_name TEXT,
    messages_hash TEXT,
    tags_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_calls_model ON calls(model);
CREATE INDEX IF NOT EXISTS idx_calls_call_site ON calls(call_site);
CREATE INDEX IF NOT EXISTS idx_calls_messages_hash ON calls(messages_hash);

CREATE TABLE IF NOT EXISTS prompts (
    call_id   INTEGER PRIMARY KEY REFERENCES calls(id),
    messages  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    cache_key   TEXT PRIMARY KEY,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    ttl_seconds INTEGER NOT NULL DEFAULT 3600
);
"""

DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".llmcost")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "data.db")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


class Storage:
    """Thread-safe SQLite storage for LLM call tracking and caching."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._write_lock = threading.Lock()
        self._is_memory = self._db_path == ":memory:"

        if not self._is_memory:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)

        # Keep a single persistent connection (WAL mode handles concurrent reads)
        self._shared_conn = self._make_connection()

        self._init_schema()

    def _make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _connect(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            return self._shared_conn
        return self._make_connection()

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._shared_conn:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            self._close(conn)

    # ── Write Operations ──

    _INSERT_SQL = """INSERT INTO calls
       (timestamp, provider, model, input_tokens, output_tokens,
        cost_usd, latency_ms, success, error_type, call_site,
        function_name, messages_hash, tags_json)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    @staticmethod
    def _record_to_row(r: Dict[str, Any]) -> tuple:
        return (
            r.get("timestamp", _utcnow()),
            r.get("provider", "unknown"),
            r.get("model", "unknown"),
            r.get("input_tokens", 0),
            r.get("output_tokens", 0),
            r.get("cost_usd", 0.0),
            r.get("latency_ms", 0),
            1 if r.get("success", True) else 0,
            r.get("error_type"),
            r.get("call_site"),
            r.get("function_name"),
            r.get("messages_hash"),
            json.dumps(r["tags"]) if r.get("tags") else None,
        )

    def log_call(self, record: Dict[str, Any], messages: Optional[str] = None) -> Optional[int]:
        """Insert a call record. Returns the row id, or None on failure."""
        with self._write_lock:
            conn = self._connect()
            try:
                cursor = conn.execute(self._INSERT_SQL, self._record_to_row(record))
                call_id = cursor.lastrowid

                if messages is not None and call_id is not None:
                    conn.execute(
                        "INSERT INTO prompts (call_id, messages) VALUES (?, ?)",
                        (call_id, messages),
                    )

                conn.commit()
                return call_id
            except Exception:
                conn.rollback()
                return None
            finally:
                self._close(conn)

    def log_calls_batch(self, records: List[Dict[str, Any]]) -> int:
        """Insert multiple call records in a single transaction. Returns count inserted."""
        with self._write_lock:
            conn = self._connect()
            try:
                rows = [self._record_to_row(r) for r in records]
                conn.executemany(self._INSERT_SQL, rows)
                conn.commit()
                return len(rows)
            except Exception:
                conn.rollback()
                return 0
            finally:
                self._close(conn)

    # ── Read Operations ──

    def get_calls(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        model: Optional[str] = None,
        call_site: Optional[str] = None,
        tag_filter: Optional[Dict[str, str]] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query call records with optional filters."""
        conditions = []
        params: list = []

        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        if model:
            conditions.append("model = ?")
            params.append(model)
        if call_site:
            conditions.append("call_site = ?")
            params.append(call_site)
        if tag_filter:
            for key, value in tag_filter.items():
                conditions.append(f"json_extract(tags_json, '$.{key}') = ?")
                params.append(value)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM calls {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    def get_summary(
        self, since: Optional[str] = None, until: Optional[str] = None, group_by: str = "model"
    ) -> List[Dict[str, Any]]:
        """Aggregate cost data grouped by model, provider, or feature tag."""
        conditions = []
        params: list = []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp < ?")
            params.append(until)
        since_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if group_by == "feature":
            group_col = "json_extract(tags_json, '$.feature')"
            alias = "feature"
        elif group_by == "provider":
            group_col = "provider"
            alias = "provider"
        else:
            group_col = "model"
            alias = "model"

        query = f"""
            SELECT {group_col} AS {alias},
                   COUNT(*) AS calls,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd) AS cost_usd,
                   AVG(latency_ms) AS avg_latency_ms,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
            FROM calls
            {since_clause}
            GROUP BY {group_col}
            ORDER BY cost_usd DESC
        """

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    def get_totals(self, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
        """Get total cost, calls, and tokens for a period."""
        conditions = []
        params: list = []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp < ?")
            params.append(until)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM calls
            {where}
        """

        conn = self._connect()
        try:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "avg_latency_ms": 0}
        finally:
            self._close(conn)

    def get_hotspots(
        self, since: Optional[str] = None, top_n: int = 10
    ) -> List[Dict[str, Any]]:
        """Get top cost hotspots by call site."""
        since_clause = ""
        params: list = []
        if since:
            since_clause = "WHERE timestamp >= ?"
            params.append(since)

        query = f"""
            SELECT call_site,
                   function_name,
                   model,
                   COUNT(*) AS calls,
                   SUM(cost_usd) AS cost_usd,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens,
                   AVG(latency_ms) AS avg_latency_ms,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
            FROM calls
            {since_clause}
            GROUP BY call_site
            ORDER BY cost_usd DESC
            LIMIT ?
        """
        params.append(top_n)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    def get_timeline(
        self, since: Optional[str] = None, bucket: str = "day"
    ) -> List[Dict[str, Any]]:
        """Get cost timeline bucketed by hour or day."""
        since_clause = ""
        params: list = []
        if since:
            since_clause = "WHERE timestamp >= ?"
            params.append(since)

        if bucket == "hour":
            time_fmt = "%Y-%m-%dT%H:00:00"
        else:
            time_fmt = "%Y-%m-%d"

        query = f"""
            SELECT strftime('{time_fmt}', timestamp) AS time_bucket,
                   COUNT(*) AS calls,
                   SUM(cost_usd) AS cost_usd,
                   SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens
            FROM calls
            {since_clause}
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    def get_calls_for_optimizer(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all call data needed by the optimizer, ordered by call_site and timestamp."""
        since_clause = ""
        params: list = []
        if since:
            since_clause = "WHERE timestamp >= ?"
            params.append(since)

        query = f"""
            SELECT id, timestamp, provider, model, input_tokens, output_tokens,
                   cost_usd, latency_ms, success, error_type, call_site,
                   function_name, messages_hash, tags_json
            FROM calls
            {since_clause}
            ORDER BY call_site, timestamp
        """

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    def get_duplicate_hashes(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Find messages_hash values that appear more than once."""
        since_clause = ""
        params: list = []
        if since:
            since_clause = "WHERE timestamp >= ? AND messages_hash IS NOT NULL"
            params.append(since)
        else:
            since_clause = "WHERE messages_hash IS NOT NULL"

        query = f"""
            SELECT messages_hash, call_site, model,
                   COUNT(*) AS call_count,
                   SUM(cost_usd) AS total_cost
            FROM calls
            {since_clause}
            GROUP BY messages_hash, call_site
            HAVING COUNT(*) > 1
            ORDER BY total_cost DESC
        """

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(conn)

    # ── Latency Stats ──

    @staticmethod
    def _percentile(sorted_values: List[int], pct: float) -> int:
        """Compute a percentile from a sorted list of values."""
        import math
        if not sorted_values:
            return 0
        idx = math.ceil(len(sorted_values) * pct) - 1
        idx = max(0, min(idx, len(sorted_values) - 1))
        return sorted_values[idx]

    def get_latency_stats(
        self, since: Optional[str] = None, until: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get latency percentiles (p50, p95, p99) overall, by model, and by call site."""
        conditions = []
        params: list = []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp < ?")
            params.append(until)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        conn = self._connect()
        try:
            # Overall
            rows = conn.execute(
                f"SELECT latency_ms FROM calls {where} ORDER BY latency_ms", params
            ).fetchall()
            all_latencies = [r["latency_ms"] for r in rows]

            overall = {
                "calls": len(all_latencies),
                "p50": self._percentile(all_latencies, 0.50),
                "p95": self._percentile(all_latencies, 0.95),
                "p99": self._percentile(all_latencies, 0.99),
            }

            # By model
            model_rows = conn.execute(
                f"SELECT model, latency_ms FROM calls {where} ORDER BY model, latency_ms",
                params,
            ).fetchall()

            models_data: Dict[str, List[int]] = {}
            for r in model_rows:
                models_data.setdefault(r["model"], []).append(r["latency_ms"])

            by_model = []
            for model, latencies in models_data.items():
                by_model.append({
                    "model": model,
                    "calls": len(latencies),
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                    "p99": self._percentile(latencies, 0.99),
                })
            by_model.sort(key=lambda x: x["p95"], reverse=True)

            # By call site
            site_rows = conn.execute(
                f"SELECT call_site, latency_ms FROM calls {where} ORDER BY call_site, latency_ms",
                params,
            ).fetchall()

            sites_data: Dict[str, List[int]] = {}
            for r in site_rows:
                site = r["call_site"] or "unknown"
                sites_data.setdefault(site, []).append(r["latency_ms"])

            by_call_site = []
            for site, latencies in sites_data.items():
                by_call_site.append({
                    "call_site": site,
                    "calls": len(latencies),
                    "p50": self._percentile(latencies, 0.50),
                    "p95": self._percentile(latencies, 0.95),
                    "p99": self._percentile(latencies, 0.99),
                })
            by_call_site.sort(key=lambda x: x["p95"], reverse=True)
            by_call_site = by_call_site[:10]

            return {
                "overall": overall,
                "by_model": by_model,
                "by_call_site": by_call_site,
            }
        finally:
            self._close(conn)

    # ── Cache Operations ──

    def cache_get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get a cached response if it exists and hasn't expired."""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT response, created_at, ttl_seconds FROM cache
                   WHERE cache_key = ?""",
                (cache_key,),
            ).fetchone()

            if row is None:
                return None

            created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created > timedelta(seconds=row["ttl_seconds"]):
                with self._write_lock:
                    conn.execute("DELETE FROM cache WHERE cache_key = ?", (cache_key,))
                    conn.commit()
                return None

            return json.loads(row["response"])
        finally:
            self._close(conn)

    def cache_set(self, cache_key: str, response: Any, ttl: int = 3600) -> None:
        """Store a response in the cache."""
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO cache (cache_key, response, created_at, ttl_seconds)
                       VALUES (?, ?, ?, ?)""",
                    (cache_key, json.dumps(response, default=str), _utcnow(), ttl),
                )
                conn.commit()
            finally:
                self._close(conn)

    def cache_clear(self) -> int:
        """Clear all cached entries. Returns count of deleted rows."""
        with self._write_lock:
            conn = self._connect()
            try:
                cursor = conn.execute("DELETE FROM cache")
                conn.commit()
                return cursor.rowcount
            finally:
                self._close(conn)


# Module-level singleton for convenience
_default_storage: Optional[Storage] = None
_default_lock = threading.Lock()


def get_storage(db_path: Optional[str] = None) -> Storage:
    """Get or create the default Storage instance."""
    global _default_storage
    if db_path:
        return Storage(db_path)
    with _default_lock:
        if _default_storage is None:
            _default_storage = Storage()
        return _default_storage
