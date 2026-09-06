#!/usr/bin/env bash
set -euo pipefail
repo="${1:-magic-alt/grafana-dashboard}"

if ! command -v gh >/dev/null; then
  echo "gh CLI is required" >&2
  exit 2
fi

gh api --method POST "repos/${repo}/rulesets" --input .github/rulesets/main.json
