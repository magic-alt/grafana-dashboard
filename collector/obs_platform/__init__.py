"""Shared runtime primitives for the observability platform.

The stock and LEAN workloads are reference applications. New projects should depend on
this package's configuration and telemetry contract rather than copying lab-specific code.
"""

from .config import DatabaseSettings, TelemetrySettings

__all__ = ["DatabaseSettings", "TelemetrySettings"]
