#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

forever=0
sleep_seconds=1800
max_iterations=""
consecutive_failures=0

usage() {
  printf 'Usage: scripts/agent-loop.sh [--forever] [--sleep-seconds N] [--max-iterations N]\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_positive_integer() {
  local label="$1"
  local value="$2"
  case "$value" in
    ''|*[!0-9]*)
      fail "$label must be a positive integer"
      ;;
    0)
      fail "$label must be greater than zero"
      ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --forever)
      forever=1
      shift
      ;;
    --sleep-seconds)
      shift
      [ "$#" -gt 0 ] || fail "--sleep-seconds requires a value"
      sleep_seconds="$1"
      shift
      ;;
    --max-iterations)
      shift
      [ "$#" -gt 0 ] || fail "--max-iterations requires a value"
      max_iterations="$1"
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

require_positive_integer "--sleep-seconds" "$sleep_seconds"
if [ -n "$max_iterations" ]; then
  require_positive_integer "--max-iterations" "$max_iterations"
fi

trap 'printf "\nAgent loop stopped by user.\n"; exit 130' INT TERM

write_loop_event() {
  local event="$1"
  local task_id="${2:-none}"
  local message="${3:-}"
  local timestamp
  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p .agent/logs
  printf '%s event=%s task=%s message=%s\n' "$timestamp" "$event" "$task_id" "$message" >> .agent/logs/agent-loop-events.log
}

refuse_main_branch() {
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
  case "$branch" in
    main|master)
      fail "Refusing to run agent loop on $branch. Create or switch to a non-main branch first."
      ;;
  esac
}

latest_review_json() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
from pathlib import Path
import sys

task_id = sys.argv[1]
paths = sorted(
    Path(".agent/reviews").glob(f"REVIEW-{task_id}-*.json"),
    key=lambda path: (path.stat().st_mtime, path.name),
    reverse=True,
)
if not paths:
    raise SystemExit(1)
print(paths[0])
PY
}

review_verdict() {
  local review_file="$1"
  python3 - "$review_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"ERROR: invalid review JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)

required_strings = [
    "task_id",
    "verdict",
    "summary",
    "scope_check",
    "tests_check",
    "docs_check",
    "security_check",
]
required_arrays = ["required_fixes", "recommended_followups"]

for key in required_strings:
    if not isinstance(data.get(key), str) or not data[key].strip():
        print(f"ERROR: review JSON missing non-empty string field: {key}", file=sys.stderr)
        raise SystemExit(1)

for key in required_arrays:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        print(f"ERROR: review JSON field must be an array of strings: {key}", file=sys.stderr)
        raise SystemExit(1)

if data["verdict"] not in {"accepted", "needs_revision", "blocked"}:
    print(f"ERROR: invalid review verdict: {data['verdict']!r}", file=sys.stderr)
    raise SystemExit(1)

print(data["verdict"])
PY
}

review_summary() {
  local review_file="$1"
  python3 - "$review_file" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data.get("summary", ""))
PY
}

append_completion_note() {
  local task_id="$1"
  local review_file="$2"
  local timestamp
  local branch
  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
  {
    printf '\n## Implemented %s\n\n' "$timestamp"
    printf '%s\n' "- Task: $task_id"
    printf '%s\n' "- Branch: $branch"
    printf '%s\n' "- Review: $review_file"
    printf '%s\n' "- Note: Codex review accepted. Human PR review and merge are still required."
  } >> .agent/completed.md
}

commit_loop_checkpoint() {
  local task_id="$1"
  local verdict="$2"
  local review_file="$3"
  local review_md

  review_md="${review_file%.json}.md"
  git add ".agent/tasks/${task_id}.json" "$review_file"
  if [ -f "$review_md" ]; then
    git add "$review_md"
  fi
  if [ -f .agent/completed.md ]; then
    git add .agent/completed.md
  fi

  if git diff --cached --quiet; then
    printf 'No loop checkpoint changes to commit.\n'
    return 0
  fi

  git commit -m "Agent review ${task_id}: ${verdict}"
}

