"""Local HTTP server for the LLM Cost Profiler dashboard."""

import http.server
import json
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from llm_cost_profiler.optimizer import run_all_optimizations
from llm_cost_profiler.storage import Storage


def _since_str(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard API and frontend."""

    storage: Optional[Storage] = None

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default request logging
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if route == "/" or route == "/index.html":
            self._serve_html()
        elif route.startswith("/api/"):
            self._serve_api(route, params)
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        html_path = Path(__file__).parent / "dashboard.html"
        try:
            content = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(500, "Dashboard HTML file not found")

    def _serve_api(self, route: str, params: Dict[str, list]) -> None:
        days = int(params.get("days", ["7"])[0])
        since = _since_str(days)
        storage = self.__class__.storage

        if storage is None:
            self._json_response({"error": "Storage not initialized"}, 500)
            return

        try:
            if route == "/api/summary":
                totals = storage.get_totals(since=since)
                data = {
                    "period_days": days,
                    "total_cost": totals["cost_usd"],
                    "total_calls": totals["calls"],
                    "total_input_tokens": totals["input_tokens"],
                    "total_output_tokens": totals["output_tokens"],
                    "avg_latency_ms": totals["avg_latency_ms"],
                    "avg_cost_per_call": (
                        totals["cost_usd"] / totals["calls"]
                        if totals["calls"] > 0
                        else 0
                    ),
                }
                self._json_response(data)

            elif route == "/api/by-feature":
                rows = storage.get_summary(since=since, group_by="feature")
                self._json_response({"features": rows})

            elif route == "/api/by-model":
                rows = storage.get_summary(since=since, group_by="model")
                self._json_response({"models": rows})

            elif route == "/api/by-callsite":
                rows = storage.get_hotspots(since=since, top_n=50)
                self._json_response({"hotspots": rows})

            elif route == "/api/timeline":
                bucket = params.get("bucket", ["day"])[0]
                rows = storage.get_timeline(since=since, bucket=bucket)
                self._json_response({"timeline": rows, "bucket": bucket})

            elif route == "/api/calls":
                limit = int(params.get("limit", ["100"])[0])
                offset = int(params.get("offset", ["0"])[0])
                model = params.get("model", [None])[0]
                rows = storage.get_calls(
                    since=since, model=model, limit=limit, offset=offset
                )
                self._json_response({"calls": rows, "limit": limit, "offset": offset})

            elif route == "/api/optimizations":
                findings = run_all_optimizations(storage, since=since)
                self._json_response({"optimizations": findings})

            else:
                self.send_error(404, f"Unknown API route: {route}")

        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def start_dashboard(port: int = 8177) -> None:
    """Start the dashboard HTTP server and open the browser."""
    DashboardHandler.storage = Storage()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()
