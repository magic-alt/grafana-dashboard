from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _source(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _text(env: Mapping[str, str], key: str, default: str) -> str:
    value = str(env.get(key, default)).strip()
    return value or default


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _text(env, key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _first_int(env: Mapping[str, str], primary: str, secondary: str, default: int) -> int:
    if str(env.get(primary, "")).strip():
        return _int(env, primary, default)
    return _int(env, secondary, default)


def env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str = "postgres"
    port: int = 5432
    name: str = "stockdash"
    user: str = "stock"
    password: str = ""
    sslmode: str = "disable"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseSettings:
        env = _source(environ)
        return cls(
            host=_text(env, "DB_HOST", _text(env, "POSTGRES_HOST", "postgres")),
            port=_first_int(env, "DB_PORT", "POSTGRES_PORT", 5432),
            name=_text(env, "DB_NAME", _text(env, "POSTGRES_DB", "stockdash")),
            user=_text(env, "DB_USER", _text(env, "POSTGRES_USER", "stock")),
            password=str(env.get("DB_PASSWORD", env.get("POSTGRES_PASSWORD", ""))),
            sslmode=_text(env, "DB_SSLMODE", _text(env, "POSTGRES_SSLMODE", "disable")),
        )

    def validate(self, *, require_password: bool = True) -> None:
        if not self.host:
            raise ValueError("database host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"database port out of range: {self.port}")
        if not self.name or not self.user:
            raise ValueError("database name and user must not be empty")
        if require_password and not self.password:
            raise ValueError("database password must be supplied via DB_PASSWORD or POSTGRES_PASSWORD")

    def psycopg_kwargs(self) -> dict[str, object]:
        values: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
        }
        if self.password:
            values["password"] = self.password
        if self.sslmode:
            values["sslmode"] = self.sslmode
        return values


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    service_name: str
    service_namespace: str = "magic-alt"
    deployment_environment: str = "local"
    service_version: str = "dev"
    project_name: str = "grafana-dashboard"
    otlp_endpoint: str = ""
    otlp_traces_endpoint: str = ""
    pyroscope_address: str = ""
    pyroscope_application_name: str = ""
    pyroscope_sample_rate: int = 100
    enabled: bool = False

    @classmethod
    def from_env(
        cls,
        default_service_name: str,
        environ: Mapping[str, str] | None = None,
    ) -> TelemetrySettings:
        env = _source(environ)
        service_name = _text(env, "OTEL_SERVICE_NAME", default_service_name)
        otlp_endpoint = str(env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")).strip()
        traces_endpoint = str(env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")).strip()
        pyroscope_address = str(env.get("PYROSCOPE_SERVER_ADDRESS", "")).strip()
        explicit_enabled = env_bool(env.get("OBSERVABILITY_ENABLED"), default=False)
        return cls(
            service_name=service_name,
            service_namespace=_text(env, "OBS_SERVICE_NAMESPACE", "magic-alt"),
            deployment_environment=_text(env, "DEPLOYMENT_ENVIRONMENT", "local"),
            service_version=_text(env, "SERVICE_VERSION", "dev"),
            project_name=_text(env, "OBS_PROJECT", "grafana-dashboard"),
            otlp_endpoint=otlp_endpoint,
            otlp_traces_endpoint=traces_endpoint,
            pyroscope_address=pyroscope_address,
            pyroscope_application_name=_text(
                env,
                "PYROSCOPE_APPLICATION_NAME",
                service_name.replace("-", "."),
            ),
            pyroscope_sample_rate=_int(env, "PYROSCOPE_SAMPLE_RATE", 100),
            enabled=explicit_enabled or bool(otlp_endpoint or traces_endpoint or pyroscope_address),
        )

    @property
    def traces_endpoint(self) -> str:
        if self.otlp_traces_endpoint:
            return self.otlp_traces_endpoint
        if self.otlp_endpoint:
            return self.otlp_endpoint.rstrip("/") + "/v1/traces"
        return ""

    def resource_attributes(self) -> dict[str, str]:
        return {
            "service.name": self.service_name,
            "service.namespace": self.service_namespace,
            "service.version": self.service_version,
            "deployment.environment": self.deployment_environment,
            "project.name": self.project_name,
        }
