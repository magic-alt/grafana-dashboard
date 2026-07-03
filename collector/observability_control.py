#!/usr/bin/env python3
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

from fetch_prices import DB, ensure_schema, wait_for_db
from observability_case import run_case, wait_for_grafana
from observability_support import configure_observability


HOST = os.getenv("OBS_CONTROL_HOST", "0.0.0.0")
PORT = int(os.getenv("OBS_CONTROL_PORT", "8080"))
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000")
PUBLIC_GRAFANA_URL = os.getenv("PUBLIC_GRAFANA_URL", "http://localhost:3000")
ANALYSIS_LOAD_FACTOR = int(os.getenv("OBS_ANALYSIS_LOAD_FACTOR", "12"))

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
  <title>Stock Observability Control</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #1f2937; }
    main { max-width: 1120px; margin: 0 auto; padding: 28px 20px 40px; }
    header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
    h1 { margin: 0 0 6px; font-size: 26px; line-height: 1.2; }
    p { margin: 0; color: #526071; }
    button { border: 0; border-radius: 6px; background: #0f766e; color: white; font-size: 15px; font-weight: 650; padding: 11px 16px; cursor: pointer; }
    button:disabled { background: #8aa5a1; cursor: wait; }
    a { color: #0f766e; text-decoration: none; font-weight: 650; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
    .panel { background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 16px; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }
    .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
    .value { font-size: 24px; font-weight: 750; line-height: 1.2; word-break: break-word; }
    .status { display: inline-flex; align-items: center; gap: 8px; font-weight: 700; }
    .dot { width: 9px; height: 9px; border-radius: 999px; background: #94a3b8; }
    .running .dot { background: #d97706; }
    .ok .dot { background: #16a34a; }
    .failed .dot { background: #dc2626; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
    th { color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .muted { color: #64748b; }
    @media (max-width: 760px) {
      header { display: block; }
      .actions { margin-top: 14px; }
      .grid { grid-template-columns: 1fr; }
      main { padding: 20px 14px 32px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Stock Observability Control</h1>
        <p>点击按钮会拉取真实 Yahoo Finance 数据，执行股票分析，写入 Postgres，并刷新 Grafana p50/p75 延迟分析。</p>
      </div>
      <div class="actions">
        <button id="refreshButton">Refresh Real Data</button>
        <a id="grafanaLink" href="#" target="_blank">Open Grafana Lab</a>
      </div>
    </header>

    <section class="grid">
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
      <div class="label">Recent Real Runs</div>
      <table>
        <thead><tr><th>Completed</th><th>Run</th><th>Critical Path</th><th>Dominant Stage</th><th>Reason</th></tr></thead>
        <tbody id="runs"><tr><td colspan="5" class="muted">loading</td></tr></tbody>
      </table>
    </section>
  </main>

  <script>
    const grafanaBase = "__PUBLIC_GRAFANA_URL__";
    const labUrl = `${grafanaBase}/d/stock-observability-lab/stock-observability-lab?from=now-6h&to=now`;
    document.getElementById("grafanaLink").href = labUrl;

    function text(value) { return value === null || value === undefined || value === "" ? "-" : String(value); }
    function latency(value) { return value ? `${Number(value).toFixed(1)} ms` : "-"; }
    function drilldown(percentile) {
      return `${labUrl}&var-percentile=${encodeURIComponent(percentile)}`;
    }
    function setJob(job) {
      const button = document.getElementById("refreshButton");
      const status = document.getElementById("jobStatus");
      status.className = `value status ${job.status === "running" ? "running" : job.status === "failed" ? "failed" : job.status === "ok" ? "ok" : ""}`;
      status.querySelector("span:last-child").textContent = job.status || "idle";
      button.disabled = job.status === "running";
      if (job.critical_path_ms) document.getElementById("latestLatency").textContent = latency(job.critical_path_ms);
      if (job.reason_summary) document.getElementById("latestReason").textContent = job.reason_summary;
      if (job.error) document.getElementById("latestReason").textContent = job.error;
    }
    async function getJson(url, options) {
      const response = await fetch(url, options);
      const body = await response.text();
      if (!response.ok) throw new Error(body || response.statusText);
      return JSON.parse(body);
    }
    async function refreshSummary() {
      const summary = await getJson("/api/summary");
      const percentileRows = summary.percentiles.length
        ? summary.percentiles.map((row) => `<tr><td>${row.percentile}</td><td>${latency(row.latency_ms)}</td><td>${row.samples}</td><td><a href="${drilldown(row.percentile)}" target="_blank">Open ${row.percentile}</a></td></tr>`).join("")
        : `<tr><td colspan="4" class="muted">No successful runs yet</td></tr>`;
      document.getElementById("percentiles").innerHTML = percentileRows;
      const runRows = summary.runs.length
        ? summary.runs.map((row) => `<tr><td>${text(row.completed_at)}</td><td>${text(row.run_id).slice(0, 8)}</td><td>${latency(row.critical_path_ms)}</td><td>${text(row.dominant_stage)}</td><td>${text(row.reason_summary)}</td></tr>`).join("")
        : `<tr><td colspan="5" class="muted">No successful runs yet</td></tr>`;
      document.getElementById("runs").innerHTML = runRows;
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
    document.getElementById("refreshButton").addEventListener("click", async () => {
      try {
        setJob(await getJson("/api/runs", { method: "POST" }));
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


def summary_payload():
    percentiles = query_rows(
        """
WITH runs AS (
  SELECT COALESCE(critical_path_ms, total_ms)::double precision AS latency_ms
  FROM observability_runs
  WHERE status = 'ok'
    AND COALESCE(critical_path_ms, total_ms) IS NOT NULL
),
stats AS (
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
SELECT
  completed_at,
  run_id::text AS run_id,
  trace_id,
  COALESCE(critical_path_ms, total_ms)::double precision AS critical_path_ms,
  dominant_stage,
  reason_summary
FROM observability_runs
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


def execute_refresh():
    try:
        report = run_case(
            mode="live",
            analysis_load_factor=ANALYSIS_LOAD_FACTOR,
            run_browser=False,
        )
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
        logging.exception("controlled observability refresh failed")
        update_job(
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )


def start_refresh():
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
    threading.Thread(target=execute_refresh, daemon=True).start()
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

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            html = INDEX_HTML.replace("__PUBLIC_GRAFANA_URL__", PUBLIC_GRAFANA_URL.rstrip("/"))
            self.send_body(200, "text/html; charset=utf-8", html)
        elif self.path == "/api/health":
            self.send_json(200, {"status": "ok"})
        elif self.path == "/api/job":
            self.send_json(200, current_job())
        elif self.path == "/api/summary":
            self.send_json(200, summary_payload())
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/runs":
            self.send_json(202, start_refresh())
        else:
            self.send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    configure_observability(os.getenv("OTEL_SERVICE_NAME", "stock-observability-control"))
    wait_for_db()
    ensure_schema()
    wait_for_grafana(GRAFANA_URL)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logging.info("observability control listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
