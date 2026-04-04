"""Optimization analysis engine — detects waste and suggests savings."""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from llm_cost_profiler.pricing import get_cheaper_alternative, get_cost, get_savings_for_downgrade
from llm_cost_profiler.storage import Storage


def run_all_optimizations(
    storage: Storage, since: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Run all optimization analyses and return prioritized findings."""
    findings: List[Dict[str, Any]] = []
    calls = storage.get_calls_for_optimizer(since=since)
    duplicates = storage.get_duplicate_hashes(since=since)

    if not calls:
        return findings

    findings.extend(find_cacheable_calls(duplicates, calls))
    findings.extend(find_retry_waste(calls))
    findings.extend(find_downgrade_candidates(calls))
    findings.extend(find_context_bloat(calls))
    findings.extend(find_batching_opportunities(calls))

    # Sort by estimated monthly savings descending
    findings.sort(key=lambda f: f.get("estimated_savings_monthly", 0), reverse=True)
    return findings


def find_cacheable_calls(
    duplicates: List[Dict[str, Any]], calls: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Detect duplicate/cacheable calls by messages_hash."""
    findings = []
    days_span = _get_days_span(calls)
    monthly_factor = 30 / max(days_span, 1)

    for dup in duplicates:
        count = dup["call_count"]
        total_cost = dup["total_cost"]
        site = dup["call_site"] or "unknown"
        model = dup["model"] or "unknown"

        unique_count = 1
        savings = total_cost - (total_cost / count)  # all dupes after the first
        monthly_savings = savings * monthly_factor

        if monthly_savings < 0.01:
            continue

        dup_pct = ((count - unique_count) / count) * 100

        findings.append({
            "category": "CACHE",
            "call_site": site,
            "message": (
                f"{dup_pct:.0f}% of calls are exact duplicates "
                f"({count - unique_count} of {count}/period)"
            ),
            "details": "Add @cache decorator to avoid redundant API calls",
            "estimated_savings_monthly": round(monthly_savings, 2),
            "confidence": "HIGH",
        })

    # Aggregate by call_site (multiple hashes per site)
    by_site: Dict[str, Dict] = {}
    for f in findings:
        site = f["call_site"]
        if site in by_site:
            by_site[site]["estimated_savings_monthly"] += f["estimated_savings_monthly"]
            by_site[site]["message"] = f["message"]  # Use latest
        else:
            by_site[site] = f.copy()

    return list(by_site.values())


def find_retry_waste(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect retry patterns — same call site, similar prompt, within 60s, first failed."""
    findings = []
    days_span = _get_days_span(calls)
    monthly_factor = 30 / max(days_span, 1)

    # Group by call_site
    by_site: Dict[str, list] = defaultdict(list)
    for call in calls:
        site = call.get("call_site") or "unknown"
        by_site[site].append(call)

    for site, site_calls in by_site.items():
        if len(site_calls) < 5:
            continue

        total = len(site_calls)
        retry_count = 0
        retry_cost = 0.0
        error_types: Dict[str, int] = defaultdict(int)

        # Walk through calls looking for retry patterns
        for i in range(1, len(site_calls)):
            curr = site_calls[i]
            prev = site_calls[i - 1]

            # Same hash, previous failed, within 60 seconds
            if (
                curr.get("messages_hash")
                and curr["messages_hash"] == prev.get("messages_hash")
                and not prev["success"]
            ):
                try:
                    curr_time = datetime.fromisoformat(curr["timestamp"])
                    prev_time = datetime.fromisoformat(prev["timestamp"])
                    gap = (curr_time - prev_time).total_seconds()
                    if 0 < gap < 60:
                        retry_count += 1
                        retry_cost += curr["cost_usd"]
                        if prev.get("error_type"):
                            error_types[prev["error_type"]] += 1
                except (ValueError, TypeError):
                    continue

        if retry_count == 0:
            continue

        retry_rate = retry_count / total * 100
        monthly_waste = retry_cost * monthly_factor

        if monthly_waste < 0.01:
            continue

        # Find dominant error type
        top_error = max(error_types, key=error_types.get) if error_types else "unknown"
        error_pct = (error_types.get(top_error, 0) / retry_count * 100) if retry_count > 0 else 0

        detail = f"{retry_rate:.0f}% retry rate"
        if top_error != "unknown":
            detail += f". {error_pct:.0f}% of failures are {top_error}"

        findings.append({
            "category": "RETRY FIX",
            "call_site": site,
            "message": detail,
            "details": "Investigate error patterns and fix root cause to eliminate retries",
            "estimated_savings_monthly": round(monthly_waste, 2),
            "confidence": "HIGH",
        })

    return findings


def find_downgrade_candidates(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find call sites using expensive models for simple outputs."""
    findings = []
    days_span = _get_days_span(calls)
    monthly_factor = 30 / max(days_span, 1)

    # Group by (call_site, model)
    groups: Dict[tuple, list] = defaultdict(list)
    for call in calls:
        if not call["success"]:
            continue
        site = call.get("call_site") or "unknown"
        model = call.get("model") or "unknown"
        groups[(site, model)].append(call)

    for (site, model), group in groups.items():
        cheaper = get_cheaper_alternative(model)
        if not cheaper:
            continue

        total_calls = len(group)
        if total_calls < 5:
            continue

        avg_output = sum(c["output_tokens"] for c in group) / total_calls

        # Only suggest downgrade if outputs are consistently short/simple
        if avg_output >= 200:
            continue

        # Calculate savings
        total_input = sum(c["input_tokens"] for c in group)
        total_output = sum(c["output_tokens"] for c in group)
        current_cost = sum(c["cost_usd"] for c in group)
        cheaper_cost = get_cost(cheaper, total_input, total_output)
        savings = current_cost - cheaper_cost
        monthly_savings = savings * monthly_factor

        if monthly_savings < 0.01:
            continue

        findings.append({
            "category": "MODEL DOWNGRADE",
            "call_site": site,
            "message": (
                f"Output is always <{int(avg_output + 1)} tokens avg. "
                f"Using {model} but {cheaper} would likely produce identical results"
            ),
            "details": f"Switch {model} to {cheaper}",
            "estimated_savings_monthly": round(monthly_savings, 2),
            "confidence": "MEDIUM",
        })

    return findings


def find_context_bloat(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find call sites with disproportionate input:output token ratios."""
    findings = []
    days_span = _get_days_span(calls)
    monthly_factor = 30 / max(days_span, 1)

    # Group by call_site
    groups: Dict[str, list] = defaultdict(list)
    for call in calls:
        if not call["success"]:
            continue
        site = call.get("call_site") or "unknown"
        groups[site].append(call)

    for site, group in groups.items():
        total = len(group)
        if total < 5:
            continue

        avg_input = sum(c["input_tokens"] for c in group) / total
        avg_output = sum(c["output_tokens"] for c in group) / total

        if avg_output <= 0:
            continue

        ratio = avg_input / avg_output
        if ratio <= 20:
            continue

        # Estimate savings if input were reduced by 50%
        total_input = sum(c["input_tokens"] for c in group)
        total_output = sum(c["output_tokens"] for c in group)
        model = group[0].get("model", "unknown")

        current_cost = sum(c["cost_usd"] for c in group)
        reduced_cost = get_cost(model, total_input // 2, total_output)
        savings = current_cost - reduced_cost
        monthly_savings = savings * monthly_factor

        if monthly_savings < 0.01:
            continue

        # Check if input tokens are growing over time (conversation accumulation)
        growing = _is_growing(group)
        growth_note = ". Input token count growing over time — possible unbounded history" if growing else ""

        findings.append({
            "category": "CONTEXT BLOAT",
            "call_site": site,
            "message": (
                f"Avg {int(avg_input):,} input tokens but only {int(avg_output):,} output tokens "
                f"({ratio:.0f}:1 ratio){growth_note}"
            ),
            "details": "Consider truncating context or summarizing conversation history",
            "estimated_savings_monthly": round(monthly_savings, 2),
            "confidence": "MEDIUM",
        })

    return findings


def find_batching_opportunities(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect sequential calls to the same site within short windows."""
    findings = []
    days_span = _get_days_span(calls)
    monthly_factor = 30 / max(days_span, 1)

    # Group by call_site
    groups: Dict[str, list] = defaultdict(list)
    for call in calls:
        site = call.get("call_site") or "unknown"
        groups[site].append(call)

    for site, group in groups.items():
        if len(group) < 10:
            continue

        # Find bursts: consecutive calls within 5 seconds
        bursts = []
        current_burst = [group[0]]

        for i in range(1, len(group)):
            try:
                curr_time = datetime.fromisoformat(group[i]["timestamp"])
                prev_time = datetime.fromisoformat(group[i - 1]["timestamp"])
                gap = (curr_time - prev_time).total_seconds()
                if 0 <= gap <= 5:
                    current_burst.append(group[i])
                else:
                    if len(current_burst) >= 3:
                        bursts.append(current_burst)
                    current_burst = [group[i]]
            except (ValueError, TypeError):
                if len(current_burst) >= 3:
                    bursts.append(current_burst)
                current_burst = [group[i]]

        if len(current_burst) >= 3:
            bursts.append(current_burst)

        if not bursts:
            continue

        avg_burst_size = sum(len(b) for b in bursts) / len(bursts)
        total_burst_cost = sum(c["cost_usd"] for b in bursts for c in b)
        total_latency = sum(c["latency_ms"] for b in bursts for c in b)

        # Estimated savings: reduce overhead tokens from sequential calls
        # Conservative: 20% savings from batching
        monthly_savings = total_burst_cost * 0.20 * monthly_factor

        if monthly_savings < 0.01:
            continue

        findings.append({
            "category": "BATCHING",
            "call_site": site,
            "message": (
                f"{len(bursts)} burst(s) detected, avg {avg_burst_size:.0f} sequential calls "
                f"within 5s windows"
            ),
            "details": (
                "Batch these into fewer calls with structured output to reduce overhead"
            ),
            "estimated_savings_monthly": round(monthly_savings, 2),
            "confidence": "LOW",
        })

    return findings


# ── Helpers ──

def _get_days_span(calls: List[Dict[str, Any]]) -> float:
    """Get the number of days spanned by the call records."""
    if not calls:
        return 1
    try:
        first = datetime.fromisoformat(calls[0]["timestamp"])
        last = datetime.fromisoformat(calls[-1]["timestamp"])
        span = (last - first).total_seconds() / 86400
        return max(span, 1)
    except (ValueError, TypeError):
        return 1


def _is_growing(calls: List[Dict[str, Any]]) -> bool:
    """Check if input tokens are trending upward over time."""
    if len(calls) < 10:
        return False

    # Compare first quarter avg to last quarter avg
    n = len(calls)
    q1 = calls[: n // 4]
    q4 = calls[3 * n // 4:]

    avg_q1 = sum(c["input_tokens"] for c in q1) / len(q1) if q1 else 0
    avg_q4 = sum(c["input_tokens"] for c in q4) / len(q4) if q4 else 0

    # Growing if last quarter is 50%+ higher than first quarter
    return avg_q4 > avg_q1 * 1.5 if avg_q1 > 0 else False
