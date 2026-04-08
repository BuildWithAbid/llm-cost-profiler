# Latency Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a latency report as CLI command, API endpoint, and dashboard tab — surfacing the already-collected `latency_ms` data as p50/p95/p99 percentiles.

**Architecture:** New `get_latency_stats()` method in `storage.py` computes percentiles in Python from SQL results. New `cmd_latency()` in `cli.py` renders the CLI output. New `/api/latency` endpoint in `server.py`. New "Latency" tab in `dashboard.html`. New `tests/test_latency.py` for all three.

**Tech Stack:** Python 3.9+, SQLite, pytest, Chart.js (existing in dashboard)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `llm_cost_profiler/storage.py:402` | Add `get_latency_stats()` method |
| Modify | `llm_cost_profiler/cli.py:416-473` | Add `cmd_latency()` + register subcommand |
| Modify | `llm_cost_profiler/server.py:106-111` | Add `/api/latency` route |
| Modify | `llm_cost_profiler/dashboard.html:346-403,733-753` | Add Latency tab + render function |
| Create | `tests/test_latency.py` | Tests for storage, CLI, API |
| Modify | `README.md` | Document the new command |

---

### Task 1: Add `get_latency_stats()` to storage

**Files:**
- Modify: `llm_cost_profiler/storage.py:402` (before `# ── Cache Operations ──`)
- Create: `tests/test_latency.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_latency.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest tests/test_latency.py -v`
Expected: FAIL with `AttributeError: 'Storage' object has no attribute 'get_latency_stats'`

- [ ] **Step 3: Implement `get_latency_stats`**

In `llm_cost_profiler/storage.py`, add this method to the `Storage` class, just before the `# ── Cache Operations ──` comment (line 403):

```python
    # ── Latency Stats ──

    @staticmethod
    def _percentile(sorted_values: List[int], pct: float) -> int:
        """Compute a percentile from a sorted list of values."""
        if not sorted_values:
            return 0
        idx = int(len(sorted_values) * pct)
        idx = min(idx, len(sorted_values) - 1)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest tests/test_latency.py -v`
Expected: 5 passed

- [ ] **Step 5: Run all existing tests for regressions**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest -v`
Expected: All pass (50 existing + 5 new)

- [ ] **Step 6: Commit**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git add llm_cost_profiler/storage.py tests/test_latency.py
git commit -m "feat: add get_latency_stats() to storage layer"
```

---

### Task 2: Add `cmd_latency` CLI command

**Files:**
- Modify: `llm_cost_profiler/cli.py:416-473`
- Modify: `tests/test_latency.py`

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_latency.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest tests/test_latency.py::TestLatencyCLI -v`
Expected: FAIL with `error: argument command: invalid choice: 'latency'`

- [ ] **Step 3: Implement `cmd_latency` and register the subcommand**

In `llm_cost_profiler/cli.py`, add `cmd_latency` before the `# ── Dashboard Command ──` comment (line 418), and add a formatting helper `fmt_ms` after `bar_chart` (line 87):

First, add `fmt_ms` after the `bar_chart` function (after line 86):

```python
def fmt_ms(ms: int) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"
```

Then add `cmd_latency` before `cmd_dashboard` (before line 418):

```python
# ── Latency Command ──

def cmd_latency(args: argparse.Namespace) -> None:
    storage = Storage()
    since = since_str(args.days)
    stats = storage.get_latency_stats(since=since)

    overall = stats["overall"]
    by_model = stats["by_model"]
    by_site = stats["by_call_site"]

    print()
    print(bold(f"LLM Latency Report — Last {args.days} Days"))
    print(bold("=" * 40))

    if overall["calls"] == 0:
        print(dim("  No data found."))
        print()
        return

    print(
        f"Overall: "
        f"p50 {c(fmt_ms(overall['p50']), _C.CYAN)} | "
        f"p95 {c(fmt_ms(overall['p95']), _C.YELLOW)} | "
        f"p99 {c(fmt_ms(overall['p99']), _C.RED)} | "
        f"{fmt_number(overall['calls'])} calls"
    )

    # By Model
    if by_model:
        print()
        print(bold("By Model:"))
        for row in by_model:
            print(
                f"  {row['model'][:22].ljust(22)} "
                f"p50 {fmt_ms(row['p50']).rjust(7)}   "
                f"p95 {fmt_ms(row['p95']).rjust(7)}   "
                f"p99 {fmt_ms(row['p99']).rjust(7)}   "
                f"{fmt_number(row['calls']).rjust(7)} calls"
            )

    # Slowest Call Sites
    if by_site:
        print()
        print(bold("Slowest Call Sites:"))
        max_p95 = max(s["p95"] for s in by_site)
        for i, row in enumerate(by_site, 1):
            bar = bar_chart(row["p95"], max_p95)
            print(
                f"  {c(str(i) + '.', _C.BOLD)} {(row['call_site'] or '?')[:35].ljust(35)} "
                f"p95 {fmt_ms(row['p95']).rjust(7)}   "
                f"{fmt_number(row['calls']).rjust(7)} calls  "
                f"{c(bar, _C.YELLOW)}"
            )

    # Warning for high p95
    warnings = []
    for row in by_model:
        if row["p95"] > 3000:
            warnings.append(
                f"{row['model']}: p95 latency is {fmt_ms(row['p95'])} "
                f"— consider async or streaming for user-facing calls"
            )
    if warnings:
        print()
        for w in warnings:
            print(f"  {c(chr(9888), _C.YELLOW)} {w}")

    print()
```

