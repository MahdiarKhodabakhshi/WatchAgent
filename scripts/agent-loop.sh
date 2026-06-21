#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

forever=0
sleep_seconds=300

usage() {
  printf 'Usage: scripts/agent-loop.sh [--forever] [--sleep SECONDS]\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --forever)
      forever=1
      shift
      ;;
    --sleep)
      shift
      [ "$#" -gt 0 ] || fail "--sleep requires a value"
      sleep_seconds="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "Unknown argument: $1"
      ;;
  esac
done

case "$sleep_seconds" in
  ''|*[!0-9]*)
    fail "--sleep must be a positive integer"
    ;;
esac

trap 'printf "\nAgent loop stopped by user.\n"; exit 130' INT TERM

command -v python3 >/dev/null 2>&1 || fail "python3 is required to inspect task JSON files."

oldest_approved_task_id() {
  python3 - <<'PY'
import json
import pathlib
import sys

tasks = []
for path in pathlib.Path(".agent/tasks").glob("*.json"):
    if path.name == "TASK-TEMPLATE.json":
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if data.get("status") == "approved":
        tasks.append((data.get("created_at") or "", path.stat().st_mtime, data.get("task_id") or ""))

if not tasks:
    sys.exit(1)

tasks.sort()
print(tasks[0][2])
PY
}

cycle=1
while :; do
  printf 'Starting agent loop cycle %s\n' "$cycle"
  scripts/agent-env-check.sh

  scripts/codex-planner.sh

  if task_id="$(oldest_approved_task_id)"; then
    printf 'Approved task found: %s\n' "$task_id"
    scripts/claude-implementer.sh
    scripts/codex-reviewer.sh "$task_id"
  else
    printf 'approval needed\n'
    exit 0
  fi

  if [ "$forever" -ne 1 ]; then
    printf 'Agent loop completed one cycle. Use --forever to continue automatically.\n'
    break
  fi

  cycle=$((cycle + 1))
  printf 'Sleeping %s seconds before next cycle.\n' "$sleep_seconds"
  sleep "$sleep_seconds"
done
