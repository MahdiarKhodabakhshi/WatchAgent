#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/agent-reject.sh TASK-ID reason...\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

task_id=""
reason=""

for arg in "$@"; do
  case "$arg" in
    -*)
      usage
      fail "Unknown option: $arg"
      ;;
    *)
      if [ -z "$task_id" ]; then
        task_id="$arg"
      elif [ -z "$reason" ]; then
        reason="$arg"
      else
        reason="$reason $arg"
      fi
      ;;
  esac
done

if [ -z "$task_id" ]; then
  usage
  exit 2
fi

if [ -z "$reason" ]; then
  usage
  fail "A rejection reason is required."
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

python3 - "$task_file" "$reason" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
reason = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))

task_id = data.get("task_id", path.stem)
status = data.get("status")

if status not in {"proposed", "blocked"}:
    raise SystemExit(
        f"Task {task_id} has status {status!r}; only proposed or blocked tasks can be rejected."
    )

data["status"] = "rejected"
data["approved_by"] = None
data["status_updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
data["status_reason"] = reason

path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
PY

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Remove any pending approval request for this task so the loop stops surfacing it.
pending_file=".agent/approvals/pending/${task_id}.md"
if [ -f "$pending_file" ]; then
  rm -f "$pending_file"
fi

mkdir -p .agent/approvals/rejected
rejected_file=".agent/approvals/rejected/${task_id}.md"

{
  printf '# Rejection: %s\n\n' "$task_id"
  printf '%s\n' '- Rejected by: human'
  printf '%s\n' "- Rejected at: $timestamp"
  printf '%s\n' "- Task file: $task_file"
  printf '%s\n' "- Reason: $reason"
} > "$rejected_file"

printf 'Rejected %s\n' "$task_id"
printf 'Updated %s\n' "$task_file"
printf 'Wrote %s\n' "$rejected_file"
