from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Mapping
from typing import Any

from .config import TelemetrySettings


_LOG = logging.getLogger(__name__)
_TRACER: Any = None
_TRACING_READY = False
_PYROSCOPE: Any = None
_PYROSCOPE_READY = False
_SETTINGS: TelemetrySettings | None = None


def configure_observability(service_name: str | None = None) -> TelemetrySettings:
    """Configure tracing/profiling once and return the resolved telemetry contract.

    Applications may call this repeatedly. Configuration is idempotent inside one process,
    which is important for workers and CLI entrypoints that share helper modules.
    """

    global _SETTINGS
    settings = TelemetrySettings.from_env(service_name or "observability-workload")
    _SETTINGS = settings
    if not settings.enabled:
        return settings

    if settings.traces_endpoint:
        _configure_tracing(settings)
    if settings.pyroscope_address:
        _configure_pyroscope(settings)
    return settings


def _configure_tracing(settings: TelemetrySettings) -> None:
    global _TRACER, _TRACING_READY
    if _TRACING_READY:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # pragma: no cover - defensive optional dependency path
        _LOG.warning("OpenTelemetry tracing is disabled: %s", exc)
        return

    provider = TracerProvider(resource=Resource.create(settings.resource_attributes()))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.traces_endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(settings.service_name, settings.service_version)
    _TRACING_READY = True
    _LOG.info(
        "OpenTelemetry traces enabled endpoint=%s service=%s namespace=%s environment=%s",
        settings.traces_endpoint,
        settings.service_name,
        settings.service_namespace,
        settings.deployment_environment,
    )


def _configure_pyroscope(settings: TelemetrySettings) -> None:
    global _PYROSCOPE, _PYROSCOPE_READY
    if _PYROSCOPE_READY:
        return

    try:
        import pyroscope
    except Exception as exc:  # pragma: no cover - defensive optional dependency path
        _LOG.warning("Pyroscope profiling is disabled: %s", exc)
        return

    pyroscope.configure(
        application_name=settings.pyroscope_application_name,
        server_address=settings.pyroscope_address,
        sample_rate=settings.pyroscope_sample_rate,
        tags={
            "service_name": settings.service_name,
            "service_namespace": settings.service_namespace,
            "deployment_environment": settings.deployment_environment,
            "project": settings.project_name,
        },
    )
    _PYROSCOPE = pyroscope
    _PYROSCOPE_READY = True
    _LOG.info(
        "Pyroscope profiling enabled server=%s app=%s",
        settings.pyroscope_address,
        settings.pyroscope_application_name,
    )


@contextlib.contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
    if _TRACER is None:
        yield None
        return

    from opentelemetry.trace import Status, StatusCode

    with _TRACER.start_as_current_span(name) as active_span:
        set_span_attributes(active_span, attributes or {})
        try:
            yield active_span
        except Exception as exc:
            active_span.record_exception(exc)
            active_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def set_span_attributes(active_span: Any, attributes: Mapping[str, Any] | None) -> None:
    if active_span is None:
        return
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            active_span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)) and all(isinstance(item, (str, bool, int, float)) for item in value):
            active_span.set_attribute(key, value)
        else:
            active_span.set_attribute(key, str(value))


@contextlib.contextmanager
def profile_tags(tags: Mapping[str, Any]) -> Iterator[None]:
    if not _PYROSCOPE_READY or _PYROSCOPE is None:
        yield
        return

    normalized = {key: str(value) for key, value in tags.items() if value is not None}
    with _PYROSCOPE.tag_wrapper(normalized):
        yield


def trace_id_from_span(active_span: Any) -> str | None:
    if active_span is None:
        return None
    context = active_span.get_span_context()
    if not context or not context.trace_id:
        return None
    return f"{context.trace_id:032x}"


def flush_traces(timeout_millis: int = 10_000) -> None:
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if force_flush:
            force_flush(timeout_millis=timeout_millis)
    except Exception as exc:  # pragma: no cover - shutdown path
        _LOG.warning("Unable to flush OpenTelemetry traces: %s", exc)


def current_settings() -> TelemetrySettings | None:
    return _SETTINGS
