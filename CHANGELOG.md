# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/).

---
## 0.2.0 - 2026-04-08

### Added
- Latency report — `llmcost latency` CLI command showing p50/p95/p99 percentiles 
  by model and call site (contributed by @monteiro-consulting)
- `/api/latency` endpoint for programmatic access
- Latency tab in web dashboard with stat cards and breakdown tables
- `get_latency_stats()` method in storage layer with percentile helper

### Fixed
- Cross-thread SQLite access for dashboard server (contributed by @monteiro-consulting)

## [0.1.1] - 2026-04-05

### Fixed
- **Critical**: `llmcost compare` was closing the shared SQLite connection, causing crashes on subsequent queries
- `cache_get` was redundantly acquiring the same shared connection
- Hardcoded model list in CLI warnings now uses the pricing table dynamically

### Improved
- Replaced `inspect.stack()` with `sys._getframe()` walk for ~10x faster call-site capture
- Pre-computed sorted pricing keys at module level (avoids per-call sorting)
- Extracted `_record_to_row()` helper in storage, eliminating duplicated 13-field tuple
- Optimizer now computes `days_span`, `monthly_factor`, and site groupings once instead of per-analysis
- Added `until` parameter to `get_totals()` and `get_summary()` for proper date-range queries

### Removed
- Dead `_make_cache_key` function in cache module
- Redundant `extract_model` and `serialize_messages` overrides in OpenAI/Anthropic adapters

---

## [0.1.0] - 2026-04-04

### Added
- Transparent wrapper SDK for OpenAI and Anthropic clients (`wrap()`)
- SQLite storage layer with WAL mode and thread-safe writes
- CLI tool (`llmcost`) with 5 commands: `report`, `hotspots`, `compare`, `optimize`, `dashboard`
- Tag system for grouping costs by feature, customer, or any custom dimension
- Response caching decorator (`@cache`) with TTL support
- Optimization engine with 5 analyses: cache detection, retry waste, model downgrade, context bloat, batching
- Local web dashboard with Chart.js (dark theme, auto-refresh)
- Built-in pricing table for 20+ OpenAI and Anthropic models
- Automatic call-site detection from the Python call stack
- Async client support
- Optional prompt storage for deeper analysis
- 50 tests covering storage, pricing, wrapper, optimizer, and CLI

[0.1.1]: https://github.com/buildwithabid/llm-cost-profiler/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/buildwithabid/llm-cost-profiler/releases/tag/v0.1.0
