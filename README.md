# LLM Cost Profiler

**Find the money you're burning on LLM APIs.** Two lines of code, zero config, instant visibility.

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

## Setup — 2 lines, 30 seconds

```bash
pip install llm-spend-profiler
```

```python
from llm_cost_profiler import wrap
from openai import OpenAI

client = wrap(OpenAI())  # that's it. everything is tracked now.
```

Your code works exactly as before. Every API call is silently logged to a local SQLite database. If logging fails for any reason, it fails silently — your app is never affected.

Works with **Anthropic** too:

```python
from anthropic import Anthropic
client = wrap(Anthropic())
```

And **async** clients:

```python
from openai import AsyncOpenAI
client = wrap(AsyncOpenAI())
```

---

## What you get

### `llmcost report` — Where your money goes

```bash
llmcost report           # last 7 days
llmcost report --days 30 # last 30 days
```

Shows total spend, breakdown by feature and model, and automatic warnings about retry waste, context bloat, and overpriced model usage.

### `llmcost hotspots` — Which lines of code cost the most

```
Top Cost Hotspots:
  1. features/summarizer.py:47   summarize_doc()    $412.80/week   4,201 calls  ████████████████████
  2. api/chat.py:123             handle_message()   $203.11/week   3,892 calls  ██████████
  3. pipeline/classify.py:34     classify_text()     $89.40/week   2,847 calls  ████
```

Auto-detected from the Python call stack. No manual annotation needed.

### `llmcost compare` — What changed

```
Week-over-Week Comparison:
  Total: $847.32 → was $623.10 (+36% ⚠)

  Biggest increases:
    summarizer: +$180 (+77%) — call volume doubled
    chatbot: +$44 (+28%) — avg tokens per call increased
```

### `llmcost optimize` — What to fix and how much you'll save

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

  #4 CONTEXT BLOAT — chatbot.py:123                   [SAVE $155/mo]
     Avg 3,200 input tokens, growing over conversation
     → Truncate history to last 5 messages
     Confidence: MEDIUM
```

Five analyses: **cache detection**, **retry waste**, **model downgrade suggestions**, **context bloat detection**, **batching opportunities**.

### `llmcost dashboard` — Visual dashboard

```bash
llmcost dashboard  # opens http://127.0.0.1:8177
```

Dark-themed local web dashboard with:
- Cost summary cards and feature treemap
- Spend timeline chart (daily/hourly)
- Model usage breakdown
- Hotspots table
- Optimization waterfall chart

Auto-refreshes every 30 seconds. Single HTML file, no npm, no build step.

---

## Tag your calls

Group costs by feature, customer, environment — whatever matters to you:

```python
from llm_cost_profiler import tag

with tag(feature="summarizer", customer="acme_corp"):
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Summarize this..."}]
    )
```

Tags nest. Inner tags merge with outer tags.

## Cache responses

Stop paying for duplicate calls:

```python
from llm_cost_profiler import cache

@cache(ttl=3600)  # cache for 1 hour
def classify_text(text):
    return client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"Classify: {text}"}]
    )

classify_text("hello")  # API call, cached
classify_text("hello")  # instant, free
```

## Store prompts (optional)

Enable prompt storage for deeper optimization analysis:

```python
client = wrap(OpenAI(), store_prompts=True)
```

Disabled by default for privacy. When enabled, the optimizer can detect near-duplicate prompts and analyze what causes retry failures.

---

## How it works

- **Wrapper**: Transparent proxy pattern — intercepts SDK method calls without monkey-patching. Your client object behaves identically.
- **Storage**: SQLite with WAL mode at `~/.llmcost/data.db`. Thread-safe. All data stays local.
- **Pricing**: Built-in table for OpenAI and Anthropic models. Prefix-matching handles versioned model names automatically.
- **Call site detection**: Walks the Python call stack to find the file and line that triggered each API call.
- **Zero dependencies**: Only uses the Python standard library. The OpenAI/Anthropic SDKs are detected at runtime, not required at install time.

---

## Requirements

- Python 3.9+
- No required dependencies
- Optional: `openai` and/or `anthropic` SDKs

## License

MIT
