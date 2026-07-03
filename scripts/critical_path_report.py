#!/usr/bin/env python3
import json
import sys
from pathlib import Path


DEFAULT_REPORT = Path("test-results/observability-case.json")
OUTPUT_REPORT = Path("test-results/critical-path-report.md")
CRITICAL_STAGES = [
    ("download_prices", "Yahoo Finance download"),
    ("normalize_prices", "per-symbol normalization"),
    ("analysis", "technical indicator analysis"),
    ("store_prices", "price upsert"),
    ("store_indicators", "indicator upsert"),
    ("grafana_query", "Grafana datasource query"),
    ("browser_render", "Grafana browser render"),
]


def load_report(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing observability report: {path}") from exc


def require_data(report):
    if report.get("status") != "ok":
        raise SystemExit(f"Observability run is not ok: {report.get('status')}")
    if int(report.get("price_rows") or 0) <= 0:
        raise SystemExit("No price rows were produced")
    if int(report.get("indicator_rows") or 0) <= 0:
        raise SystemExit("No indicator rows were produced")
    timings = report.get("stage_timings_ms") or {}
    missing = [stage for stage, _ in CRITICAL_STAGES[:-1] if stage not in timings]
    if missing:
        raise SystemExit(f"Missing required critical path stage timings: {', '.join(missing)}")
    return timings


def markdown_table(rows):
    lines = ["| Stage | Duration ms | Share |", "|---|---:|---:|"]
    total = sum(duration for _, duration in rows)
    for label, duration in rows:
        share = (duration / total * 100) if total else 0
        lines.append(f"| {label} | {duration:.3f} | {share:.1f}% |")
    return "\n".join(lines)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = load_report(path)
    timings = require_data(report)

    stage_rows = []
    for key, label in CRITICAL_STAGES:
        value = timings.get(key)
        if value is not None:
            stage_rows.append((label, float(value)))
    critical_path_ms = sum(duration for _, duration in stage_rows)
    bottlenecks = sorted(stage_rows, key=lambda row: row[1], reverse=True)[:3]

    lines = [
        "# Stock Pipeline Critical Path Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Trace ID: `{report.get('trace_id')}`",
        f"- Symbols: `{', '.join(report.get('symbols', []))}`",
        f"- Price rows: `{report.get('price_rows')}`",
        f"- Indicator rows: `{report.get('indicator_rows')}`",
        f"- Measured critical path: `{critical_path_ms:.3f} ms`",
        f"- Dominant stage: `{report.get('dominant_stage')}`",
        f"- Reason: {report.get('reason_summary')}",
        "",
        "## Critical Path",
        "",
        markdown_table(stage_rows),
        "",
        "## Top Bottlenecks",
        "",
    ]
    for label, duration in bottlenecks:
        lines.append(f"- `{label}`: `{duration:.3f} ms`")

    per_symbol = report.get("per_symbol") or []
    if per_symbol:
        slowest_analysis = sorted(per_symbol, key=lambda item: float(item.get("analysis_ms") or 0), reverse=True)[:3]
        lines.extend(["", "## Slowest Symbol Analysis", ""])
        for item in slowest_analysis:
            lines.append(
                f"- `{item.get('symbol')}`: `{float(item.get('analysis_ms') or 0):.3f} ms`, "
                f"`{item.get('indicator_rows', 0)}` indicator rows"
            )

    links = report.get("links") or {}
    if links:
        lines.extend(["", "## Links", ""])
        for name, url in links.items():
            lines.append(f"- {name}: {url}")

    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nok - wrote {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
