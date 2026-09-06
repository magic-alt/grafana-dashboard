#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid YAML {path.relative_to(ROOT)}: {exc}")
        return None


def validate_yaml_and_json() -> None:
    for path in sorted((ROOT / "grafana" / "provisioning").rglob("*.yml")):
        load_yaml(path)
    for path in sorted((ROOT / "observability").rglob("*.yml")):
        load_yaml(path)
    for path in sorted((ROOT / "observability").rglob("*.yaml")):
        load_yaml(path)
    for path in sorted((ROOT / "grafana" / "dashboards").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"invalid dashboard JSON {path.relative_to(ROOT)}: {exc}")
    ruleset = ROOT / ".github" / "rulesets" / "main.json"
    if ruleset.exists():
        try:
            json.loads(ruleset.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"invalid GitHub ruleset JSON: {exc}")


def validate_runtime_contract() -> None:
    compose_paths = [ROOT / "docker-compose.yml", ROOT / "docker-compose.observability.yml"]
    text = "\n".join(path.read_text(encoding="utf-8") for path in compose_paths)
    forbidden = {
        "/Users/kaermax": "developer-specific absolute path",
        "container_name:": "global container_name prevents parallel stacks and scaling",
        "GF_AUTH_ANONYMOUS_ENABLED: \"true\"": "anonymous Grafana must not be the default",
    }
    for needle, reason in forbidden.items():
        if needle in text:
            fail(f"deployment config contains {reason}: {needle!r}")
    for needle in ["POSTGRES_PASSWORD:?", "GRAFANA_ADMIN_PASSWORD:?", "OTEL_EXPORTER_OTLP_ENDPOINT", "OBS_SERVICE_NAMESPACE", "DEPLOYMENT_ENVIRONMENT", "migrate:"]:
        if needle not in text:
            fail(f"deployment config is missing required contract fragment {needle!r}")


def validate_observability_contract() -> None:
    otel = load_yaml(ROOT / "observability" / "otel-collector.yml") or {}
    pipelines = otel.get("service", {}).get("pipelines", {})
    missing = {"traces", "metrics", "logs"} - set(pipelines)
    if missing:
        fail(f"OTel collector is missing pipelines: {', '.join(sorted(missing))}")
    processors = otel.get("processors", {})
    if "tail_sampling" not in processors:
        fail("OTel collector must define tail_sampling")
    for exporter in ("otlp/tempo", "prometheus", "otlphttp/loki"):
        if exporter not in otel.get("exporters", {}):
            fail(f"OTel collector is missing exporter {exporter!r}")

    prometheus = load_yaml(ROOT / "observability" / "prometheus.yml") or {}
    if not prometheus.get("rule_files"):
        fail("Prometheus must load recording/SLO/alert rules")

    policy = load_yaml(ROOT / "observability" / "telemetry-policy.yaml") or {}
    if policy.get("kind") != "TelemetryPolicy":
        fail("telemetry policy is missing or invalid")
    catalog = load_yaml(ROOT / "observability" / "slo" / "platform.yaml") or {}
    if catalog.get("kind") != "SLOCatalog":
        fail("SLO catalog is missing or invalid")


def validate_architecture_contract() -> None:
    required = [
        "collector/alembic.ini",
        "collector/migrations/versions/0001_initial_platform_schema.py",
        "collector/control_plane/app.py",
        "collector/control_plane/security.py",
        "grafana/dashboards/platform-fleet-overview.json",
        "observability/prometheus/rules/recording.yml",
        "observability/prometheus/rules/slo.yml",
        "observability/prometheus/rules/alerts.yml",
        "observability/telemetry-policy.yaml",
        "scripts/otel_e2e.py",
        ".github/workflows/codeql.yml",
        ".github/workflows/release.yml",
        ".github/workflows/azure-deploy.yml",
        ".github/rulesets/main.json",
        "docs/release.md",
    ]
    for relative in required:
        if not (ROOT / relative).exists():
            fail(f"missing production delivery file {relative}")

    control = (ROOT / "collector" / "observability_control.py").read_text(encoding="utf-8")
    if "BaseHTTPRequestHandler" in control or "ThreadingHTTPServer" in control:
        fail("legacy unauthenticated HTTP control server must not be used")

    main_compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    if "db/init.sql" in main_compose:
        fail("database bootstrap must go through Alembic, not docker-entrypoint init.sql")


def main() -> int:
    validate_yaml_and_json()
    validate_runtime_contract()
    validate_observability_contract()
    validate_architecture_contract()
    if ERRORS:
        for error in ERRORS:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"repository validation failed with {len(ERRORS)} error(s)", file=sys.stderr)
        return 1
    print("repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
