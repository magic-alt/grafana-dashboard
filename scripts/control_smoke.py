#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


BASE_URL = "http://localhost:18080"


def request(path, method="GET"):
    req = urllib.request.Request(f"{BASE_URL}{path}", method=method, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(body)
            return body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def check_page():
    health = request("/api/health")
    if health.get("status") != "ok":
        raise AssertionError(f"Unexpected control health response: {health}")
    page = request("/")
    for text in ("Stock Observability Control", "Refresh Real Data", "Latency Percentiles"):
        if text not in page:
            raise AssertionError(f"Control page missing expected text: {text}")
    summary = request("/api/summary")
    if "percentiles" not in summary or "runs" not in summary:
        raise AssertionError(f"Unexpected summary shape: {summary}")
    print("ok - control page and summary API are reachable")


def run_refresh():
    job = request("/api/runs", method="POST")
    if job.get("status") not in {"running", "ok"}:
        raise AssertionError(f"Unexpected initial job state: {job}")
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        job = request("/api/job")
        if job.get("status") == "ok":
            if not job.get("run_id"):
                raise AssertionError(f"Completed job has no run_id: {job}")
            if not job.get("critical_path_ms"):
                raise AssertionError(f"Completed job has no critical_path_ms: {job}")
            if not job.get("reason_summary"):
                raise AssertionError(f"Completed job has no reason_summary: {job}")
            print(f"ok - control refresh completed run_id={job['run_id']}")
            return
        if job.get("status") == "failed":
            raise AssertionError(f"Control refresh failed: {job.get('error')}")
        time.sleep(3)
    raise AssertionError("Control refresh did not complete before timeout")


def main():
    parser = argparse.ArgumentParser(description="Smoke test the observability control page.")
    parser.add_argument("--run-refresh", action="store_true", help="Click the refresh API and wait for a real data run.")
    args = parser.parse_args()
    check_page()
    if args.run_refresh:
        run_refresh()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"control smoke failed: {exc}", file=sys.stderr)
        sys.exit(1)
