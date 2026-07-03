#!/usr/bin/env python3
import argparse
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests
from psycopg.types.json import Jsonb

from fetch_prices import DB, TICKERS, ensure_schema, run_once, wait_for_db
from latency_reason import explain_latency
from observability_support import (
    configure_observability,
    flush_traces,
    profile_tags,
    set_span_attributes,
    span as obs_span,
    trace_id_from_span,
)


REPORT_PATH = Path(os.getenv("OBS_CASE_REPORT", "test-results/observability-case.json"))
DATASOURCE_UID = "stock-postgres"


def elapsed_ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


def wait_for_grafana(base_url):
    last_error = None
    for _ in range(30):
        try:
            response = requests.get(f"{base_url}/api/health", timeout=5)
            response.raise_for_status()
            payload = response.json()
            if payload.get("database") == "ok":
                return payload
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Grafana did not become ready: {last_error}")


def frame_row_count(frames):
    total = 0
    for frame in frames:
        values = frame.get("data", {}).get("values", [])
        if values:
            total += len(values[0])
    return total


def query_datasource(base_url, raw_sql, fmt):
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": DATASOURCE_UID, "type": "postgres"},
                "rawSql": raw_sql,
                "format": fmt,
                "rawQuery": True,
                "intervalMs": 86400000,
                "maxDataPoints": 2000,
            }
        ],
        "from": "now-1y",
        "to": "now",
        "range": {"from": "now-1y", "to": "now", "raw": {"from": "now-1y", "to": "now"}},
    }
    response = requests.post(f"{base_url}/api/ds/query", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def query_grafana_display_data(base_url):
    summary_sql = """
SELECT
  symbol,
  close::double precision AS latest_close,
  day_change_pct::double precision AS day_change_pct
FROM stock_summary
ORDER BY symbol;
""".strip()
    indicators_sql = """
SELECT
  symbol,
  price_time,
  close::double precision AS close,
  ma20::double precision AS ma20,
  ma60::double precision AS ma60,
  volatility20::double precision AS volatility20
FROM stock_indicators
WHERE price_time >= NOW() - INTERVAL '30 days'
ORDER BY price_time DESC, symbol
LIMIT 200;
""".strip()

    with profile_tags({"stage": "grafana_query"}):
        with obs_span("stock.grafana.datasource_query", {"grafana.datasource_uid": DATASOURCE_UID}):
            summary = query_datasource(base_url, summary_sql, "table")
            indicators = query_datasource(base_url, indicators_sql, "table")

    summary_rows = frame_row_count(summary["results"]["A"]["frames"])
    indicator_rows = frame_row_count(indicators["results"]["A"]["frames"])
    if summary_rows < max(1, len(TICKERS) // 2):
        raise RuntimeError(f"Grafana summary query returned too few rows: {summary_rows}")
    if indicator_rows < 20:
        raise RuntimeError(f"Grafana indicators query returned too few rows: {indicator_rows}")
    return {"summary_rows": summary_rows, "indicator_rows": indicator_rows}


def record_observability_run(report, started_at, completed_at):
    timings = report["stage_timings_ms"]
    sql = """
INSERT INTO observability_runs (
    run_id, started_at, completed_at, mode, symbols, price_rows, indicator_rows,
    download_ms, normalize_ms, analysis_ms, store_prices_ms, store_indicators_ms,
    grafana_query_ms, browser_render_ms, pipeline_total_ms, critical_path_ms, total_ms,
    dominant_stage, reason_code, reason_summary, trace_id, status, details
) VALUES (
    %(run_id)s, %(started_at)s, %(completed_at)s, %(mode)s, %(symbols)s, %(price_rows)s, %(indicator_rows)s,
    %(download_ms)s, %(normalize_ms)s, %(analysis_ms)s, %(store_prices_ms)s, %(store_indicators_ms)s,
    %(grafana_query_ms)s, %(browser_render_ms)s, %(pipeline_total_ms)s, %(critical_path_ms)s, %(total_ms)s,
    %(dominant_stage)s, %(reason_code)s, %(reason_summary)s, %(trace_id)s, %(status)s, %(details)s
)
ON CONFLICT (run_id) DO UPDATE SET
    completed_at = EXCLUDED.completed_at,
    price_rows = EXCLUDED.price_rows,
    indicator_rows = EXCLUDED.indicator_rows,
    download_ms = EXCLUDED.download_ms,
    normalize_ms = EXCLUDED.normalize_ms,
    analysis_ms = EXCLUDED.analysis_ms,
    store_prices_ms = EXCLUDED.store_prices_ms,
    store_indicators_ms = EXCLUDED.store_indicators_ms,
    grafana_query_ms = EXCLUDED.grafana_query_ms,
    browser_render_ms = EXCLUDED.browser_render_ms,
    pipeline_total_ms = EXCLUDED.pipeline_total_ms,
    critical_path_ms = EXCLUDED.critical_path_ms,
    total_ms = EXCLUDED.total_ms,
    dominant_stage = EXCLUDED.dominant_stage,
    reason_code = EXCLUDED.reason_code,
    reason_summary = EXCLUDED.reason_summary,
    trace_id = EXCLUDED.trace_id,
    status = EXCLUDED.status,
    details = EXCLUDED.details;
"""
    params = {
        "run_id": report["run_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "mode": report["mode"],
        "symbols": report["symbols"],
        "price_rows": int(report["price_rows"]),
        "indicator_rows": int(report["indicator_rows"]),
        "download_ms": timings.get("download_prices"),
        "normalize_ms": timings.get("normalize_prices"),
        "analysis_ms": timings.get("analysis"),
        "store_prices_ms": timings.get("store_prices"),
        "store_indicators_ms": timings.get("store_indicators"),
        "grafana_query_ms": timings.get("grafana_query"),
        "browser_render_ms": timings.get("browser_render"),
        "pipeline_total_ms": report.get("pipeline_total_ms"),
        "critical_path_ms": report.get("critical_path_ms"),
        "total_ms": report.get("total_ms"),
        "dominant_stage": report.get("dominant_stage"),
        "reason_code": report.get("reason_code"),
        "reason_summary": report.get("reason_summary"),
        "trace_id": report.get("trace_id"),
        "status": report["status"],
        "details": Jsonb(report),
    }
    with psycopg.connect(**DB) as conn:
        conn.execute(sql, params)
        conn.commit()


def maybe_run_browser_probe(enabled):
    if not enabled:
        return {"skipped": True, "reason": "not requested"}
    if not shutil.which("npm") or not Path("package.json").exists():
        return {"skipped": True, "reason": "npm/package.json not available in this runtime"}
    completed = subprocess.run(["npm", "run", "test:observability"], check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "Browser observability probe failed:\n"
            + completed.stdout
            + "\n"
            + completed.stderr
        )
    return {"skipped": False, "stdout": completed.stdout.strip()}


def build_links(public_grafana_url, trace_id):
    links = {
        "stock_dashboard": f"{public_grafana_url}/d/local-stock-market/local-stock-market-dashboard?from=now-1y&to=now",
        "observability_dashboard": f"{public_grafana_url}/d/stock-observability-lab/stock-observability-lab?from=now-6h&to=now",
        "pyroscope": os.getenv("PUBLIC_PYROSCOPE_URL", "http://localhost:4040"),
    }
    if trace_id:
        links["tempo_trace"] = f"{public_grafana_url}/explore?schemaVersion=1&panes=%7B%7D&orgId=1&left=%7B%22datasource%22:%22tempo%22,%22queries%22:%5B%7B%22query%22:%22{trace_id}%22%7D%5D%7D"
    return links


def parse_args():
    parser = argparse.ArgumentParser(description="Run the stock observability learning case.")
    parser.add_argument("--mode", choices=["live"], default="live")
    parser.add_argument("--run-browser", action="store_true", help="Run the host browser probe when npm is available.")
    parser.add_argument(
        "--analysis-load-factor",
        type=int,
        default=int(os.getenv("OBS_ANALYSIS_LOAD_FACTOR", "12")),
        help="CPU load multiplier that makes analysis visible in a flame graph.",
    )
    return parser.parse_args()


def run_case(mode="live", analysis_load_factor=12, run_browser=False, report_path=REPORT_PATH):
    grafana_url = os.getenv("GRAFANA_URL", "http://localhost:3000")
    public_grafana_url = os.getenv("PUBLIC_GRAFANA_URL", grafana_url)
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    run_started = time.perf_counter()

    wait_for_db()
    ensure_schema()
    wait_for_grafana(grafana_url)

    with obs_span(
        "stock.observability_case",
        {
            "test.run_id": run_id,
            "test.mode": mode,
            "stock.symbols": ",".join(TICKERS),
            "analysis.load_factor": analysis_load_factor,
        },
    ) as root_span:
        trace_id = trace_id_from_span(root_span)
        pipeline = run_once(load_factor=analysis_load_factor, include_analysis=True)

        started = time.perf_counter()
        grafana_result = query_grafana_display_data(grafana_url)
        pipeline["stage_timings_ms"]["grafana_query"] = elapsed_ms(started)

        browser_probe = maybe_run_browser_probe(run_browser)
        completed_at = datetime.now(timezone.utc)
        latency = explain_latency(pipeline["stage_timings_ms"])

        report = {
            "run_id": run_id,
            "mode": mode,
            "status": "ok",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "trace_id": trace_id,
            "symbols": pipeline["symbols"],
            "period": pipeline["period"],
            "interval": pipeline["interval"],
            "price_rows": pipeline["price_rows"],
            "indicator_rows": pipeline["indicator_rows"],
            "stage_timings_ms": pipeline["stage_timings_ms"],
            "per_symbol": pipeline["per_symbol"],
            "analysis": pipeline.get("analysis", {}),
            "grafana": grafana_result,
            "browser_probe": browser_probe,
            "run_wall_ms": elapsed_ms(run_started),
            "pipeline_total_ms": latency["pipeline_total_ms"],
            "critical_path_ms": latency["critical_path_ms"],
            "total_ms": latency["critical_path_ms"],
            "dominant_stage": latency["dominant_stage"],
            "dominant_stage_ms": latency["dominant_stage_ms"],
            "dominant_stage_share": latency["dominant_stage_share"],
            "reason_code": latency["reason_code"],
            "reason_summary": latency["reason_summary"],
            "latency_explanation": latency,
            "pyroscope_service_name": os.getenv("PYROSCOPE_APPLICATION_NAME", "stock.observability.case"),
            "links": build_links(public_grafana_url, trace_id),
        }
        set_span_attributes(
            root_span,
            {
                "test.trace_id": trace_id,
                "stock.price_rows": pipeline["price_rows"],
                "stock.indicator_rows": pipeline["indicator_rows"],
                "stock.grafana_summary_rows": grafana_result["summary_rows"],
                "stock.grafana_indicator_rows": grafana_result["indicator_rows"],
                "stock.critical_path_ms": latency["critical_path_ms"],
                "stock.dominant_stage": latency["dominant_stage"],
                "stock.reason_code": latency["reason_code"],
            },
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record_observability_run(report, started_at, completed_at)
    flush_traces()
    time.sleep(2)
    return report


def main():
    args = parse_args()
    configure_observability(os.getenv("OTEL_SERVICE_NAME", "stock-observability-case"))
    report = run_case(
        mode=args.mode,
        analysis_load_factor=args.analysis_load_factor,
        run_browser=args.run_browser,
        report_path=REPORT_PATH,
    )

    print(f"ok - observability case run_id={report.get('run_id')}")
    print(f"ok - trace_id={report.get('trace_id')}")
    print(f"ok - report={REPORT_PATH}")
    print(f"ok - observability dashboard={report['links']['observability_dashboard']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("observability case failed")
        raise
