#!/usr/bin/env python3
import argparse
import csv
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import requests
from psycopg.types.json import Jsonb

from lean_latency_reason import explain_lean_latency
from observability_support import (
    configure_observability,
    flush_traces,
    profile_tags,
    set_span_attributes,
    span as obs_span,
    trace_id_from_span,
)


DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "stockdash"),
    "user": os.getenv("DB_USER", "stock"),
    "password": os.getenv("DB_PASSWORD", "stock"),
}

LEAN_PLATFORM_ROOT = Path(os.getenv("LEAN_PLATFORM_ROOT", "/Users/kaermax/lean-platform"))
LEAN_DATA_DIR = Path(os.getenv("LEAN_DATA_DIR", "/Users/kaermax/Lean/Data"))
LEAN_RUNS_DIR = Path(os.getenv("LEAN_RUNS_DIR", str(LEAN_PLATFORM_ROOT / "runs")))
LEAN_ALGORITHM_PATH = Path(os.getenv("LEAN_ALGORITHM_PATH", str(LEAN_PLATFORM_ROOT / "DockerDemoAlgorithm.py")))
LEAN_PLOT_SCRIPT = Path(os.getenv("LEAN_PLOT_SCRIPT", str(LEAN_PLATFORM_ROOT / "plot_results.py")))
DEFAULT_DOCKER_IMAGE = os.getenv("LEAN_DOCKER_IMAGE", "quantconnect/lean:latest")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000").rstrip("/")
PUBLIC_GRAFANA_URL = os.getenv("PUBLIC_GRAFANA_URL", GRAFANA_URL).rstrip("/")
PUBLIC_PYROSCOPE_URL = os.getenv("PUBLIC_PYROSCOPE_URL", "http://localhost:4040").rstrip("/")
REPORT_PATH = Path(os.getenv("LEAN_OBS_REPORT", "test-results/lean-observability-case.json"))
PYROSCOPE_RUNNER_APP = os.getenv("PYROSCOPE_APPLICATION_NAME", "lean.backtest.runner")


class LeanCaseError(RuntimeError):
    pass


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def elapsed_ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


def wait_for_db():
    last_error = None
    for _ in range(40):
        try:
            with psycopg.connect(**DB) as conn:
                conn.execute("SELECT 1")
                return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise LeanCaseError(f"Postgres did not become ready: {last_error}")


def wait_for_grafana(base_url=GRAFANA_URL):
    last_error = None
    for _ in range(40):
        try:
            response = requests.get(f"{base_url}/api/health", timeout=5)
            response.raise_for_status()
            payload = response.json()
            if payload.get("database") == "ok":
                return payload
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise LeanCaseError(f"Grafana did not become ready: {last_error}")


def ensure_schema():
    sql = """
CREATE TABLE IF NOT EXISTS lean_backtest_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    fast INTEGER NOT NULL,
    slow INTEGER NOT NULL,
    cash NUMERIC NOT NULL,
    data_source TEXT NOT NULL,
    data_rows INTEGER,
    data_first_date DATE,
    data_last_date DATE,
    overwrite_data BOOLEAN NOT NULL DEFAULT false,
    docker_image TEXT NOT NULL,
    lean_container_name TEXT,
    result_json_path TEXT,
    summary_json_path TEXT,
    report_html_path TEXT,
    statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id TEXT,
    profile_query TEXT,
    engine_profile_query TEXT,
    critical_path_ms NUMERIC,
    pipeline_total_ms NUMERIC,
    total_ms NUMERIC,
    dominant_stage TEXT,
    reason_code TEXT,
    reason_summary TEXT,
    error TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS lean_backtest_stage_timings (
    run_id UUID NOT NULL REFERENCES lean_backtest_runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    duration_ms NUMERIC NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_completed_at ON lean_backtest_runs (completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_critical_path ON lean_backtest_runs (critical_path_ms);
CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_symbol ON lean_backtest_runs (symbol, completed_at DESC);
"""
    with psycopg.connect(**DB) as conn:
        conn.execute(sql)
        conn.commit()


