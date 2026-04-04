"""CLI entry point for the llmcost command."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from llm_cost_profiler.storage import Storage


# ── ANSI Colors ──

class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

_use_color: Optional[bool] = None

def _color_enabled() -> bool:
    global _use_color
    if _use_color is not None:
        return _use_color
    if os.environ.get("NO_COLOR"):
        _use_color = False
    elif not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        _use_color = False
    else:
        _use_color = True
        # Enable ANSI on Windows
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass
    return _use_color

def c(text: str, color: str) -> str:
    if _color_enabled():
        return f"{color}{text}{_C.RESET}"
    return text

def bold(text: str) -> str:
    return c(text, _C.BOLD)

def dim(text: str) -> str:
    return c(text, _C.DIM)


# ── Formatting Helpers ──

def fmt_cost(cost: float) -> str:
    if cost >= 1000:
        return f"${cost:,.2f}"
    if cost >= 1:
        return f"${cost:.2f}"
    if cost >= 0.01:
        return f"${cost:.3f}"
    return f"${cost:.4f}"

def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

def fmt_number(n: int) -> str:
    return f"{n:,}"

def bar_chart(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int((value / max_value) * width)
    filled = max(0, min(filled, width))
    return "\u2588" * filled

def since_str(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


# ── Report Command ──

def cmd_report(args: argparse.Namespace) -> None:
    storage = Storage()
    since = since_str(args.days)
    totals = storage.get_totals(since=since)
    by_feature = storage.get_summary(since=since, group_by="feature")
    by_model = storage.get_summary(since=since, group_by="model")

    total_cost = totals["cost_usd"]
    total_tokens = totals["input_tokens"] + totals["output_tokens"]
    total_calls = totals["calls"]

    # Header
    print()
    print(bold(f"LLM Cost Report — Last {args.days} Days"))
    print(bold("=" * 40))
    print(
        f"Total: {c(fmt_cost(total_cost), _C.GREEN)} | "
        f"{fmt_tokens(total_tokens)} tokens | "
        f"{fmt_number(total_calls)} calls"
    )

    # By Feature
    if by_feature:
        print()
        print(bold("By Feature:"))
        max_cost = max((r["cost_usd"] for r in by_feature), default=0)
        for row in by_feature:
            name = row.get("feature") or "untagged"
            cost = row["cost_usd"]
            pct = (cost / total_cost * 100) if total_cost > 0 else 0
            bar = bar_chart(cost, max_cost)
            color = _C.CYAN if name != "untagged" else _C.DIM
            print(
                f"  {c(name[:18].ljust(18), color)} "
                f"{fmt_cost(cost).rjust(10)}  "
                f"({pct:4.1f}%)  "
                f"{c(bar, _C.GREEN)}"
            )

    # By Model
    if by_model:
        print()
        print(bold("By Model:"))
        for row in by_model:
            cost = row["cost_usd"]
            pct = (cost / total_cost * 100) if total_cost > 0 else 0
            print(
                f"  {row['model'][:18].ljust(18)} "
                f"{fmt_cost(cost).rjust(10)}  "
                f"({pct:4.1f}%)"
            )

    # Warnings
    warnings = _generate_warnings(storage, since)
    if warnings:
        print()
        print(bold("Warnings:"))
        for w in warnings:
            print(f"  {c('\u26a0', _C.YELLOW)} {w}")

    print()


def _generate_warnings(storage: Storage, since: str) -> List[str]:
    """Generate spending warnings based on call patterns."""
    warnings = []

    # Check retry rates by call site
    calls = storage.get_calls_for_optimizer(since=since)
    if not calls:
        return warnings

    # Group by call_site
    sites: dict[str, list] = {}
    for call in calls:
        site = call.get("call_site") or "unknown"
        sites.setdefault(site, []).append(call)

    for site, site_calls in sites.items():
        total = len(site_calls)
        if total < 10:
            continue

        # Retry detection: same hash, within 60s, first failed
        retry_cost = 0.0
        retry_count = 0
        hashes = {}
        for call in site_calls:
            h = call.get("messages_hash")
            if h:
                if h in hashes:
                    prev = hashes[h]
                    if not prev["success"]:
                        retry_count += 1
                        retry_cost += call["cost_usd"]
                hashes[h] = call

        if retry_count > 0 and retry_count / total > 0.1:
            pct = retry_count / total * 100
            warnings.append(
                f"{site}: {pct:.0f}% of calls are retries "
                f"({fmt_cost(retry_cost)} wasted)"
            )

        # Context bloat: high input:output ratio
        avg_in = sum(c["input_tokens"] for c in site_calls) / total
        avg_out = sum(c["output_tokens"] for c in site_calls) / max(total, 1)
        if avg_out > 0 and avg_in / avg_out > 20:
            warnings.append(
                f"{site}: avg {fmt_tokens(int(avg_in))} input tokens but only "
                f"{fmt_tokens(int(avg_out))} output tokens "
                f"(possible context bloat)"
            )

        # Expensive model for short output
        models = set(c["model"] for c in site_calls)
        for model in models:
            model_calls = [c for c in site_calls if c["model"] == model]
            avg_out = sum(c["output_tokens"] for c in model_calls) / len(model_calls)
            if avg_out < 10:
                from llm_cost_profiler.pricing import get_cheaper_alternative
                cheaper = get_cheaper_alternative(model)
                if cheaper:
                    warnings.append(
                        f"{site}: using {model} but output is always <10 tokens "
                        f"(cheaper model likely works)"
                    )

    return warnings[:5]  # Cap at 5 warnings


# ── Hotspots Command ──

def cmd_hotspots(args: argparse.Namespace) -> None:
    storage = Storage()
    since = since_str(args.days)
    hotspots = storage.get_hotspots(since=since, top_n=args.top)
    totals = storage.get_totals(since=since)
    total_cost = totals["cost_usd"]

    print()
    print(bold("Top Cost Hotspots:"))

    if not hotspots:
        print(dim("  No data found."))
        print()
        return

    max_cost = max(h["cost_usd"] for h in hotspots)
    weekly = args.days / 7

    for i, h in enumerate(hotspots, 1):
        site = h["call_site"] or "unknown"
        func = h["function_name"] or "?"
        cost = h["cost_usd"]
        cost_per_week = cost / weekly if weekly > 0 else cost
        calls = h["calls"]
        pct = (cost / total_cost * 100) if total_cost > 0 else 0
        bar = bar_chart(cost, max_cost)

        print(
            f"  {c(str(i) + '.', _C.BOLD)} {site.ljust(35)} "
            f"{func.ljust(22)} "
            f"{fmt_cost(cost_per_week).rjust(10)}/week   "
            f"{fmt_number(calls).rjust(7)} calls  "
            f"{c(bar, _C.GREEN)}"
        )

    print()


# ── Compare Command ──

def cmd_compare(args: argparse.Namespace) -> None:
    storage = Storage()
    days = args.days

    # Current period
    now_since = since_str(days)
    now_totals = storage.get_totals(since=now_since)
    now_features = storage.get_summary(since=now_since, group_by="feature")

    # Previous period
    prev_end = datetime.now(timezone.utc) - timedelta(days=days)
    prev_start = prev_end - timedelta(days=days)
    prev_since = prev_start.strftime("%Y-%m-%dT%H:%M:%S.%f")
    prev_until = prev_end.strftime("%Y-%m-%dT%H:%M:%S.%f")

    prev_totals = storage.get_totals(since=prev_since, until=prev_until)
    prev_features_list = storage.get_summary(since=prev_since, until=prev_until, group_by="feature")
    prev_features = {(r.get("feature") or "untagged"): r for r in prev_features_list}

    now_cost = now_totals["cost_usd"]
    prev_cost = prev_totals["cost_usd"]

    print()
    period = f"{days}-Day" if days != 7 else "Week-over-Week"
    print(bold(f"{period} Comparison:"))

    # Total comparison
    if prev_cost > 0:
        change_pct = ((now_cost - prev_cost) / prev_cost) * 100
        arrow = "+" if change_pct > 0 else ""
        color = _C.RED if change_pct > 20 else (_C.YELLOW if change_pct > 0 else _C.GREEN)
        flag = " \u26a0" if abs(change_pct) > 25 else ""
        print(
            f"  Total: {fmt_cost(now_cost)} \u2192 was {fmt_cost(prev_cost)} "
            f"({c(f'{arrow}{change_pct:.0f}%', color)}{flag})"
        )
    else:
        print(f"  Total: {fmt_cost(now_cost)} (no previous data)")

    # Feature-level changes
    increases = []
    decreases = []

    for row in now_features:
        name = row.get("feature") or "untagged"
        now_c = row["cost_usd"]
        prev_c = prev_features.get(name, {}).get("cost_usd", 0)
        if prev_c > 0:
            diff = now_c - prev_c
            pct = (diff / prev_c) * 100
        elif now_c > 0:
            diff = now_c
            pct = 100
        else:
            continue

        entry = (name, diff, pct, now_c, prev_c, row)
        if diff > 0:
            increases.append(entry)
        elif diff < 0:
            decreases.append(entry)

    increases.sort(key=lambda x: -x[1])
    decreases.sort(key=lambda x: x[1])

    if increases:
        print()
        print(bold("  Biggest increases:"))
        for name, diff, pct, now_c, prev_c, row in increases[:5]:
            print(
                f"    {name[:18].ljust(18)}: "
                f"{c(f'+{fmt_cost(diff)}', _C.RED)} "
                f"({c(f'+{pct:.0f}%', _C.RED)})"
            )

    if decreases:
        print()
        print(bold("  Biggest decreases:"))
        for name, diff, pct, now_c, prev_c, row in decreases[:5]:
            print(
                f"    {name[:18].ljust(18)}: "
                f"{c(f'{fmt_cost(diff)}', _C.GREEN)} "
                f"({c(f'{pct:.0f}%', _C.GREEN)})"
            )

    print()


# ── Optimize Command ──

def cmd_optimize(args: argparse.Namespace) -> None:
    from llm_cost_profiler.optimizer import run_all_optimizations

    storage = Storage()
    since = since_str(args.days)
    findings = run_all_optimizations(storage, since=since)
    totals = storage.get_totals(since=since)

    # Project monthly spend
    daily_cost = totals["cost_usd"] / max(args.days, 1)
    monthly_projected = daily_cost * 30

    total_savings = sum(f.get("estimated_savings_monthly", 0) for f in findings)

    print()
    print(bold("LLM Cost Optimization Report"))
    print(bold("=" * 40))
    print(f"Current monthly spend (projected): {c(fmt_cost(monthly_projected), _C.CYAN)}")

    if total_savings > 0:
        pct = (total_savings / monthly_projected * 100) if monthly_projected > 0 else 0
        print(
            f"Potential savings found: "
            f"{c(fmt_cost(total_savings) + '/month', _C.GREEN)} "
            f"({pct:.1f}%)"
        )

    if not findings:
        print()
        print(dim("  No optimization opportunities found."))
        print()
        return

    print()
    for i, f in enumerate(findings, 1):
        category = f["category"].upper()
        savings = f.get("estimated_savings_monthly", 0)
        confidence = f.get("confidence", "MEDIUM")
        message = f["message"]
        details = f.get("details", "")

        color = _C.GREEN if savings > 0 else _C.CYAN
        conf_color = _C.GREEN if confidence == "HIGH" else (_C.YELLOW if confidence == "MEDIUM" else _C.DIM)

        savings_str = f"[SAVE {fmt_cost(savings)}/mo]" if savings > 0 else ""

        print(
            f"  {c(f'#{i}', _C.BOLD)} {c(category, _C.YELLOW)} — "
            f"{f.get('call_site', '')}"
            f"  {c(savings_str, color)}"
        )
        print(f"     {message}")
        if details:
            print(f"     {c('\u2192 ' + details, _C.DIM)}")
        print(f"     Confidence: {c(confidence, conf_color)}")
        print()

    print()


# ── Dashboard Command ──

def cmd_dashboard(args: argparse.Namespace) -> None:
    from llm_cost_profiler.server import start_dashboard

    port = args.port
    print(f"Starting dashboard at {c(f'http://127.0.0.1:{port}', _C.CYAN)}")
    print(dim("Press Ctrl+C to stop."))
    try:
        start_dashboard(port=port)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


# ── Main Entry Point ──

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="llmcost",
        description="LLM Cost Profiler — track, visualize, and optimize LLM API spending",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # report
    p_report = subparsers.add_parser("report", help="Show spending breakdown")
    p_report.add_argument("--days", type=int, default=7, help="Number of days (default: 7)")
    p_report.set_defaults(func=cmd_report)

    # hotspots
    p_hotspots = subparsers.add_parser("hotspots", help="Show top cost hotspots by code location")
    p_hotspots.add_argument("--days", type=int, default=7, help="Number of days (default: 7)")
    p_hotspots.add_argument("--top", type=int, default=10, help="Number of hotspots (default: 10)")
    p_hotspots.set_defaults(func=cmd_hotspots)

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare current period to previous period")
    p_compare.add_argument("--days", type=int, default=7, help="Number of days per period (default: 7)")
    p_compare.set_defaults(func=cmd_compare)

    # optimize
    p_optimize = subparsers.add_parser("optimize", help="Analyze data and suggest optimizations")
    p_optimize.add_argument("--days", type=int, default=30, help="Number of days to analyze (default: 30)")
    p_optimize.set_defaults(func=cmd_optimize)

    # dashboard
    p_dashboard = subparsers.add_parser("dashboard", help="Launch the local web dashboard")
    p_dashboard.add_argument("--port", type=int, default=8177, help="Port (default: 8177)")
    p_dashboard.set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
