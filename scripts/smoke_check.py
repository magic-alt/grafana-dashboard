#!/usr/bin/env python3
import json
import sys
import urllib.error
import urllib.request


BASE_URL = "http://localhost:3000"
DATASOURCE_UID = "stock-postgres"
EXPECTED_SYMBOLS = {"AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"}


def request_json(path, method="GET", payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def query_datasource(raw_sql, fmt):
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": DATASOURCE_UID, "type": "postgres"},
                "rawSql": raw_sql,
                "format": fmt,
                "rawQuery": True,
                "intervalMs": 86400000,
                "maxDataPoints": 2000,
            }
        ],
        "from": "now-1y",
        "to": "now",
        "range": {"from": "now-1y", "to": "now", "raw": {"from": "now-1y", "to": "now"}},
    }
    return request_json("/api/ds/query", method="POST", payload=payload)


def frames_for(response):
    try:
        return response["results"]["A"]["frames"]
    except KeyError as exc:
        raise AssertionError(f"Unexpected datasource response shape: {json.dumps(response)[:1000]}") from exc


def frame_row_count(frames):
    total = 0
    for frame in frames:
        values = frame.get("data", {}).get("values", [])
        if values:
            total += len(values[0])
    return total


def assert_symbols_present(response):
    payload = json.dumps(response)
    missing = sorted(symbol for symbol in EXPECTED_SYMBOLS if symbol not in payload)
    if missing:
        raise AssertionError(f"Missing expected symbols in datasource response: {', '.join(missing)}")


def check_health():
    health = request_json("/api/health")
    if health.get("database") != "ok":
        raise AssertionError(f"Grafana health is not ok: {health}")


def check_datasource_definition():
    datasource = request_json(f"/api/datasources/uid/{DATASOURCE_UID}")
    if datasource.get("database") != "stockdash":
        raise AssertionError(f"Datasource top-level database is incorrect: {datasource.get('database')!r}")
    json_data = datasource.get("jsonData", {})
    if json_data.get("database") != "stockdash":
        raise AssertionError(f"Datasource jsonData.database is incorrect: {json_data.get('database')!r}")


def check_datasource_queries():
    close_sql = """
SELECT
  price_time AS "time",
  close::double precision AS close,
  symbol
FROM stock_prices
WHERE price_time BETWEEN NOW() - INTERVAL '1 year' AND NOW()
ORDER BY 1, symbol;
""".strip()
    close_result = query_datasource(close_sql, "time_series")
    close_rows = frame_row_count(frames_for(close_result))
    if close_rows < 100:
        raise AssertionError(f"Close price query returned too few rows: {close_rows}")
    assert_symbols_present(close_result)

    summary_sql = """
SELECT
  symbol,
  close::double precision AS latest_close
FROM stock_summary
ORDER BY symbol;
""".strip()
    summary_result = query_datasource(summary_sql, "table")
    summary_rows = frame_row_count(frames_for(summary_result))
    if summary_rows != len(EXPECTED_SYMBOLS):
        raise AssertionError(f"Summary query returned {summary_rows} rows; expected {len(EXPECTED_SYMBOLS)}")
    assert_symbols_present(summary_result)


def check_dashboard_definition():
    dashboard = None
    errors = []
    for path in (
        "/apis/dashboard.grafana.app/v0alpha1/namespaces/default/dashboards/local-stock-market",
        "/api/dashboards/uid/local-stock-market",
    ):
        try:
            dashboard = request_json(path)
            break
        except RuntimeError as exc:
            errors.append(str(exc))

    if dashboard is None:
        raise AssertionError("Unable to read provisioned dashboard: " + " | ".join(errors))

    raw = json.dumps(dashboard)
    required_fragments = [
        "selected_symbols",
        "close::double precision AS close",
        "volume::double precision AS volume",
        "daily_return_pct::double precision AS daily_return_pct",
    ]
    for fragment in required_fragments:
        if fragment not in raw:
            raise AssertionError(f"Provisioned dashboard is missing expected SQL fragment: {fragment}")
    if "symbol AS metric" in raw:
        raise AssertionError("Provisioned dashboard still contains legacy 'symbol AS metric' SQL")


def main():
    checks = [
        ("Grafana health", check_health),
        ("Datasource definition", check_datasource_definition),
        ("Datasource queries", check_datasource_queries),
        ("Dashboard definition", check_dashboard_definition),
    ]

    for label, check in checks:
        check()
        print(f"ok - {label}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"smoke check failed: {exc}", file=sys.stderr)
        sys.exit(1)