run_cycle() {
  local task_id
  local implement_exit
  local review_file
  local verdict
  local summary

  scripts/agent-env-check.sh
  refuse_main_branch

  if ! scripts/codex-planner.sh; then
    write_loop_event "planner_failed" "none" "Codex planner failed."
    scripts/agent-notify.sh loop_failed || true
    return 10
  fi

  if ! task_id="$(scripts/agent-task-state.py get-next)"; then
    scripts/agent-notify.sh approval_needed || true
    printf 'No approved tasks. Review .agent/approvals/pending/ and approve one with scripts/agent-approve.sh TASK-ID.\n'
    return 2
  fi

  printf 'Actionable task selected: %s\n' "$task_id"

  set +e
  scripts/claude-implementer.sh "$task_id"
  implement_exit="$?"
  set -e

  case "$implement_exit" in
    0)
      ;;
    20)
      write_loop_event "claude_usage_limit" "$task_id" "Claude usage or session limit detected."
      scripts/agent-notify.sh usage_limit "$task_id" || true
      return 20
      ;;
    21)
      write_loop_event "claude_auth_failed" "$task_id" "Claude authentication failure detected."
      scripts/agent-notify.sh auth_failed "$task_id" || true
      return 21
      ;;
    22)
      write_loop_event "claude_max_turns" "$task_id" "Claude stopped after reaching max turns."
      scripts/agent-notify.sh usage_limit "$task_id" || true
      return 22
      ;;
    *)
      write_loop_event "claude_failed" "$task_id" "Claude implementer exited with status $implement_exit."
      scripts/agent-notify.sh implementation_blocked "$task_id" || true
      return 10
      ;;
  esac

  if ! scripts/codex-reviewer.sh "$task_id"; then
    write_loop_event "reviewer_failed" "$task_id" "Codex reviewer failed."
    scripts/agent-notify.sh loop_failed "$task_id" || true
    return 10
  fi

  if ! review_file="$(latest_review_json "$task_id")"; then
    write_loop_event "review_missing" "$task_id" "Codex reviewer did not produce review JSON."
    scripts/agent-notify.sh loop_failed "$task_id" || true
    return 10
  fi

  if ! verdict="$(review_verdict "$review_file")"; then
    write_loop_event "review_invalid" "$task_id" "Review JSON is missing required fields or has an invalid verdict."
    scripts/agent-notify.sh loop_failed "$task_id" || true
    return 10
  fi

  summary="$(review_summary "$review_file")"
  case "$verdict" in
    accepted)
      scripts/agent-task-state.py mark-implemented "$task_id"
      append_completion_note "$task_id" "$review_file"
      scripts/agent-notify.sh review_ready "$task_id" || true
      ;;
    needs_revision)
      scripts/agent-task-state.py mark-needs-revision "$task_id" --review-file "$review_file"
      ;;
    blocked)
      scripts/agent-task-state.py mark-blocked "$task_id" --reason "$summary"
      scripts/agent-notify.sh implementation_blocked "$task_id" || true
      ;;
    *)
      write_loop_event "review_invalid" "$task_id" "Unexpected review verdict: $verdict"
      scripts/agent-notify.sh loop_failed "$task_id" || true
      return 10
      ;;
  esac

  if ! commit_loop_checkpoint "$task_id" "$verdict" "$review_file"; then
    write_loop_event "checkpoint_failed" "$task_id" "Could not commit loop checkpoint after review."
    scripts/agent-notify.sh loop_failed "$task_id" || true
    return 10
  fi

  printf 'Cycle finished for %s with review verdict: %s\n' "$task_id" "$verdict"
  return 0
}

cycle=1
while :; do
  printf 'Starting agent loop cycle %s\n' "$cycle"

  set +e
  run_cycle
  cycle_status="$?"
  set -e

  case "$cycle_status" in
    0|2)
      consecutive_failures=0
      ;;
    20|21|22)
      printf 'Agent loop stopped after Claude checkpoint condition. See .agent/handoff.md and .agent/logs/.\n'
      exit "$cycle_status"
      ;;
    *)
      consecutive_failures=$((consecutive_failures + 1))
      if [ "$consecutive_failures" -gt 3 ]; then
        write_loop_event "repeated_failures" "none" "Stopping after more than 3 consecutive failures."
        fail "Stopping after more than 3 consecutive failures."
      fi
      if [ "$forever" -ne 1 ]; then
        printf 'Agent loop stopped after failure. See .agent/logs/agent-loop-events.log.\n' >&2
        exit "$cycle_status"
      fi
      ;;
  esac

  if [ -n "$max_iterations" ] && [ "$cycle" -ge "$max_iterations" ]; then
    printf 'Agent loop reached --max-iterations %s.\n' "$max_iterations"
    break
  fi

  if [ "$forever" -ne 1 ]; then
    printf 'Agent loop completed one cycle. Use --forever to continue automatically.\n'
    break
  fi

  cycle=$((cycle + 1))
  printf 'Sleeping %s seconds before next cycle.\n' "$sleep_seconds"
  sleep "$sleep_seconds"
done
