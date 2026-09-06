from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

import psycopg
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status

from obs_platform.config import DatabaseSettings
from observability_case import run_case, wait_for_grafana
from observability_support import configure_observability

from .security import Principal, require_role

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000")
PUBLIC_GRAFANA_URL = os.getenv("PUBLIC_GRAFANA_URL", "http://localhost:3000")
ANALYSIS_LOAD_FACTOR = int(os.getenv("OBS_ANALYSIS_LOAD_FACTOR", "12"))
PORT = int(os.getenv("OBS_CONTROL_PORT", "8080"))
HOST = os.getenv("OBS_CONTROL_HOST", "0.0.0.0")

ViewerPrincipal = Annotated[Principal, Depends(require_role("viewer"))]
OperatorPrincipal = Annotated[Principal, Depends(require_role("operator"))]

app = FastAPI(
    title="Magic Alt Observability Control Plane",
    version="1.0.0",
    description=(
        "Authenticated operational API for reference workloads. Production identity can be enforced upstream "
        "by Entra/Application Gateway while preserving this API contract."
    ),
)

_JOB_LOCK = threading.Lock()
_JOB_STATE = {
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "error": None,
    "run_id": None,
    "trace_id": None,
}


def _db_kwargs() -> dict[str, object]:
    settings = DatabaseSettings.from_env()
    settings.validate(require_password=True)
    return settings.psycopg_kwargs()


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _query(sql: str) -> list[dict[str, object]]:
    with psycopg.connect(**_db_kwargs()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [item.name for item in cur.description]
        return [
            {key: _json_value(value) for key, value in zip(columns, row, strict=True)}
            for row in cur.fetchall()
        ]


def _current_job() -> dict[str, object]:
    with _JOB_LOCK:
        return dict(_JOB_STATE)


def _update_job(**values) -> None:
    with _JOB_LOCK:
        _JOB_STATE.update(values)


def _execute_refresh() -> None:
    try:
        report = run_case(mode="live", analysis_load_factor=ANALYSIS_LOAD_FACTOR, run_browser=False)
        _update_job(
            status="ok",
            completed_at=datetime.now(UTC).isoformat(),
            error=None,
            run_id=report.get("run_id"),
            trace_id=report.get("trace_id"),
            critical_path_ms=report.get("critical_path_ms"),
            reason_summary=report.get("reason_summary"),
        )
    except Exception as exc:
        logging.exception("controlled observability refresh failed")
        _update_job(status="failed", completed_at=datetime.now(UTC).isoformat(), error=str(exc))


def _start_refresh() -> dict[str, object]:
    with _JOB_LOCK:
        if _JOB_STATE["status"] == "running":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="refresh already running")
        _JOB_STATE.update(
            status="running",
            started_at=datetime.now(UTC).isoformat(),
            completed_at=None,
            error=None,
            run_id=None,
            trace_id=None,
        )
        state = dict(_JOB_STATE)
    threading.Thread(target=_execute_refresh, daemon=True, name="stock-observability-refresh").start()
    return state


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", tags=["system"])
def root(principal: ViewerPrincipal) -> dict[str, str]:
    return {
        "service": "observability-control-plane",
        "role": principal.role,
        "openapi": "/openapi.json",
        "docs": "/docs",
        "grafana": f"{PUBLIC_GRAFANA_URL.rstrip('/')}/d/stock-observability-lab/stock-observability-lab",
    }


@app.get("/api/v1/jobs/current", tags=["runs"])
def current_job(_: ViewerPrincipal) -> dict[str, object]:
    return _current_job()


@app.post("/api/v1/runs", status_code=status.HTTP_202_ACCEPTED, tags=["runs"])
def start_run(_: OperatorPrincipal) -> dict[str, object]:
    return _start_refresh()


@app.get("/api/v1/summary", tags=["runs"])
def summary(_: ViewerPrincipal) -> dict[str, object]:
    percentiles = _query(
        """
        WITH runs AS (
          SELECT COALESCE(critical_path_ms, total_ms)::double precision AS latency_ms
          FROM observability_runs
          WHERE status = 'ok' AND COALESCE(critical_path_ms, total_ms) IS NOT NULL
        ), stats AS (SELECT COUNT(*) AS samples FROM runs)
        SELECT p.percentile, p.latency_ms, stats.samples
        FROM stats, LATERAL (
          SELECT 'p50' AS percentile, percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) AS latency_ms FROM runs
          UNION ALL SELECT 'p75', percentile_cont(0.75) WITHIN GROUP (ORDER BY latency_ms) FROM runs
          UNION ALL SELECT 'p95', percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FROM runs
        ) p
        WHERE stats.samples > 0
        ORDER BY CASE p.percentile WHEN 'p50' THEN 1 WHEN 'p75' THEN 2 ELSE 3 END
        """
    )
    runs = _query(
        """
        SELECT completed_at, run_id::text AS run_id, trace_id,
               COALESCE(critical_path_ms, total_ms)::double precision AS critical_path_ms,
               dominant_stage, reason_summary
        FROM observability_runs
        WHERE status = 'ok'
        ORDER BY completed_at DESC
        LIMIT 20
        """
    )
    return {"percentiles": percentiles, "runs": runs}


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    configure_observability(os.getenv("OTEL_SERVICE_NAME", "observability-control-plane"))
    wait_for_grafana(GRAFANA_URL)
    uvicorn.run(app, host=HOST, port=PORT, access_log=True, proxy_headers=True)


if __name__ == "__main__":
    main()