def symbol_key(symbol):
    cleaned = str(symbol or "").strip().lower()
    if not cleaned or not all(ch.isalnum() or ch in ".-" for ch in cleaned):
        raise LeanCaseError(f"Invalid symbol: {symbol!r}")
    return cleaned


def parse_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise LeanCaseError(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


def lean_price(value):
    return str(int(round(float(value) * 10000)))


def daily_zip_path(symbol):
    return LEAN_DATA_DIR / "equity" / "usa" / "daily" / f"{symbol_key(symbol)}.zip"


def ensure_equity_dirs():
    for relative in ("equity/usa/daily", "equity/usa/map_files", "equity/usa/factor_files"):
        (LEAN_DATA_DIR / relative).mkdir(parents=True, exist_ok=True)


def write_auxiliary_files(symbol, first_date):
    ticker = symbol_key(symbol)
    ensure_equity_dirs()
    start = first_date.strftime("%Y%m%d")
    map_file = LEAN_DATA_DIR / "equity" / "usa" / "map_files" / f"{ticker}.csv"
    if not map_file.exists():
        map_file.write_text(f"{start},{ticker},P\n20501231,{ticker},P\n", encoding="utf-8")
    factor_file = LEAN_DATA_DIR / "equity" / "usa" / "factor_files" / f"{ticker}.csv"
    if not factor_file.exists():
        factor_file.write_text(f"{start},1,1,0\n20501231,1,1,0\n", encoding="utf-8")


def normalize_rows(rows):
    normalized = []
    for row in rows:
        try:
            item_date = parse_date(str(row["date"])[:10])
            open_price = float(row["open"])
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            volume = int(float(row["volume"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LeanCaseError(f"Invalid OHLCV row: {row}") from exc
        if min(open_price, high, low, close) <= 0 or volume < 0:
            continue
        normalized.append((item_date, open_price, high, low, close, volume))
    normalized.sort(key=lambda item: item[0])
    if not normalized:
        raise LeanCaseError("No valid OHLCV rows found.")
    return normalized


def write_lean_daily_zip(symbol, rows, source, overwrite=False):
    ticker = symbol_key(symbol)
    normalized = normalize_rows(rows)
    ensure_equity_dirs()
    write_auxiliary_files(ticker, normalized[0][0])
    output = daily_zip_path(ticker)
    if output.exists() and not overwrite:
        raise LeanCaseError(f"{output} already exists; enable overwriteData to replace it.")

    csv_lines = []
    for item_date, open_price, high, low, close, volume in normalized:
        csv_lines.append(
            f"{item_date:%Y%m%d} 00:00,{lean_price(open_price)},{lean_price(high)},"
            f"{lean_price(low)},{lean_price(close)},{volume}"
        )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{ticker}.csv", "\n".join(csv_lines) + "\n")

    return {
        "symbol": ticker.upper(),
        "source": source,
        "rows": len(csv_lines),
        "first_date": normalized[0][0].isoformat(),
        "last_date": normalized[-1][0].isoformat(),
        "lean_file": str(output),
    }


def read_lean_daily_zip_metadata(symbol, start=None, end=None):
    path = daily_zip_path(symbol)
    if not path.exists():
        raise LeanCaseError(f"Missing LEAN daily data for {symbol.upper()}: {path}")
    start_date = parse_date(start) if start else None
    end_date = parse_date(end) if end else None
    dates = []
    ticker = symbol_key(symbol)
    with zipfile.ZipFile(path) as archive:
        name = f"{ticker}.csv"
        if name not in archive.namelist():
            names = archive.namelist()
            if not names:
                raise LeanCaseError(f"LEAN daily zip is empty: {path}")
            name = names[0]
        with archive.open(name) as file:
            for raw in file:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                item_date = datetime.strptime(line.split(",", 1)[0].split()[0], "%Y%m%d").date()
                if start_date and item_date < start_date:
                    continue
                if end_date and item_date > end_date:
                    continue
                dates.append(item_date)
    if not dates:
        raise LeanCaseError(f"No local LEAN rows for {symbol.upper()} in {start}..{end}.")
    return {
        "symbol": symbol.upper(),
        "source": "local",
        "rows": len(dates),
        "first_date": min(dates).isoformat(),
        "last_date": max(dates).isoformat(),
        "lean_file": str(path),
    }


def download_text(url):
    response = requests.get(url, headers={"User-Agent": "lean-observability-lab/1.0"}, timeout=45)
    response.raise_for_status()
    return response.text


def fetch_alpha_vantage_rows(symbol, api_key, outputsize="compact"):
    if not api_key:
        raise LeanCaseError("Alpha Vantage requires alphaVantageApiKey.")
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol.upper(),
        "outputsize": outputsize,
        "datatype": "csv",
        "apikey": api_key,
    }
    text = download_text("https://www.alphavantage.co/query?" + urllib.parse.urlencode(params))
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if "timestamp,open,high,low,close,volume" not in first_line:
        raise LeanCaseError("Alpha Vantage did not return daily CSV data. Check API key, limits, and entitlement.")
    return [
        {
            "date": row["timestamp"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in csv.DictReader(text.splitlines())
    ]


def fetch_stooq_rows(symbol):
    ticker = symbol_key(symbol).replace(".", "-")
    text = download_text(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(ticker)}.us&i=d")
    lines = text.splitlines()
    if text.lstrip().startswith("<!DOCTYPE") or "__verify" in text:
        raise LeanCaseError("Stooq is requiring browser verification from this network; choose another provider.")
    if not lines or not lines[0].lower().startswith("date,open,high,low,close,volume"):
        raise LeanCaseError(f"Stooq did not return daily CSV data for {symbol}.")
    rows = []
    for row in csv.DictReader(lines):
        if not row or (row.get("Close") or "").lower() == "null":
            continue
        rows.append(
            {
                "date": row["Date"],
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "volume": row["Volume"],
            }
        )
    if not rows:
        raise LeanCaseError(f"No Stooq rows found for {symbol}.")
    return rows


def fetch_yahoo_rows(symbol, start="2000-01-01", end=None):
    start_ts = int(datetime.combine(parse_date(start), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_date = parse_date(end) if end else date.today()
    end_ts = int(datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    ticker = symbol_key(symbol).upper().replace(".", "-")
    params = urllib.parse.urlencode(
        {
            "period1": start_ts,
            "period2": end_ts,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
    )
    text = download_text(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?{params}")
    if "Too Many Requests" in text:
        raise LeanCaseError("Yahoo Finance is rate-limiting this network; choose another provider.")
    data = json.loads(text)
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = (data.get("chart") or {}).get("error") or {}
        raise LeanCaseError(f"Yahoo Finance did not return chart data for {symbol}: {error}")
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    rows = []
    for index, timestamp in enumerate(timestamps):
        try:
            open_price = quote["open"][index]
            high = quote["high"][index]
            low = quote["low"][index]
            close = quote["close"][index]
            volume = quote["volume"][index] or 0
        except (KeyError, IndexError):
            continue
        if None in (open_price, high, low, close):
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
                "open": str(open_price),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "volume": str(volume),
            }
        )
    if not rows:
        raise LeanCaseError(f"No Yahoo Finance rows found for {symbol}.")
    return rows


def prepare_data(request):
    symbol = request["symbol"]
    source = request.get("dataSource", "local")
    overwrite = bool(request.get("overwriteData", False))
    if source == "local":
        return read_lean_daily_zip_metadata(symbol, request["start"], request["end"])
    if source == "yahoo":
        rows = fetch_yahoo_rows(symbol, request["start"], request["end"])
        return write_lean_daily_zip(symbol, rows, "yahoo", overwrite=overwrite)
    if source == "stooq":
        rows = fetch_stooq_rows(symbol)
        return write_lean_daily_zip(symbol, rows, "stooq", overwrite=overwrite)
    if source == "alpha_vantage":
        rows = fetch_alpha_vantage_rows(
            symbol,
            request.get("alphaVantageApiKey") or os.getenv("ALPHA_VANTAGE_API_KEY"),
            request.get("alphaVantageOutputSize", "compact"),
        )
        return write_lean_daily_zip(symbol, rows, "alpha_vantage", overwrite=overwrite)
    raise LeanCaseError(f"Unsupported dataSource: {source}")


def lean_job_parameters(parameters):
    return {key: str(value) for key, value in parameters.items() if value is not None}


def base_config(run_id, parameters):
    return {
        "environment": "backtesting",
        "algorithm-id": run_id,
        "backtest-name": f"Local {parameters['ticker']} EMA Backtest",
        "algorithm-type-name": "DockerDemoAlgorithm",
        "algorithm-language": "Python",
        "algorithm-location": "/Lean/DockerDemoAlgorithm.py",
        "data-folder": "/Lean/Data",
        "results-destination-folder": "/Lean/Results",
        "close-automatically": True,
        "debugging": False,
        "debugging-method": "LocalCmdline",
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "data-channel-provider": "DataChannelProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
        "symbol-minute-limit": 10000,
        "symbol-second-limit": 10000,
        "symbol-tick-limit": 10000,
        "seed-lookback-period": 5,
        "seed-retry-minute-lookback-period": 1440,
        "seed-retry-hour-lookback-period": 24,
        "seed-retry-daily-lookback-period": 10,
        "ignore-unknown-asset-holdings": True,
        "show-missing-data-logs": True,
        "maximum-warmup-history-days-look-back": 5,
        "maximum-data-points-per-chart-series": 1000000,
        "maximum-chart-series": 30,
        "force-exchange-always-open": False,
        "transaction-log": "",
        "reserved-words-prefix": "@",
        "job-user-id": "0",
        "project-id": "0",
        "api-access-token": "",
        "job-organization-id": "",
        "parameters": lean_job_parameters(parameters),
        "python-additional-paths": [],
        "environments": {
            "backtesting": {
                "live-mode": False,
                "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
                "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
                "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
                "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
                "history-provider": [
                    "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
                ],
                "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
            }
        },
    }


def validate_request(request):
    symbol = symbol_key(request.get("symbol", "SPY")).upper()
    start = parse_date(request.get("start", "2013-01-01"))
    end = parse_date(request.get("end", "2013-06-30"))
    if end <= start:
        raise LeanCaseError("end must be after start.")
    fast = int(request.get("fast", 10))
    slow = int(request.get("slow", 30))
    if fast <= 0 or slow <= 0:
        raise LeanCaseError("fast and slow must be positive.")
    cash = float(request.get("cash", 100000))
    if cash <= 0:
        raise LeanCaseError("cash must be positive.")
    data_source = request.get("dataSource", "local")
    if data_source not in {"local", "yahoo", "stooq", "alpha_vantage"}:
        raise LeanCaseError(f"Unsupported dataSource: {data_source}")
    return {
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "fast": fast,
        "slow": slow,
        "cash": cash,
        "dataSource": data_source,
        "overwriteData": bool(request.get("overwriteData", data_source != "local")),
        "alphaVantageApiKey": request.get("alphaVantageApiKey"),
        "alphaVantageOutputSize": request.get("alphaVantageOutputSize", "compact"),
        "dockerImage": request.get("dockerImage") or DEFAULT_DOCKER_IMAGE,
    }


def docker_command(run_id, config_path, results_dir, image, symbol):
    docker = shutil.which("docker")
    if not docker:
        raise LeanCaseError("docker command not found.")
    container_name = f"lean-observability-{run_id.split('-')[0]}"
    command = [
        docker,
        "run",
        "--rm",
        "--name",
        container_name,
        "--label",
        "lean.observability=true",
        "--label",
        f"lean.run_id={run_id}",
        "--label",
        f"lean.symbol={symbol}",
        "--label",
        "service.name=lean.engine",
        "-v",
        f"{config_path}:/Lean/Launcher/bin/Debug/config.json:ro",
        "-v",
        f"{LEAN_ALGORITHM_PATH}:/Lean/DockerDemoAlgorithm.py:ro",
        "-v",
        f"{LEAN_DATA_DIR}:/Lean/Data:ro",
        "-v",
        f"{results_dir}:/Lean/Results",
        image,
    ]
    return container_name, command


def run_command_stream(command):
    output = []
    logging.info("running: %s", " ".join(command))
    process = subprocess.Popen(
        command,
        cwd=LEAN_PLATFORM_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        stripped = line.rstrip()
        output.append(stripped)
        logging.info("[lean] %s", stripped)
    return process.wait(), output


def load_json(path):
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def extract_statistics(result_json, summary_json=None):
    source = summary_json if summary_json and summary_json.exists() else result_json
    if not source.exists():
        return {}
    data = load_json(source)
    return data.get("statistics") or data.get("Statistics") or {}


def summarize_result_profile_data(result_data, load_factor):
    charts = result_data.get("charts") or {}
    checksum = 0.0
    iterations = max(1, int(load_factor))
    for _ in range(iterations):
        for chart in charts.values():
            for series in (chart.get("series") or {}).values():
                for row in series.get("values", []):
                    if len(row) >= 2:
                        try:
                            value = float(row[-1])
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(value):
                            checksum += math.sin(value) * math.cos(value / 2)
    return round(checksum, 6)


def render_report(result_json, report_html):
    completed = subprocess.run(
        [sys.executable, str(LEAN_PLOT_SCRIPT), "--input", str(result_json), "--output", str(report_html)],
        cwd=LEAN_PLATFORM_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise LeanCaseError("Report rendering failed:\n" + completed.stdout + "\n" + completed.stderr)


def frame_row_count(frames):
    total = 0
    for frame in frames:
        values = frame.get("data", {}).get("values", [])
        if values:
            total += len(values[0])
    return total


def query_datasource(raw_sql, fmt="table"):
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": "stock-postgres", "type": "postgres"},
                "rawSql": raw_sql,
                "format": fmt,
                "rawQuery": True,
                "intervalMs": 1000,
                "maxDataPoints": 2000,
            }
        ],
        "from": "now-6h",
        "to": "now",
        "range": {"from": "now-6h", "to": "now", "raw": {"from": "now-6h", "to": "now"}},
    }
    response = requests.post(f"{GRAFANA_URL}/api/ds/query", json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def query_grafana_display_data(run_id):
    sql = f"""
SELECT
  completed_at,
  symbol,
  data_source,
  critical_path_ms::double precision AS critical_path_ms,
  reason_summary
FROM lean_backtest_runs
WHERE run_id = '{run_id}'::uuid;
""".strip()
    payload = query_datasource(sql, "table")
    rows = frame_row_count(payload["results"]["A"]["frames"])
    if rows < 1:
        raise LeanCaseError("Grafana datasource query returned no Lean backtest rows.")
    return {"rows": rows}


@contextmanager
def measured_stage(stage_timings, stage, attributes=None):
    started = time.perf_counter()
    with profile_tags({"stage": stage, **{k: v for k, v in (attributes or {}).items() if isinstance(v, (str, int, float))}}):
        with obs_span(f"lean.{stage}", attributes or {}) as active_span:
            try:
                yield active_span
            finally:
                duration = elapsed_ms(started)
                stage_timings[stage] = duration
                set_span_attributes(active_span, {"lean.stage_duration_ms": duration})


def build_links(trace_id, run_id):
    links = {
        "lean_dashboard": f"{PUBLIC_GRAFANA_URL}/d/lean-backtest-observability-lab/lean-backtest-observability-lab?from=now-6h&to=now",
        "pyroscope": PUBLIC_PYROSCOPE_URL,
    }
    if trace_id:
        links["tempo_trace"] = f"{PUBLIC_GRAFANA_URL}/explore?schemaVersion=1&panes=%7B%7D&orgId=1&left=%7B%22datasource%22:%22tempo%22,%22queries%22:%5B%7B%22query%22:%22{trace_id}%22%7D%5D%7D"
    engine_query = urllib.parse.quote(
        f'process_cpu:cpu:nanoseconds:cpu:nanoseconds{{service_name="lean.engine",run_id="{run_id}"}}',
        safe="",
    )
    links["engine_flame_graph"] = f"{PUBLIC_PYROSCOPE_URL}/pyroscope/render?query={engine_query}&from=now-30m&until=now"
    return links


def record_lean_run(report):
    run = report["request"]
    latency = report.get("latency", {})
    params = {
        "run_id": report["run_id"],
        "started_at": report["started_at"],
        "completed_at": report["completed_at"],
        "status": report["status"],
        "symbol": run["symbol"],
        "start_date": run["start"],
        "end_date": run["end"],
        "fast": run["fast"],
        "slow": run["slow"],
        "cash": run["cash"],
        "data_source": run["dataSource"],
        "data_rows": (report.get("data") or {}).get("rows"),
        "data_first_date": (report.get("data") or {}).get("first_date"),
        "data_last_date": (report.get("data") or {}).get("last_date"),
        "overwrite_data": run["overwriteData"],
        "docker_image": run["dockerImage"],
        "lean_container_name": report.get("lean_container_name"),
        "result_json_path": report.get("result_json_path"),
        "summary_json_path": report.get("summary_json_path"),
        "report_html_path": report.get("report_html_path"),
        "statistics": Jsonb(report.get("statistics") or {}),
        "trace_id": report.get("trace_id"),
        "profile_query": report.get("profile_query"),
        "engine_profile_query": report.get("engine_profile_query"),
        "critical_path_ms": latency.get("critical_path_ms"),
        "pipeline_total_ms": latency.get("pipeline_total_ms"),
        "total_ms": latency.get("critical_path_ms"),
        "dominant_stage": latency.get("dominant_stage"),
        "reason_code": latency.get("reason_code"),
        "reason_summary": latency.get("reason_summary"),
        "error": report.get("error"),
        "details": Jsonb(report),
    }
    sql = """
INSERT INTO lean_backtest_runs (
    run_id, started_at, completed_at, status, symbol, start_date, end_date, fast, slow, cash,
    data_source, data_rows, data_first_date, data_last_date, overwrite_data, docker_image,
    lean_container_name, result_json_path, summary_json_path, report_html_path, statistics,
    trace_id, profile_query, engine_profile_query, critical_path_ms, pipeline_total_ms, total_ms,
    dominant_stage, reason_code, reason_summary, error, details
) VALUES (
    %(run_id)s, %(started_at)s, %(completed_at)s, %(status)s, %(symbol)s, %(start_date)s, %(end_date)s,
    %(fast)s, %(slow)s, %(cash)s, %(data_source)s, %(data_rows)s, %(data_first_date)s, %(data_last_date)s,
    %(overwrite_data)s, %(docker_image)s, %(lean_container_name)s, %(result_json_path)s, %(summary_json_path)s,
    %(report_html_path)s, %(statistics)s, %(trace_id)s, %(profile_query)s, %(engine_profile_query)s,
    %(critical_path_ms)s, %(pipeline_total_ms)s, %(total_ms)s, %(dominant_stage)s, %(reason_code)s,
    %(reason_summary)s, %(error)s, %(details)s
)
ON CONFLICT (run_id) DO UPDATE SET
    completed_at = EXCLUDED.completed_at,
    status = EXCLUDED.status,
    data_rows = EXCLUDED.data_rows,
    data_first_date = EXCLUDED.data_first_date,
    data_last_date = EXCLUDED.data_last_date,
    lean_container_name = EXCLUDED.lean_container_name,
    result_json_path = EXCLUDED.result_json_path,
    summary_json_path = EXCLUDED.summary_json_path,
    report_html_path = EXCLUDED.report_html_path,
    statistics = EXCLUDED.statistics,
    trace_id = EXCLUDED.trace_id,
    profile_query = EXCLUDED.profile_query,
    engine_profile_query = EXCLUDED.engine_profile_query,
    critical_path_ms = EXCLUDED.critical_path_ms,
    pipeline_total_ms = EXCLUDED.pipeline_total_ms,
    total_ms = EXCLUDED.total_ms,
    dominant_stage = EXCLUDED.dominant_stage,
    reason_code = EXCLUDED.reason_code,
    reason_summary = EXCLUDED.reason_summary,
    error = EXCLUDED.error,
    details = EXCLUDED.details;
"""
    with psycopg.connect(**DB) as conn:
        conn.execute(sql, params)
        for stage, duration_ms in (report.get("stage_timings_ms") or {}).items():
            conn.execute(
                """
INSERT INTO lean_backtest_stage_timings (run_id, stage, duration_ms, details)
VALUES (%s, %s, %s, '{}'::jsonb)
ON CONFLICT (run_id, stage) DO UPDATE SET duration_ms = EXCLUDED.duration_ms;
""",
                (report["run_id"], stage, duration_ms),
            )
        conn.commit()


def write_report(report, report_path=REPORT_PATH):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=json_default), encoding="utf-8")


def run_case(request, report_path=REPORT_PATH):
    wait_for_db()
    ensure_schema()
    wait_for_grafana()

    validated = validate_request(request)
    run_id = str(uuid.uuid4())
    short_id = run_id.split("-")[0]
    run_name = f"obs-{validated['symbol'].lower()}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{short_id}"
    run_dir = LEAN_RUNS_DIR / run_name
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    stage_timings = {}
    report = {
        "run_id": run_id,
        "run_name": run_name,
        "status": "running",
        "started_at": started_at.isoformat(),
        "request": validated,
        "stage_timings_ms": stage_timings,
        "pyroscope_service_name": PYROSCOPE_RUNNER_APP,
        "profile_query": f'process_cpu:cpu:nanoseconds:cpu:nanoseconds{{service_name="{PYROSCOPE_RUNNER_APP}"}}',
        "engine_profile_query": f'process_cpu:cpu:nanoseconds:cpu:nanoseconds{{service_name="lean.engine",run_id="{run_id}"}}',
    }

    try:
        with obs_span(
            "lean.case",
            {
                "lean.run_id": run_id,
                "lean.symbol": validated["symbol"],
                "lean.data_source": validated["dataSource"],
                "lean.start": validated["start"],
                "lean.end": validated["end"],
                "lean.fast": validated["fast"],
                "lean.slow": validated["slow"],
            },
        ) as root_span:
            trace_id = trace_id_from_span(root_span)
            report["trace_id"] = trace_id

            with measured_stage(stage_timings, "data_refresh", {"symbol": validated["symbol"], "data_source": validated["dataSource"]}):
                report["data"] = prepare_data(validated)

            parameters = {
                "ticker": validated["symbol"],
                "start": validated["start"],
                "end": validated["end"],
                "cash": validated["cash"],
                "fast": validated["fast"],
                "slow": validated["slow"],
            }
            config_path = run_dir / "config.json"
            with measured_stage(stage_timings, "config_write", {"symbol": validated["symbol"]}):
                config_path.write_text(json.dumps(base_config(run_name, parameters), indent=2), encoding="utf-8")

            container_name, command = docker_command(
                run_id,
                config_path,
                results_dir,
                validated["dockerImage"],
                validated["symbol"],
            )
            report["lean_container_name"] = container_name
            with measured_stage(stage_timings, "docker_backtest", {"symbol": validated["symbol"], "container": container_name}):
                exit_code, output = run_command_stream(command)
                report["lean_exit_code"] = exit_code
                report["lean_output_tail"] = output[-80:]
                if exit_code != 0:
                    raise LeanCaseError(f"LEAN Docker backtest failed with exit code {exit_code}.")

            result_json = results_dir / f"{run_name}.json"
            summary_json = results_dir / f"{run_name}-summary.json"
            report_html = results_dir / "report.html"
            if not result_json.exists():
                raise LeanCaseError(f"Expected LEAN result file not found: {result_json}")

            with measured_stage(stage_timings, "result_parse", {"symbol": validated["symbol"]}):
                result_data = load_json(result_json)
                statistics = extract_statistics(result_json, summary_json if summary_json.exists() else None)
                checksum = summarize_result_profile_data(
                    result_data,
                    int(os.getenv("LEAN_OBS_PROFILE_LOAD_FACTOR", "8")),
                )
                report["statistics"] = statistics
                report["result_profile_checksum"] = checksum
                report["result_json_path"] = str(result_json)
                report["summary_json_path"] = str(summary_json) if summary_json.exists() else None

            with measured_stage(stage_timings, "report_render", {"symbol": validated["symbol"]}):
                render_report(result_json, report_html)
                report["report_html_path"] = str(report_html)

            latency = explain_lean_latency(stage_timings)
            report["latency"] = latency
            report["critical_path_ms"] = latency["critical_path_ms"]
            report["reason_summary"] = latency["reason_summary"]
            report["completed_at"] = datetime.now(timezone.utc).isoformat()
            report["status"] = "ok"
            report["links"] = build_links(trace_id, run_id)
            set_span_attributes(
                root_span,
                {
                    "lean.critical_path_ms": latency["critical_path_ms"],
                    "lean.dominant_stage": latency["dominant_stage"],
                    "lean.reason_code": latency["reason_code"],
                },
            )
            record_lean_run(report)

            with measured_stage(stage_timings, "grafana_query", {"symbol": validated["symbol"]}):
                report["grafana_query"] = query_grafana_display_data(run_id)

            latency = explain_lean_latency(stage_timings)
            report["latency"] = latency
            report["critical_path_ms"] = latency["critical_path_ms"]
            report["reason_summary"] = latency["reason_summary"]
            report["completed_at"] = datetime.now(timezone.utc).isoformat()
            report["links"] = build_links(trace_id, run_id)
            record_lean_run(report)
            write_report(report, report_path)
            return report
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["latency"] = explain_lean_latency(stage_timings)
        report["critical_path_ms"] = report["latency"]["critical_path_ms"]
        report["reason_summary"] = report["latency"]["reason_summary"]
        report["links"] = build_links(report.get("trace_id"), run_id)
        try:
            record_lean_run(report)
            write_report(report, report_path)
        finally:
            raise
    finally:
        flush_traces()


def parse_args():
    parser = argparse.ArgumentParser(description="Run a real LEAN backtest observability case.")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2013-01-01")
    parser.add_argument("--end", default="2013-06-30")
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--cash", type=float, default=100000)
    parser.add_argument("--data-source", choices=["local", "yahoo", "stooq", "alpha_vantage"], default="local")
    parser.add_argument("--overwrite-data", action="store_true")
    parser.add_argument("--alpha-vantage-api-key", default=None)
    parser.add_argument("--image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--report", default=str(REPORT_PATH))
    return parser.parse_args()


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    configure_observability(os.getenv("OTEL_SERVICE_NAME", "lean-backtest-runner"))
    args = parse_args()
    request = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "fast": args.fast,
        "slow": args.slow,
        "cash": args.cash,
        "dataSource": args.data_source,
        "overwriteData": args.overwrite_data or args.data_source != "local",
        "alphaVantageApiKey": args.alpha_vantage_api_key,
        "dockerImage": args.image,
    }
    report = run_case(request, Path(args.report))
    print(json.dumps(report, indent=2, default=json_default))


if __name__ == "__main__":
    main()
