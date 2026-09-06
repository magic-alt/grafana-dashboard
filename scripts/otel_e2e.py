#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

OTLP = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318").rstrip("/")
PROMETHEUS = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
TEMPO = os.getenv("TEMPO_URL", "http://localhost:3200").rstrip("/")
LOKI = os.getenv("LOKI_URL", "http://localhost:3100").rstrip("/")
SERVICE = "telemetry-e2e-probe"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def eventually(label: str, check, timeout: float = 45.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                print(f"ok - {label}")
                return value
        except Exception as exc:
            last = exc
        time.sleep(2)
    raise RuntimeError(f"{label} did not become observable; last_error={last}")


def emit() -> str:
    resource = Resource.create(
        {
            "service.name": SERVICE,
            "service.namespace": "magic-alt",
            "service.version": "ci",
            "deployment.environment": "ci",
            "project.name": "grafana-dashboard",
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{OTLP}/v1/traces")))
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{OTLP}/v1/metrics"), export_interval_millis=500)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(OTLPLogExporter(endpoint=f"{OTLP}/v1/logs")))
    _logs.set_logger_provider(logger_provider)

    meter = metrics.get_meter("e2e")
    counter = meter.create_counter("telemetry_e2e_requests", unit="1")
    histogram = meter.create_histogram("telemetry_e2e_duration", unit="s")
    counter.add(1, {"result": "ok"})
    histogram.record(0.012, {"result": "ok"})

    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logger = logging.getLogger("telemetry-e2e")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)

    tracer = trace.get_tracer("e2e")
    with tracer.start_as_current_span("telemetry.e2e") as span:
        span.set_attribute("e2e.marker", "production-platform")
        logger.info("otel e2e marker %s", datetime.now(UTC).isoformat())
        trace_id = f"{span.get_span_context().trace_id:032x}"

    tracer_provider.force_flush()
    meter_provider.force_flush()
    logger_provider.force_flush()
    return trace_id


def main() -> None:
    trace_id = emit()
    eventually("Tempo trace", lambda: get_json(f"{TEMPO}/api/traces/{trace_id}").get("batches"))

    metric_query = urllib.parse.quote("observability_platform_telemetry_e2e_requests_total")
    eventually(
        "Prometheus metric",
        lambda: get_json(f"{PROMETHEUS}/api/v1/query?query={metric_query}").get("data", {}).get("result"),
    )

    loki_query = urllib.parse.quote(f'{{service_name="{SERVICE}"}}')
    eventually(
        "Loki log",
        lambda: get_json(f"{LOKI}/loki/api/v1/query_range?query={loki_query}&limit=20").get("data", {}).get("result"),
    )


if __name__ == "__main__":
    main()
