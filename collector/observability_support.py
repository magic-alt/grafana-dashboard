"""Backward-compatible imports for the original lab scripts.

New applications should import from :mod:`obs_platform.telemetry` directly. Keeping this
module avoids a flag-day rewrite of the stock and LEAN reference workloads while the
repository migrates to the shared platform runtime.
"""

from obs_platform.telemetry import (
    configure_observability,
    current_settings,
    flush_traces,
    profile_tags,
    set_span_attributes,
    span,
    trace_id_from_span,
)

__all__ = [
    "configure_observability",
    "current_settings",
    "flush_traces",
    "profile_tags",
    "set_span_attributes",
    "span",
    "trace_id_from_span",
]
