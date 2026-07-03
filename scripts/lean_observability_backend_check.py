#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_REPORT = Path("test-results/lean-observability-case.json")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000").rstrip("/")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200").rstrip("/")
PYROSCOPE_URL = os.getenv("PYROSCOPE_URL", "http://localhost:4040").rstrip("/")


def request_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def request_text(url, timeout=30):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def retry(fn, timeout_seconds=90, interval=5):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            time.sleep(interval)
    raise last_error


def check_grafana():
    for uid in ("tempo", "pyroscope", "stock-postgres"):
        datasource = request_json(f"{GRAFANA_URL}/api/datasources/uid/{uid}")
        if datasource.get("uid") != uid:
            raise AssertionError(f"Unexpected datasource response for {uid}: {datasource}")
    dashboard = request_json(f"{GRAFANA_URL}/api/dashboards/uid/lean-backtest-observability-lab")
    title = dashboard.get("dashboard", {}).get("title")
    if title != "Lean Backtest Observability Lab":
        raise AssertionError(f"Lean dashboard is not provisioned: {title!r}")
    print("ok - Grafana datasources and Lean dashboard are provisioned")


def check_tempo(report):
    trace_id = report.get("trace_id")
    if not trace_id:
        raise AssertionError("Report has no trace_id")

    def load_trace():
        trace = request_json(f"{TEMPO_URL}/api/traces/{trace_id}")
        raw = json.dumps(trace)
        required_spans = [
            "lean.case",
            "lean.data_refresh",
            "lean.config_write",
            "lean.docker_backtest",
            "lean.result_parse",
            "lean.report_render",
            "lean.grafana_query",
        ]
        missing = [span for span in required_spans if span not in raw]
        if missing:
            raise AssertionError(f"Tempo trace is missing spans: {', '.join(missing)}")
        return trace

    retry(load_trace)
    print(f"ok - Tempo returned Lean trace {trace_id} with required spans")


def check_profile(query, label):
    encoded_query = urllib.parse.quote(query, safe="")

    def load_profile():
        profile = request_json(
            f"{PYROSCOPE_URL}/pyroscope/render?query={encoded_query}&from=now-30m&until=now&format=json",
            timeout=45,
        )
        flamebearer = profile.get("flamebearer") or {}
        names = flamebearer.get("names") or []
        levels = flamebearer.get("levels") or []
        if not names or not levels:
            raise AssertionError(f"Pyroscope flame graph for {label} has no samples")
        return names, levels

    names, levels = retry(load_profile, timeout_seconds=120, interval=5)
    if names == ["total"]:
        print(f"warn - Pyroscope profile for {label} exists but only contains total; runtime stacks were not symbolized")
    else:
        print(f"ok - Pyroscope flame graph for {label} has {len(names)} functions and {len(levels)} levels")
    return names, levels


def check_pyroscope(report):
    request_text(f"{PYROSCOPE_URL}/ready")
    check_profile(report.get("profile_query") or 'process_cpu:cpu:nanoseconds:cpu:nanoseconds{service_name="lean.backtest.runner"}', "Lean Python runner")
    print("info - Skipping LEAN engine internal profile check on local arm64; limitation is documented in README.md")


def main():
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise AssertionError(f"Lean observability report is not ok: {report.get('status')} {report.get('error')}")
    check_grafana()
    check_tempo(report)
    check_pyroscope(report)


if __name__ == "__main__":
    main()
