#!/usr/bin/env python3
import json
import logging
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from lean_observability_case import (
    DB,
    DEFAULT_DOCKER_IMAGE,
    GRAFANA_URL,
    LEAN_DATA_DIR,
    PUBLIC_GRAFANA_URL,
    ensure_schema,
    run_case,
    wait_for_db,
    wait_for_grafana,
)
from observability_support import configure_observability


HOST = os.getenv("LEAN_OBS_CONTROL_HOST", "0.0.0.0")
PORT = int(os.getenv("LEAN_OBS_CONTROL_PORT", "8081"))

JOB_LOCK = threading.Lock()
JOB_STATE = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "error": None,
    "run_id": None,
    "trace_id": None,
    "critical_path_ms": None,
    "reason_summary": None,
}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lean Backtest Observability Control</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f7f8fa; color: #1f2937; }
    main { max-width: 1180px; margin: 0 auto; padding: 26px 20px 42px; }
    header { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 18px; }
    h1 { margin: 0 0 6px; font-size: 25px; line-height: 1.2; }
    p { margin: 0; color: #526071; }
    a { color: #0f766e; font-weight: 650; text-decoration: none; }
    button { border: 0; border-radius: 6px; background: #0f766e; color: white; font-size: 15px; font-weight: 700; padding: 11px 15px; cursor: pointer; }
    button:disabled { background: #8aa5a1; cursor: wait; }
    label { display: grid; gap: 6px; font-size: 12px; color: #64748b; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
    input, select { border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 10px; font-size: 14px; background: white; color: #111827; min-width: 0; }
    .panel { background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }
    .form-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; align-items: end; margin-bottom: 16px; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
    .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
    .value { font-size: 23px; font-weight: 760; line-height: 1.2; word-break: break-word; }
    .status { display: inline-flex; align-items: center; gap: 8px; }
    .dot { width: 9px; height: 9px; border-radius: 999px; background: #94a3b8; }
    .running .dot { background: #d97706; }
    .ok .dot { background: #16a34a; }
    .failed .dot { background: #dc2626; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
    th { color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .muted { color: #64748b; }
    @media (max-width: 980px) { .form-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
    @media (max-width: 720px) {
      header { display: block; }
      .actions { margin-top: 14px; }
      .form-grid, .grid { grid-template-columns: 1fr; }
      main { padding: 20px 14px 32px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Lean Backtest Observability Control</h1>
        <p>运行本地 lean-platform 的真实 LEAN 股票回测，生成 critical path tracing 和 LEAN 容器 flame graph。</p>
      </div>
      <div class="actions">
        <button id="runButton">Run Backtest</button>
        <a id="grafanaLink" href="#" target="_blank">Open Lean Lab</a>
      </div>
    </header>

    <section class="panel">
      <div class="form-grid">
        <label>Data Source<select id="dataSource"><option value="local">local</option><option value="yahoo">yahoo</option><option value="stooq">stooq</option><option value="alpha_vantage">alpha_vantage</option></select></label>
        <label>Symbol<input id="symbol" value="SPY"></label>
        <label>Start<input id="start" value="2013-01-01"></label>
        <label>End<input id="end" value="2013-06-30"></label>
        <label>Fast EMA<input id="fast" value="10" type="number" min="1"></label>
        <label>Slow EMA<input id="slow" value="30" type="number" min="1"></label>
        <label>Cash<input id="cash" value="100000" type="number" min="1"></label>
        <label>Overwrite<select id="overwriteData"><option value="true">true</option><option value="false">false</option></select></label>
        <label>Alpha Key<input id="alphaVantageApiKey" type="password" autocomplete="off"></label>
      </div>
    </section>

    <section class="grid" style="margin-top:16px;">
      <div class="panel">
        <div class="label">Job Status</div>
        <div id="jobStatus" class="value status"><span class="dot"></span><span>idle</span></div>
      </div>
      <div class="panel">
        <div class="label">Latest Critical Path</div>
        <div id="latestLatency" class="value">-</div>
      </div>
      <div class="panel">
        <div class="label">Latest Reason</div>
        <div id="latestReason" class="value" style="font-size:16px;font-weight:650;">-</div>
      </div>
    </section>

    <section class="panel" style="margin-bottom:16px;">
      <div class="label">Latency Percentiles</div>
      <table>
        <thead><tr><th>Percentile</th><th>Latency ms</th><th>Samples</th><th>Grafana Drilldown</th></tr></thead>
        <tbody id="percentiles"><tr><td colspan="4" class="muted">loading</td></tr></tbody>
      </table>
    </section>

    <section class="panel">
      <div class="label">Recent Lean Backtests</div>
      <table>
        <thead><tr><th>Completed</th><th>Symbol</th><th>Source</th><th>Critical Path</th><th>Dominant Stage</th><th>Reason</th></tr></thead>
        <tbody id="runs"><tr><td colspan="6" class="muted">loading</td></tr></tbody>
      </table>
    </section>
  </main>

  <script>
    const grafanaBase = "__PUBLIC_GRAFANA_URL__";
    const labUrl = `${grafanaBase}/d/lean-backtest-observability-lab/lean-backtest-observability-lab?from=now-6h&to=now`;
    document.getElementById("grafanaLink").href = labUrl;
    function text(value) { return value === null || value === undefined || value === "" ? "-" : String(value); }
    function latency(value) { return value ? `${Number(value).toFixed(1)} ms` : "-"; }
    function drilldown(percentile) { return `${labUrl}&var-percentile=${encodeURIComponent(percentile)}`; }
    async function getJson(url, options) {
      const response = await fetch(url, options);
      const body = await response.text();
      if (!response.ok) throw new Error(body || response.statusText);
      return JSON.parse(body);
    }
    function requestBody() {
      return {
        dataSource: document.getElementById("dataSource").value,
        symbol: document.getElementById("symbol").value,
        start: document.getElementById("start").value,
        end: document.getElementById("end").value,
        fast: Number(document.getElementById("fast").value),
        slow: Number(document.getElementById("slow").value),
        cash: Number(document.getElementById("cash").value),
        overwriteData: document.getElementById("overwriteData").value === "true",
        alphaVantageApiKey: document.getElementById("alphaVantageApiKey").value || undefined,
      };
    }
    function setJob(job) {
      const button = document.getElementById("runButton");
      const status = document.getElementById("jobStatus");
      status.className = `value status ${job.status === "running" ? "running" : job.status === "failed" ? "failed" : job.status === "ok" ? "ok" : ""}`;
      status.querySelector("span:last-child").textContent = job.status || "idle";
      button.disabled = job.status === "running";
      if (job.critical_path_ms) document.getElementById("latestLatency").textContent = latency(job.critical_path_ms);
      if (job.reason_summary) document.getElementById("latestReason").textContent = job.reason_summary;
      if (job.error) document.getElementById("latestReason").textContent = job.error;
    }
    async function refreshSummary() {
      const summary = await getJson("/api/summary");
      document.getElementById("percentiles").innerHTML = summary.percentiles.length
        ? summary.percentiles.map((row) => `<tr><td>${row.percentile}</td><td>${latency(row.latency_ms)}</td><td>${row.samples}</td><td><a href="${drilldown(row.percentile)}" target="_blank">Open ${row.percentile}</a></td></tr>`).join("")
        : `<tr><td colspan="4" class="muted">No successful Lean backtests yet</td></tr>`;
      document.getElementById("runs").innerHTML = summary.runs.length
        ? summary.runs.map((row) => `<tr><td>${text(row.completed_at)}</td><td>${text(row.symbol)}</td><td>${text(row.data_source)}</td><td>${latency(row.critical_path_ms)}</td><td>${text(row.dominant_stage)}</td><td>${text(row.reason_summary)}</td></tr>`).join("")
        : `<tr><td colspan="6" class="muted">No successful Lean backtests yet</td></tr>`;
      if (summary.runs[0]) {
        document.getElementById("latestLatency").textContent = latency(summary.runs[0].critical_path_ms);
        document.getElementById("latestReason").textContent = text(summary.runs[0].reason_summary);
      }
    }
    async function refreshJob() {
      const job = await getJson("/api/job");
      setJob(job);
      return job;
    }
    document.getElementById("runButton").addEventListener("click", async () => {
      try {
        setJob(await getJson("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody()) }));
      } catch (error) {
        setJob({ status: "failed", error: error.message });
      }
    });
    async function tick() {
      try {
        const job = await refreshJob();
        await refreshSummary();
        if (job.status === "running") setTimeout(tick, 2000);
      } catch (error) {
        setJob({ status: "failed", error: error.message });
      }
    }
    setInterval(tick, 10000);
    tick();
  </script>
</body>
</html>
"""


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def query_rows(sql):
    with psycopg.connect(**DB) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [item.name for item in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def list_local_symbols():
    daily_dir = LEAN_DATA_DIR / "equity" / "usa" / "daily"
    if not daily_dir.exists():
        return []
    return sorted(path.stem.upper() for path in daily_dir.glob("*.zip"))[:300]


def summary_payload():
    percentiles = query_rows(
        """
WITH runs AS (
  SELECT critical_path_ms::double precision AS latency_ms
  FROM lean_backtest_runs
  WHERE status = 'ok' AND critical_path_ms IS NOT NULL
), stats AS (
  SELECT COUNT(*) AS samples FROM runs
)
SELECT p.percentile, p.latency_ms, stats.samples
FROM stats,
LATERAL (
  SELECT 'p50' AS percentile, percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS latency_ms FROM runs
  UNION ALL
  SELECT 'p75' AS percentile, percentile_cont(0.75) WITHIN GROUP (ORDER BY latency_ms) AS latency_ms FROM runs
  UNION ALL
  SELECT 'p95' AS percentile, percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS latency_ms FROM runs
) p
WHERE stats.samples > 0
ORDER BY CASE p.percentile WHEN 'p50' THEN 1 WHEN 'p75' THEN 2 ELSE 3 END;
""".strip()
    )
    runs = query_rows(
        """
SELECT completed_at, run_id::text AS run_id, symbol, data_source, trace_id,
       critical_path_ms::double precision AS critical_path_ms,
       dominant_stage, reason_summary
FROM lean_backtest_runs
WHERE status = 'ok'
ORDER BY completed_at DESC
LIMIT 10;
""".strip()
    )
    return {"percentiles": percentiles, "runs": runs}


def current_job():
    with JOB_LOCK:
        return dict(JOB_STATE)


def update_job(**kwargs):
    with JOB_LOCK:
        JOB_STATE.update(kwargs)
        return dict(JOB_STATE)


def execute_run(request):
    try:
        report = run_case(request)
        update_job(
            status="ok",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=None,
            run_id=report.get("run_id"),
            trace_id=report.get("trace_id"),
            critical_path_ms=report.get("critical_path_ms"),
            reason_summary=report.get("reason_summary"),
        )
    except Exception as exc:
        logging.exception("controlled Lean observability run failed")
        update_job(status="failed", completed_at=datetime.now(timezone.utc).isoformat(), error=str(exc))


def start_run(request):
    with JOB_LOCK:
        if JOB_STATE.get("status") == "running":
            return dict(JOB_STATE)
        JOB_STATE.update(
            {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
                "error": None,
                "run_id": None,
                "trace_id": None,
                "critical_path_ms": None,
                "reason_summary": None,
            }
        )
        state = dict(JOB_STATE)
    threading.Thread(target=execute_run, args=(request,), daemon=True).start()
    return state


class Handler(BaseHTTPRequestHandler):
    def send_body(self, status, content_type, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, payload):
        self.send_body(status, "application/json; charset=utf-8", json.dumps(payload, default=json_default))

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            html = INDEX_HTML.replace("__PUBLIC_GRAFANA_URL__", PUBLIC_GRAFANA_URL)
            self.send_body(200, "text/html; charset=utf-8", html)
        elif path == "/api/health":
            self.send_json(200, {"status": "ok"})
        elif path == "/api/options":
            self.send_json(
                200,
                {
                    "dataSources": ["local", "yahoo", "stooq", "alpha_vantage"],
                    "symbols": list_local_symbols(),
                    "defaults": {
                        "symbol": "SPY",
                        "start": "2013-01-01",
                        "end": "2013-06-30",
                        "fast": 10,
                        "slow": 30,
                        "cash": 100000,
                        "dockerImage": DEFAULT_DOCKER_IMAGE,
                    },
                },
            )
        elif path == "/api/job":
            self.send_json(200, current_job())
        elif path == "/api/summary":
            self.send_json(200, summary_payload())
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/runs":
            self.send_json(202, start_run(self.read_json_body()))
        else:
            self.send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    configure_observability(os.getenv("OTEL_SERVICE_NAME", "lean-backtest-control"))
    wait_for_db()
    ensure_schema()
    wait_for_grafana(GRAFANA_URL)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logging.info("Lean observability control listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
