#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_REPORT = Path("test-results/observability-case.json")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000").rstrip("/")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200").rstrip("/")
PYROSCOPE_URL = os.getenv("PYROSCOPE_URL", "http://localhost:4040").rstrip("/")


def request_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def request_text(url):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def check_grafana():
    for uid in ("tempo", "pyroscope", "stock-postgres"):
        datasource = request_json(f"{GRAFANA_URL}/api/datasources/uid/{uid}")
        if datasource.get("uid") != uid:
            raise AssertionError(f"Unexpected datasource response for {uid}: {datasource}")
    dashboard = request_json(f"{GRAFANA_URL}/api/dashboards/uid/stock-observability-lab")
    title = dashboard.get("dashboard", {}).get("title")
    if title != "Stock Observability Lab":
        raise AssertionError(f"Observability dashboard is not provisioned: {title!r}")
    print("ok - Grafana datasources and observability dashboard are provisioned")


def check_tempo(report):
    trace_id = report.get("trace_id")
    if not trace_id:
        raise AssertionError("Report has no trace_id")
    trace = request_json(f"{TEMPO_URL}/api/traces/{trace_id}")
    raw = json.dumps(trace)
    required_spans = [
        "stock.observability_case",
        "stock.pipeline.run",
        "stock.download_prices",
        "stock.analysis",
        "stock.grafana.datasource_query",
    ]
    missing = [span for span in required_spans if span not in raw]
    if missing:
        raise AssertionError(f"Tempo trace is missing spans: {', '.join(missing)}")
    print(f"ok - Tempo returned trace {trace_id} with required stock pipeline spans")


def check_pyroscope(report):
    request_text(f"{PYROSCOPE_URL}/ready")
    required_names = [
        "/app/fetch_prices.py run_once",
        "/app/analysis.py analyze_records",
        "/app/analysis.py _visible_cpu_analysis",
    ]
    candidates = [
        report.get("pyroscope_service_name"),
        "stock.observability.case",
        "stock.observability.control",
        "stock.collector",
    ]
    checked = []
    for service_name in [item for item in candidates if item]:
        if service_name in checked:
            continue
        checked.append(service_name)
        query = f'process_cpu:cpu:nanoseconds:cpu:nanoseconds{{service_name="{service_name}"}}'
        encoded_query = urllib.parse.quote(query, safe="")
        profile = request_json(
            f"{PYROSCOPE_URL}/pyroscope/render?query={encoded_query}&from=now-30m&until=now&format=json"
        )
        flamebearer = profile.get("flamebearer") or {}
        names = flamebearer.get("names") or []
        levels = flamebearer.get("levels") or []
        if names and levels and all(name in names for name in required_names):
            print(f"ok - Pyroscope flame graph for {service_name} has {len(names)} functions and {len(levels)} levels")
            return
    raise AssertionError(f"Pyroscope flame graph is missing required stock functions for services: {', '.join(checked)}")


def main():
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = json.loads(report_path.read_text(encoding="utf-8"))
    check_grafana()
    check_tempo(report)
    check_pyroscope(report)


if __name__ == "__main__":
    main()
