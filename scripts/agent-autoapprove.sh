#!/usr/bin/env bash
set -euo pipefail

# Opt-in: auto-approve proposed tasks up to a risk ceiling so the autonomous
# loop does not stall on the approval gate. High-risk tasks are NEVER
# auto-approved; they always require an explicit human
# `scripts/agent-approve.sh --allow-high-risk TASK-ID`.
#
# Usage:
#   scripts/agent-autoapprove.sh [--max-risk low|medium]
#
# Default ceiling is "low".

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

max_risk="low"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-risk)
      shift
      [ "$#" -gt 0 ] || fail "--max-risk requires a value (low|medium)"
      max_risk="$1"
      shift
      ;;
    -h|--help)
      printf 'Usage: scripts/agent-autoapprove.sh [--max-risk low|medium]\n'
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

case "$max_risk" in
  low|medium) ;;
  *) fail "--max-risk must be low or medium (high is never auto-approved)." ;;
esac

command -v python3 >/dev/null 2>&1 || fail "python3 is required."

# Emit "TASK-ID risk" lines for every proposed task.
mapfile -t proposed < <(python3 - <<'PY'
import json
from pathlib import Path

for path in sorted(Path(".agent/tasks").glob("*.json")):
    if path.name == "TASK-TEMPLATE.json" or path.is_symlink():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(data, dict):
        continue
    if data.get("status") != "proposed":
        continue
    task_id = data.get("task_id")
    risk = data.get("risk")
    if isinstance(task_id, str) and isinstance(risk, str):
        print(f"{task_id} {risk}")
PY
)

declare -A rank=( [low]=0 [medium]=1 [high]=2 )
ceiling="${rank[$max_risk]}"

approved_count=0
skipped_count=0

if [ "${#proposed[@]}" -eq 0 ]; then
  printf 'No proposed tasks to auto-approve.\n'
  exit 0
fi

for line in "${proposed[@]}"; do
  task_id="${line%% *}"
  risk="${line##* }"
  task_rank="${rank[$risk]:-2}"
  if [ "$task_rank" -le "$ceiling" ]; then
    if scripts/agent-approve.sh "$task_id" >/dev/null 2>&1; then
      printf 'auto-approved %s (risk=%s)\n' "$task_id" "$risk"
      approved_count=$(( approved_count + 1 ))
    else
      printf 'skip %s: approval helper failed\n' "$task_id"
      skipped_count=$(( skipped_count + 1 ))
    fi
  else
    printf 'skip %s (risk=%s above ceiling=%s; needs human approval)\n' "$task_id" "$risk" "$max_risk"
    skipped_count=$(( skipped_count + 1 ))
  fi
done

printf 'Auto-approve done: %s approved, %s skipped.\n' "$approved_count" "$skipped_count"
