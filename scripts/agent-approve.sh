#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/agent-approve.sh [--allow-high-risk] TASK-ID\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

allow_high_risk=0
task_id=""

for arg in "$@"; do
  case "$arg" in
    --allow-high-risk)
      allow_high_risk=1
      ;;
    -*)
      usage
      fail "Unknown option: $arg"
      ;;
    *)
      if [ -n "$task_id" ]; then
        usage
        fail "Only one TASK-ID may be provided."
      fi
      task_id="$arg"
      ;;
  esac
done

if [ -z "$task_id" ]; then
  usage
  exit 2
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required to update task JSON files."

task_file="$(python3 - "$task_id" <<'PY'
import json
import pathlib
import sys

requested = sys.argv[1]
for path in pathlib.Path(".agent/tasks").glob("*.json"):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if data.get("task_id") == requested:
        print(path)
        sys.exit(0)
sys.exit(1)
PY
)" || fail "Task not found: $task_id"

python3 - "$task_file" "$allow_high_risk" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
allow_high_risk = sys.argv[2] == "1"
data = json.loads(path.read_text(encoding="utf-8"))

task_id = data.get("task_id", path.stem)
status = data.get("status")
risk = data.get("risk")

if status != "proposed":
    raise SystemExit(f"Task {task_id} has status {status!r}; only proposed tasks can be approved.")

if risk == "high" and not allow_high_risk:
    raise SystemExit(f"Task {task_id} is high risk. Re-run with --allow-high-risk after explicit human approval.")

data["status"] = "approved"
data["approved_by"] = "human"

path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
PY

mkdir -p .agent/approvals/approved
approval_file=".agent/approvals/approved/${task_id}.md"
timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

{
  printf '# Approval: %s\n\n' "$task_id"
  printf '%s\n' '- Approved by: human'
  printf '%s\n' "- Approved at: $timestamp"
  printf '%s\n' "- Task file: $task_file"
} > "$approval_file"

printf 'Approved %s\n' "$task_id"
printf 'Updated %s\n' "$task_file"
printf 'Wrote %s\n' "$approval_file"
