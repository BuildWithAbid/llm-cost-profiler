<h1 align="center">LLM Cost Profiler</h1>
<p align="center">
  <strong>Track, visualize, and optimize your OpenAI and Anthropic API spending.</strong><br>
  Two lines of Python. Zero config. Instant cost visibility.
</p>
<p align="center">
  <a href="https://pypi.org/project/llm-spend-profiler/"><img src="https://img.shields.io/pypi/v/llm-spend-profiler?color=blue&label=PyPI" alt="LLM Cost Profiler on PyPI"></a>
  <a href="https://pypi.org/project/llm-spend-profiler/"><img src="https://img.shields.io/pypi/pyversions/llm-spend-profiler" alt="Python 3.9+"></a>
  <a href="https://github.com/buildwithabid/llm-cost-profiler/blob/main/LICENSE"><img src="https://img.shields.io/github/license/buildwithabid/llm-cost-profiler" alt="MIT License"></a>
  <a href="https://github.com/buildwithabid/llm-cost-profiler/stargazers"><img src="https://img.shields.io/github/stars/buildwithabid/llm-cost-profiler?style=social" alt="GitHub Stars"></a>
</p>

---

```
LLM Cost Report — Last 7 Days
========================================
Total: $847.32 | 2.4M tokens | 12,847 calls

By Feature:
  summarizer         $412.80  (48.7%)  ████████████████████
  chatbot            $203.11  (24.0%)  ████████████
  classifier          $89.40  (10.5%)  █████
  content_gen         $78.22   (9.2%)  ████
  extraction          $41.50   (4.9%)  ██
  untagged            $22.29   (2.6%)  █

Warnings:
  ⚠ summarizer: 34% of calls are retries ($140.15 wasted)
  ⚠ chatbot: avg 3,200 input tokens but only 180 output tokens (context bloat)
  ⚠ classifier: using gpt-4o but output is always <10 tokens (cheaper model works)
```

I ran this on my own project and found **$1,240/month in waste** — duplicate calls that should be cached, an expensive model doing a job a cheap one handles fine, and retry loops burning money on failures. All fixable in an afternoon.

---

## Why LLM Cost Profiler?

If you're building with GPT-4, GPT-4o, Claude, or any LLM API, costs add up fast — and they're invisible until the bill arrives. Most teams discover they're overspending only after it's too late.

LLM Cost Profiler gives you **real-time cost tracking per feature, per model, per line of code** — without changing how you write code. It detects the five most common sources of LLM waste:

- **Duplicate calls** that should be cached (often 30-60% of total spend)
- **Retry loops** burning money on repeated failures
- **Expensive models** doing jobs that cheaper models handle identically
- **Context bloat** from unbounded conversation history
- **Sequential calls** that could be batched

Works with **OpenAI** (GPT-4, GPT-4o, GPT-4o-mini, o1, o3) and **Anthropic** (Claude Opus, Sonnet, Haiku). Supports sync and async clients. Zero dependencies.

---

## Table of Contents