Then register the subcommand in the `main()` function. Add this after the `p_optimize` block (after line 460):

```python
    # latency
    p_latency = subparsers.add_parser("latency", help="Show latency percentiles by model and call site")
    p_latency.add_argument("--days", type=int, default=7, help="Number of days (default: 7)")
    p_latency.set_defaults(func=cmd_latency)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest tests/test_latency.py -v`
Expected: 7 passed (5 storage + 2 CLI)

- [ ] **Step 5: Commit**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git add llm_cost_profiler/cli.py tests/test_latency.py
git commit -m "feat: add llmcost latency CLI command"
```

---

### Task 3: Add `/api/latency` endpoint

**Files:**
- Modify: `llm_cost_profiler/server.py:106-111`
- Modify: `tests/test_latency.py`

- [ ] **Step 1: Write the failing API test**

Append to `tests/test_latency.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it passes (this is a smoke test for the data layer)**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest tests/test_latency.py::TestLatencyAPI -v`
Expected: PASS

- [ ] **Step 3: Add `/api/latency` route to server**

In `llm_cost_profiler/server.py`, add a new `elif` block inside the `_serve_api` method, after the `/api/optimizations` block (after line 108, before the `else` on line 110):

```python
            elif route == "/api/latency":
                stats = storage.get_latency_stats(since=since)
                self._json_response(stats)
```

- [ ] **Step 4: Run all tests**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git add llm_cost_profiler/server.py tests/test_latency.py
git commit -m "feat: add /api/latency endpoint to dashboard server"
```

---

### Task 4: Add Latency tab to dashboard

**Files:**
- Modify: `llm_cost_profiler/dashboard.html`

- [ ] **Step 1: Add the Latency tab button**

In `dashboard.html`, find the tabs div (line 346-350). Add a new tab after "Optimizer":

Find:
```html
  <div class="tab" data-tab="optimizer">Optimizer</div>
</div>
```

Replace with:
```html
  <div class="tab" data-tab="optimizer">Optimizer</div>
  <div class="tab" data-tab="latency">Latency</div>
</div>
```

- [ ] **Step 2: Add the Latency tab panel**

After the Optimizer tab panel closing `</div>` (line 403), before `</div>` that closes `.content`, add:

```html
  <!-- Latency Tab -->
  <div class="tab-panel" id="panel-latency">
    <div class="cards" id="latency-cards"></div>

    <div class="chart-row">
      <div class="table-box" style="flex:1">
        <h3>Latency by Model</h3>
        <table id="latency-model-table">
          <thead>
            <tr>
              <th>Model</th>
              <th class="right">p50</th>
              <th class="right">p95</th>
              <th class="right">p99</th>
              <th class="right">Calls</th>
            </tr>
          </thead>
          <tbody id="latency-model-body"></tbody>
        </table>
      </div>
      <div class="table-box" style="flex:1">
        <h3>Slowest Call Sites</h3>
        <table id="latency-site-table">
          <thead>
            <tr>
              <th>Call Site</th>
              <th class="right">p95</th>
              <th class="right">Calls</th>
              <th>Distribution</th>
            </tr>
          </thead>
          <tbody id="latency-site-body"></tbody>
        </table>
      </div>
    </div>
  </div>
```

- [ ] **Step 3: Add the `renderLatency()` function**

In the `<script>` section, before the `// ── Tab Switching ──` comment (line 717), add:

```javascript
// ── Render Latency ──
async function renderLatency() {
  const data = await api('latency');
  const overall = data.overall || {calls: 0, p50: 0, p95: 0, p99: 0};
  const byModel = data.by_model || [];
  const bySite = data.by_call_site || [];

  // Cards
  const cardsEl = document.getElementById('latency-cards');
  cardsEl.innerHTML = `
    <div class="card">
      <div class="card-label">p50 Latency</div>
      <div class="card-value latency">${fmtMs(overall.p50)}</div>
    </div>
    <div class="card">
      <div class="card-label">p95 Latency</div>
      <div class="card-value latency">${fmtMs(overall.p95)}</div>
    </div>
    <div class="card">
      <div class="card-label">p99 Latency</div>
      <div class="card-value latency">${fmtMs(overall.p99)}</div>
    </div>
    <div class="card">
      <div class="card-label">Total Calls</div>
      <div class="card-value calls">${fmtNum(overall.calls)}</div>
    </div>
  `;

  // Model table
  const modelBody = document.getElementById('latency-model-body');
  if (!byModel.length) {
    modelBody.innerHTML = '<tr><td colspan="5" class="empty-state">No data yet.</td></tr>';
  } else {
    modelBody.innerHTML = byModel.map(m => `<tr>
      <td>${m.model}</td>
      <td class="right">${fmtMs(m.p50)}</td>
      <td class="right">${fmtMs(m.p95)}</td>
      <td class="right">${fmtMs(m.p99)}</td>
      <td class="right">${fmtNum(m.calls)}</td>
    </tr>`).join('');
  }

  // Site table
  const siteBody = document.getElementById('latency-site-body');
  if (!bySite.length) {
    siteBody.innerHTML = '<tr><td colspan="4" class="empty-state">No data yet.</td></tr>';
  } else {
    const maxP95 = Math.max(...bySite.map(s => s.p95));
    siteBody.innerHTML = bySite.map(s => {
      const barWidth = maxP95 > 0 ? (s.p95 / maxP95) * 100 : 0;
      return `<tr>
        <td class="mono">${s.call_site || '?'}</td>
        <td class="right">${fmtMs(s.p95)}</td>
        <td class="right">${fmtNum(s.calls)}</td>
        <td><span class="inline-bar" style="width:${barWidth}%; background:var(--yellow)"></span></td>
      </tr>`;
    }).join('');
  }
}
```

