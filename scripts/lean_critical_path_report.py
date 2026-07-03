#!/usr/bin/env python3
import json
import sys
from pathlib import Path


DEFAULT_REPORT = Path("test-results/lean-observability-case.json")
OUTPUT_REPORT = Path("test-results/lean-critical-path-report.md")
STAGES = [
    ("data_refresh", "Market data refresh"),
    ("config_write", "LEAN config generation"),
    ("docker_backtest", "LEAN Docker backtest"),
    ("result_parse", "Result parsing"),
    ("report_render", "HTML report rendering"),
    ("grafana_query", "Grafana datasource query"),
]


def main():
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORT
    report = json.loads(input_path.read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise SystemExit(f"Lean observability run is not ok: {report.get('status')} {report.get('error')}")
    timings = report.get("stage_timings_ms") or {}
    rows = [(label, float(timings.get(stage) or 0.0)) for stage, label in STAGES]
    total = sum(duration for _, duration in rows)
    lines = [
        "# Lean Backtest Critical Path Report",
        "",
        f"- Run id: `{report.get('run_id')}`",
        f"- Symbol: `{report.get('request', {}).get('symbol')}`",
        f"- Data source: `{report.get('request', {}).get('dataSource')}`",
        f"- Measured critical path: `{total:.3f} ms`",
        f"- Dominant stage: `{report.get('latency', {}).get('dominant_stage')}`",
        f"- Reason: {report.get('reason_summary')}",
        f"- Tempo trace: `{report.get('trace_id')}`",
        "",
        "## Critical Path",
        "",
        "| Stage | Duration ms | Share |",
        "| --- | ---: | ---: |",
    ]
    for label, duration in rows:
        share = duration / total if total else 0
        lines.append(f"| {label} | {duration:.3f} | {share:.1%} |")
    lines.extend(
        [
            "",
            "## Profile Queries",
            "",
            f"- Runner: `{report.get('profile_query')}`",
            "",
            "## Limitation",
            "",
            "LEAN engine internal .NET profiling is not collected on local arm64 Docker Desktop. "
            "The critical path records the LEAN Docker backtest stage duration instead.",
        ]
    )
    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT_REPORT)


if __name__ == "__main__":
    main()
