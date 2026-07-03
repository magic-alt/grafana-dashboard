#!/usr/bin/env python3
import argparse
import json
import time
import urllib.error
import urllib.request


DEFAULT_CONTROL_URL = "http://localhost:18081"


def request_json(url, method="GET", payload=None, timeout=30):
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {body_text}") from exc


def wait_for_completion(base_url, timeout_seconds):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        job = request_json(f"{base_url}/api/job")
        if job.get("status") == "ok":
            if not job.get("critical_path_ms"):
                raise AssertionError(f"Completed Lean job has no critical_path_ms: {job}")
            if not job.get("reason_summary"):
                raise AssertionError(f"Completed Lean job has no reason_summary: {job}")
            return job
        if job.get("status") == "failed":
            raise AssertionError(f"Lean job failed: {job.get('error')}")
        time.sleep(3)
    raise TimeoutError("Timed out waiting for Lean observability job")


def main():
    parser = argparse.ArgumentParser(description="Smoke test the Lean observability control page.")
    parser.add_argument("--url", default=DEFAULT_CONTROL_URL)
    parser.add_argument("--run", action="store_true", help="Start a local SPY Lean backtest and wait for completion.")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    health = request_json(f"{base_url}/api/health")
    if health.get("status") != "ok":
        raise AssertionError(f"Unexpected health response: {health}")
    options = request_json(f"{base_url}/api/options")
    if "local" not in options.get("dataSources", []):
        raise AssertionError(f"Control options are missing local data source: {options}")
    summary = request_json(f"{base_url}/api/summary")
    if "runs" not in summary or "percentiles" not in summary:
        raise AssertionError(f"Unexpected summary response: {summary}")
    print("ok - Lean observability control health/options/summary endpoints work")

    if args.run:
        request_json(
            f"{base_url}/api/runs",
            method="POST",
            payload={
                "dataSource": "local",
                "symbol": "SPY",
                "start": "2013-01-01",
                "end": "2013-06-30",
                "fast": 10,
                "slow": 30,
                "cash": 100000,
                "overwriteData": False,
            },
        )
        job = wait_for_completion(base_url, args.timeout)
        print(f"ok - Lean observability run completed run_id={job.get('run_id')} critical_path_ms={job.get('critical_path_ms')}")


if __name__ == "__main__":
    main()
