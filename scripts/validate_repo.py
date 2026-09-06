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
    for path in sorted((ROOT / "observability").glob("*.yml")):
        load_yaml(path)
    for path in sorted((ROOT / "grafana" / "dashboards").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"invalid dashboard JSON {path.relative_to(ROOT)}: {exc}")


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

    required = [
        "POSTGRES_PASSWORD:?",
        "GRAFANA_ADMIN_PASSWORD:?",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OBS_SERVICE_NAMESPACE",
        "DEPLOYMENT_ENVIRONMENT",
    ]
    for needle in required:
        if needle not in text:
            fail(f"deployment config is missing required contract fragment {needle!r}")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if "change-me-before-running" not in env_example:
        fail(".env.example must use obvious non-production secret placeholders")


def validate_otel_contract() -> None:
    payload = load_yaml(ROOT / "observability" / "otel-collector.yml") or {}
    pipelines = payload.get("service", {}).get("pipelines", {})
    missing = {"traces", "metrics", "logs"} - set(pipelines)
    if missing:
        fail(f"OTel collector is missing pipelines: {', '.join(sorted(missing))}")

    exporters = payload.get("exporters", {})
    for exporter in ("otlp/tempo", "prometheus", "otlphttp/loki"):
        if exporter not in exporters:
            fail(f"OTel collector is missing exporter {exporter!r}")


def validate_delivery_files() -> None:
    required = [
        "README.md",
        "pyproject.toml",
        "Makefile",
        ".github/workflows/ci.yml",
        "deploy/azure/main.bicep",
        "docs/architecture.md",
        "docs/integrations.md",
        "docs/azure.md",
    ]
    for relative in required:
        if not (ROOT / relative).exists():
            fail(f"missing product delivery file {relative}")


def main() -> int:
    validate_yaml_and_json()
    validate_runtime_contract()
    validate_otel_contract()
    validate_delivery_files()

    if ERRORS:
        for error in ERRORS:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"repository validation failed with {len(ERRORS)} error(s)", file=sys.stderr)
        return 1

    print("repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
