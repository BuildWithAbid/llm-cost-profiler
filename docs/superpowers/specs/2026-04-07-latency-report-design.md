# Latency Report — Design Spec

## Problem

The profiler already collects `latency_ms` on every LLM call, but no report surfaces this data. Users have no visibility into response times, slow call sites, or latency differences between models.

## Goal

Add a latency report as a new CLI command (`llmcost latency`), a new API endpoint (`/api/latency`), and a new dashboard section — purely observational, no behavior changes.

## Coherence Check

This feature is 100% aligned with the project's philosophy:
- **Observational only** — reads existing data, changes nothing
- **Data already exists** — `latency_ms` is stored on every call but unused in reports
- **Follows existing patterns** — same flow as `report`, `hotspots`, `compare`
- **No new dependencies** — uses standard library only

## CLI Command

```
$ llmcost latency
$ llmcost latency --days 30

LLM Latency Report — Last 7 Days
========================================
Overall: p50 320ms | p95 1,240ms | p99 3,100ms | 12,847 calls

By Model:
  gpt-4o              p50  450ms   p95  1,800ms   p99  4,200ms   4,201 calls
  gpt-4o-mini         p50  180ms   p95    520ms   p99  1,100ms   3,892 calls

Slowest Call Sites:
  1. summarizer.py:47     p95 3,200ms   4,201 calls  ████████████████████
  2. api/chat.py:123      p95 1,800ms   3,892 calls  ███████████
```

`--days` flag, default 7, same as other commands.

Percentiles: p50, p95, p99 — industry standard for latency reporting.

Warning generated when p95 > 3000ms: suggests async or streaming for user-facing calls.

## Storage

New method `get_latency_stats(since, until)` in `storage.py`.

Returns a dict:
```python
{
    "overall": {"p50": int, "p95": int, "p99": int, "calls": int},
    "by_model": [
        {"model": str, "p50": int, "p95": int, "p99": int, "calls": int},
        ...
    ],
    "by_call_site": [
        {"call_site": str, "p50": int, "p95": int, "p99": int, "calls": int},
        ...
    ]
}
```

**Percentile calculation:** SQLite has no native `PERCENTILE_CONT`. Query all `latency_ms` values grouped by dimension, compute percentiles in Python via sorted list + index (`sorted_values[int(len * percentile)]`). This is simple, correct, and fast enough — the profiler stores at most thousands of calls per period.

`by_model` sorted by p95 desc. `by_call_site` sorted by p95 desc, limited to top 10.

## API Endpoint

`GET /api/latency?days=7` in `server.py`.

Returns the JSON output of `get_latency_stats()` directly. Same pattern as `/api/summary`, `/api/hotspots`, etc.

## Dashboard

New section "Latency" in `dashboard.html`:
- 3 stat cards: p50, p95, p99 overall (same card style as existing cost cards)
- Table "By Model" with columns: Model, p50, p95, p99, Calls
- Table "Slowest Call Sites" with columns: Call Site, p95, Calls, bar chart

Same dark-theme styling, included in the auto-refresh cycle (30s).

## Tests

New file `tests/test_latency.py`:

### `TestLatencyStats`
- Correct percentile calculation with known data
- Grouping by model returns correct per-model stats
- Grouping by call_site returns top sites sorted by p95 desc
- Empty database returns zeroes

### `TestLatencyCLI`
- Output contains "Latency Report" header
- Output contains percentile values
- No data case prints "No data found"

### `TestLatencyAPI`
- Endpoint returns valid JSON with overall/by_model/by_call_site keys
- Respects `days` query parameter

Conventions: `Storage(":memory:")`, `_make_call()` helper, mock patterns matching existing tests.

## README Changes

- Add `llmcost latency` row to the CLI commands table
- Add `### llmcost latency` section with usage example and sample output
- Same placement pattern as other command docs

## Files Changed

| Action | File | What |
|--------|------|------|
| Modify | `llm_cost_profiler/storage.py` | Add `get_latency_stats()` |
| Modify | `llm_cost_profiler/cli.py` | Add `cmd_latency()` + register subcommand |
| Modify | `llm_cost_profiler/server.py` | Add `/api/latency` endpoint |
| Modify | `llm_cost_profiler/dashboard.html` | Add Latency section |
| Create | `tests/test_latency.py` | Tests for storage, CLI, API |
| Modify | `README.md` | Document the new command |