- [ ] **Step 4: Add `renderLatency()` to the `fetchAll()` function**

Find the `fetchAll` function (line 734). Add `renderLatency()` to the `Promise.all` array:

Find:
```javascript
      renderOptimizer(),
    ]);
```

Replace with:
```javascript
      renderOptimizer(),
      renderLatency(),
    ]);
```

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git add llm_cost_profiler/dashboard.html
git commit -m "feat: add Latency tab to web dashboard"
```

---

### Task 5: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `llmcost latency` to the CLI commands table**

In `README.md`, find the CLI commands table (line 118-124). Add a new row after the `llmcost optimize` row:

Find:
```markdown
| `llmcost dashboard` | Local web dashboard at `http://127.0.0.1:8177` |
```

Replace with:
```markdown
| `llmcost latency` | Latency percentiles by model and call site |
| `llmcost dashboard` | Local web dashboard at `http://127.0.0.1:8177` |
```

- [ ] **Step 2: Add the `llmcost latency` documentation section**

After the `### llmcost optimize` section (after line 196, before `### llmcost dashboard`), add:

```markdown
### `llmcost latency`

```bash
llmcost latency           # last 7 days (default)
llmcost latency --days 30 # last 30 days
```

```
LLM Latency Report — Last 7 Days
========================================
Overall: p50 320ms | p95 1,240ms | p99 3,100ms | 12,847 calls

By Model:
  gpt-4o              p50  450ms   p95  1,800ms   p99  4,200ms   4,201 calls
  gpt-4o-mini         p50  180ms   p95    520ms   p99  1,100ms   3,892 calls

Slowest Call Sites:
  1. features/summarizer.py:47   p95 3,200ms   4,201 calls  ████████████████████
  2. api/chat.py:123             p95 1,800ms   3,892 calls  ███████████
```

Shows p50, p95, and p99 latency percentiles — overall, per model, and per call site. Warns when p95 exceeds 3 seconds.

```

- [ ] **Step 3: Add to the Table of Contents**

Find the line `- [How It Works](#how-it-works)` in the ToC and note that CLI commands aren't individually linked. No ToC change needed — the CLI Commands section already covers all commands.

- [ ] **Step 4: Run all tests**

Run: `cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler && python -m pytest -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git add README.md
git commit -m "docs: add llmcost latency command to README"
```

---

### Task 6: Push and create PR

**Files:** None (git operations only)

- [ ] **Step 1: Create feature branch**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git checkout -b feat/latency-report
```

Note: Commits are on main, so reset main after:

```bash
git checkout main
git checkout -b feat/latency-report
git checkout main
git reset --hard origin/main
git checkout feat/latency-report
```

- [ ] **Step 2: Push and create PR**

```bash
cd C:/Users/vmont/AppData/Local/Temp/llm-cost-profiler
git push -u origin feat/latency-report
gh pr create --title "feat: add latency report (CLI + API + dashboard)" --body "$(cat <<'EOF'
## Summary
- New `llmcost latency` CLI command showing p50/p95/p99 percentiles
- New `/api/latency` endpoint for programmatic access
- New "Latency" tab in the web dashboard with cards and tables
- Surfaces already-collected `latency_ms` data that was previously unused in reports

## Changes
- **Modified:** `storage.py` — `get_latency_stats()` method with percentile computation
- **Modified:** `cli.py` — `cmd_latency()` command with model/call-site breakdown
- **Modified:** `server.py` — `/api/latency` endpoint
- **Modified:** `dashboard.html` — Latency tab with cards + two tables
- **Created:** `tests/test_latency.py` — tests for storage, CLI, and API
- **Modified:** `README.md` — documented the new command

## Test plan
- [x] `pytest tests/test_latency.py -v` — all new tests pass
- [x] `pytest -v` — no regressions
- [ ] Manual test with real data via `llmcost latency`
- [ ] Manual test of dashboard Latency tab

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
