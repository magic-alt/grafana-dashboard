import contextlib
import logging
import os


_TRACER = None
_TRACING_READY = False
_PYROSCOPE = None
_PYROSCOPE_READY = False


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configure_observability(service_name=None):
    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "stock-pipeline")
    if _truthy(os.getenv("OBSERVABILITY_ENABLED")) or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        _configure_tracing(service_name)
    if _truthy(os.getenv("OBSERVABILITY_ENABLED")) or os.getenv("PYROSCOPE_SERVER_ADDRESS"):
        _configure_pyroscope(service_name)


def _configure_tracing(service_name):
    global _TRACER, _TRACING_READY
    if _TRACING_READY:
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    base_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint and base_endpoint:
        endpoint = base_endpoint.rstrip("/") + "/v1/traces"
    if not endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        logging.warning("OpenTelemetry is disabled: %s", exc)
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "local-stock-dashboard",
            "deployment.environment": "local",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(service_name)
    _TRACING_READY = True
    logging.info("OpenTelemetry traces enabled endpoint=%s service=%s", endpoint, service_name)


def _configure_pyroscope(service_name):
    global _PYROSCOPE, _PYROSCOPE_READY
    if _PYROSCOPE_READY:
        return

    server_address = os.getenv("PYROSCOPE_SERVER_ADDRESS")
    if not server_address:
        return

    try:
        import pyroscope
    except Exception as exc:
        logging.warning("Pyroscope profiling is disabled: %s", exc)
        return

    application_name = os.getenv("PYROSCOPE_APPLICATION_NAME", service_name.replace("-", "."))
    pyroscope.configure(
        application_name=application_name,
        server_address=server_address,
        sample_rate=int(os.getenv("PYROSCOPE_SAMPLE_RATE", "100")),
        tags={
            "otel_service": service_name,
            "component": "stock-pipeline",
        },
    )
    _PYROSCOPE = pyroscope
    _PYROSCOPE_READY = True
    logging.info("Pyroscope profiling enabled server=%s app=%s", server_address, application_name)


@contextlib.contextmanager
def span(name, attributes=None):
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


def set_span_attributes(active_span, attributes):
    if active_span is None:
        return
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        active_span.set_attribute(key, value)


@contextlib.contextmanager
def profile_tags(tags):
    if not _PYROSCOPE_READY or _PYROSCOPE is None:
        yield
        return

    with _PYROSCOPE.tag_wrapper({key: str(value) for key, value in tags.items() if value is not None}):
        yield


def trace_id_from_span(active_span):
    if active_span is None:
        return None
    context = active_span.get_span_context()
    if not context or not context.trace_id:
        return None
    return f"{context.trace_id:032x}"


def flush_traces():
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if force_flush:
            force_flush(timeout_millis=10000)
    except Exception as exc:
        logging.warning("Unable to flush OpenTelemetry traces: %s", exc)