- [Quick Start](#quick-start)
- [CLI Commands](#cli-commands)
- [Features](#features)
  - [Tag Your Calls](#tag-your-calls)
  - [Cache Responses](#cache-responses)
  - [Store Prompts](#store-prompts-optional)
- [How It Works](#how-it-works)
- [API Reference](#api-reference)
- [Uninstall](#uninstall)
- [Requirements](#requirements)
- [Contributing](#contributing)
- [License](#license)

---

## Quick Start

### Install

```bash
pip install llm-spend-profiler
```

### Wrap your client

```python
from openai import OpenAI
from llm_cost_profiler import wrap

client = wrap(OpenAI())  # that's it — every call is tracked now
```

Your code works exactly as before. Every API call is silently logged to a local SQLite database. If logging ever fails, it fails silently — your app is never affected.

### Works with Anthropic

```python
from anthropic import Anthropic
client = wrap(Anthropic())
```

### Works with async

```python
from openai import AsyncOpenAI
client = wrap(AsyncOpenAI())
```

### See where your money goes

```bash
llmcost report
```

That's it. You're tracking.

---

## CLI Commands

All commands work out of the box once you've wrapped a client and made some API calls.

| Command | What it does |
|---------|-------------|
| `llmcost report` | Spending breakdown by feature and model |
| `llmcost hotspots` | Top cost hotspots by code location |
| `llmcost compare` | Period-over-period cost comparison |
| `llmcost optimize` | Actionable savings with estimated dollar amounts |
| `llmcost dashboard` | Local web dashboard at `http://127.0.0.1:8177` |

### `llmcost report`

```bash
llmcost report           # last 7 days (default)
llmcost report --days 30 # last 30 days
```

Shows total spend, breakdown by feature and model, and automatic warnings for retry waste, context bloat, and overpriced model usage.

### `llmcost hotspots`

```bash
llmcost hotspots          # top 10 (default)
llmcost hotspots --top 20 # top 20
```

```
Top Cost Hotspots:
  1. features/summarizer.py:47   summarize_doc()    $412.80/week   4,201 calls  ████████████████████
  2. api/chat.py:123             handle_message()   $203.11/week   3,892 calls  ██████████
  3. pipeline/classify.py:34     classify_text()     $89.40/week   2,847 calls  ████
```

Auto-detected from the call stack. No manual annotation needed.

### `llmcost compare`

```bash
llmcost compare           # week-over-week (default)
llmcost compare --days 30 # month-over-month
```

```
Week-over-Week Comparison:
  Total: $847.32 → was $623.10 (+36% ⚠)

  Biggest increases:
    summarizer: +$180 (+77%)
    chatbot:    +$44  (+28%)
```

### `llmcost optimize`

```bash
llmcost optimize            # last 30 days (default)
llmcost optimize --days 90  # last 90 days
```

```
LLM Cost Optimization Report
========================================
Current monthly spend (projected): $2,847
Potential savings found: $1,240/month (43.5%)

  #1 CACHE — classifier.py:34                        [SAVE $310/mo]
     85% of calls are exact duplicates (723 of 847/week)
     → Add @cache decorator
     Confidence: HIGH

  #2 RETRY FIX — content_gen.py:112                   [SAVE $180/mo]
     28% retry rate from JSON parse errors
     → Fix prompt to return raw JSON
     Confidence: HIGH

  #3 MODEL DOWNGRADE — classifier.py:34               [SAVE $71/mo]
     Output is always <10 tokens, one of 5 fixed labels
     → Switch gpt-4o to gpt-4o-mini
     Confidence: MEDIUM
```

Five analyses: **cache detection**, **retry waste**, **model downgrade**, **context bloat**, **batching opportunities**.

### `llmcost dashboard`

```bash
llmcost dashboard           # default port 8177
llmcost dashboard --port 9000
```

Dark-themed local web dashboard with cost cards, feature treemap, spend timeline, model breakdown, hotspots table, and optimization waterfall. Auto-refreshes every 30 seconds. Single HTML file — no npm, no build step.

---

## Features

### Tag Your Calls

Group costs by feature, customer, environment — whatever matters to you:

```python
from llm_cost_profiler import tag

with tag(feature="summarizer", customer="acme_corp"):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Summarize this document..."}]
    )
```

Tags nest naturally. Inner tags merge with outer tags:

```python
with tag(feature="pipeline"):
    with tag(step="extract"):
        # tagged as feature=pipeline, step=extract
        client.chat.completions.create(...)
    with tag(step="transform"):
        # tagged as feature=pipeline, step=transform
        client.chat.completions.create(...)
```

### Cache Responses

Stop paying for duplicate calls:

```python
from llm_cost_profiler import cache

@cache(ttl=3600)  # cache for 1 hour
def classify_text(text):
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Classify: {text}"}]
    )

classify_text("hello")  # API call → cached
classify_text("hello")  # instant, free
```

Works with both sync and async functions. Cache is stored in the same local SQLite database.

### Store Prompts (optional)

Enable prompt storage for deeper optimization analysis:

```python
client = wrap(OpenAI(), store_prompts=True)
```

Disabled by default for privacy. When enabled, the optimizer can detect near-duplicate prompts and analyze what causes retry failures.

---

## How It Works

```
Your code                     LLM Cost Profiler                    OpenAI / Anthropic
─────────                     ─────────────────                    ──────────────────
client.chat.completions  →  ClientProxy → ResourceProxy chain
          .create(...)   →  intercepts create()
                            ├─ captures call site (sys._getframe)
                            ├─ reads active tags (contextvars)
                            ├─ calls real SDK method  ──────────→  API call happens
                            ├─ extracts tokens from response
                            ├─ looks up cost from pricing table
                            ├─ logs to SQLite (async-safe)
                            └─ returns original response  ←──────  response comes back
```

- **Proxy pattern** — wraps the SDK client transparently. No monkey-patching, no subclassing. Your client object behaves identically.
- **SQLite + WAL mode** — all data stored locally at `~/.llmcost/data.db`. Thread-safe writes, concurrent reads. No external database needed.
- **Built-in pricing** — covers OpenAI and Anthropic models. Prefix-matching handles versioned model names (e.g., `gpt-4o-2024-08-06` matches `gpt-4o`).
- **Call site detection** — walks the Python stack via `sys._getframe()` to find the exact file and line that triggered each API call. No decorators or annotations required.
- **Zero dependencies** — only uses the Python standard library. The OpenAI/Anthropic SDKs are detected at runtime, not required at install.

---

## API Reference

### `wrap(client, store_prompts=False)`

Wraps an OpenAI or Anthropic client. Returns a transparent proxy that tracks all API calls.

```python
from llm_cost_profiler import wrap

client = wrap(OpenAI())                        # basic tracking
client = wrap(OpenAI(), store_prompts=True)     # also store prompt content
```

### `tag(**kwargs)`

Context manager that attaches metadata to all API calls within its scope.

```python
from llm_cost_profiler import tag

with tag(feature="search", env="production"):
    # all calls here are tagged
    ...
```

### `cache(ttl=3600, db_path=None)`

Decorator that caches function results in SQLite. Identical arguments return cached responses.

```python
from llm_cost_profiler import cache

@cache(ttl=3600)
def my_function(text):
    ...
```

### `get_current_tags()`

Returns the currently active tags as a dictionary. Useful for debugging.

```python
from llm_cost_profiler import get_current_tags

with tag(feature="search"):
    print(get_current_tags())  # {"feature": "search"}
```

---

## Uninstall

```bash
pip uninstall llm-spend-profiler
```

To also remove stored data:

```bash
# macOS / Linux
rm -rf ~/.llmcost

# Windows
rmdir /s /q %USERPROFILE%\.llmcost
```

---

## Requirements

- Python 3.9+
- No required dependencies
- Optional: `openai` and/or `anthropic` SDKs

---

## Contributing

Contributions are welcome. To set up the dev environment:

```bash
git clone https://github.com/buildwithabid/llm-cost-profiler.git
cd llm-cost-profiler
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest
```

All 50 tests should pass. If you're adding a new feature, please include tests.

---

## License

MIT -- see [LICENSE](LICENSE) for details.

---

<sub>
<b>Keywords:</b> LLM cost tracking, OpenAI cost monitoring, Anthropic API costs, GPT-4 cost optimizer, Claude API spending, LLM token usage tracker, AI API cost management, Python LLM profiler, reduce OpenAI bill, LLM spend analytics, GPT cost per feature, AI cost optimization tool, LLM API budget monitor, token cost calculator, ChatGPT cost tracker
</sub>
