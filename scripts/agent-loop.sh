#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

forever=0
sleep_seconds=1800
max_iterations=""
max_revisions_per_task=3
skip_planner=0
consecutive_failures=0

usage() {
  printf 'Usage: scripts/agent-loop.sh [--once] [--forever] [--skip-planner] [--sleep-seconds N] [--max-iterations N] [--max-revisions-per-task N]\n' >&2
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
    --once)
      forever=0
      shift
      ;;
    --forever)
      forever=1
      shift
      ;;
    --skip-planner)
      skip_planner=1
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
    --max-revisions-per-task)
      shift
      [ "$#" -gt 0 ] || fail "--max-revisions-per-task requires a value"
      max_revisions_per_task="$1"
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
require_positive_integer "--max-revisions-per-task" "$max_revisions_per_task"
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

task_status() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
import json
import re
import sys
from pathlib import Path

task_id = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    raise SystemExit(1)
path = Path(".agent/tasks") / f"{task_id}.json"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
status = data.get("status")
if not isinstance(status, str) or not status:
    raise SystemExit(1)
print(status)
PY
}

latest_agent_log() {
  local prefix="$1"
  python3 - "$prefix" <<'PY'
from pathlib import Path
import sys

prefix = sys.argv[1]
paths = sorted(
    Path(".agent/logs").glob(f"{prefix}-*.log"),
    key=lambda path: (path.stat().st_mtime_ns, path.name),
    reverse=True,
)
if not paths:
    raise SystemExit(1)
print(paths[0])
PY
}

classify_failure_log() {
  local log_path="$1"
  if grep -Eiq 'auth|oauth|login|not authenticated|not logged in|unauthorized|forbidden' "$log_path"; then
    printf 'auth_failed\n'
  elif grep -Eiq 'usage limit|session limit|rate limit|quota|too many requests|exceeded|overloaded' "$log_path"; then
    printf 'usage_limit\n'
  else
    printf 'failed\n'
  fi
}

classify_latest_failure() {
  local prefix="$1"
  local log_path
  if log_path="$(latest_agent_log "$prefix")"; then
    classify_failure_log "$log_path"
  else
    printf 'failed\n'
  fi
}

latest_review_json() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
from pathlib import Path
import sys

task_id = sys.argv[1]
paths = sorted(
    Path(".agent/reviews").glob(f"REVIEW-{task_id}-*.json"),
    key=lambda path: (path.stat().st_mtime_ns, path.name),
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

selected_task_id=""

select_actionable_task() {
  selected_task_id=""
  if selected_task_id="$(scripts/agent-task-state.py get-next)"; then
    write_loop_event "task_selected" "$selected_task_id" "Selected existing actionable task."
    return 0
  fi

  if [ "$skip_planner" -eq 1 ]; then
    write_loop_event "no_actionable_skip_planner" "none" "No actionable task exists and planner was skipped."
    printf 'No actionable task found and --skip-planner was passed.\n'
    return 2
  fi

  if ! scripts/codex-planner.sh; then
    local failure_kind
    failure_kind="$(classify_latest_failure "codex-planner")"
    case "$failure_kind" in
      usage_limit)
        write_loop_event "codex_usage_limit" "none" "Codex planner reported a usage or session limit."
        scripts/agent-notify.sh usage_limit || true
        return 20
        ;;
      auth_failed)
        write_loop_event "codex_auth_failed" "none" "Codex planner reported an authentication failure."
        scripts/agent-notify.sh auth_failed || true
        return 21
        ;;
      *)
        write_loop_event "planner_failed_no_actionable" "none" "Codex planner failed and no actionable task exists."
        scripts/agent-notify.sh loop_failed || true
        return 10
        ;;
    esac
  fi

  if selected_task_id="$(scripts/agent-task-state.py get-next)"; then
    write_loop_event "task_selected_after_planner" "$selected_task_id" "Planner produced or preserved actionable work."
    return 0
  fi

  scripts/agent-notify.sh approval_needed || true
  write_loop_event "approval_needed" "none" "No actionable task exists after planner."
  printf 'No approved tasks. Review .agent/approvals/pending/ and approve one with scripts/agent-approve.sh TASK-ID.\n'
  return 2
}

run_claude_pass() {
  local task_id="$1"
  local implement_exit

  set +e
  scripts/claude-implementer.sh "$task_id"
  implement_exit="$?"

  case "$implement_exit" in
    0)
      return 0
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
}

run_review_pass() {
  local task_id="$1"
  local review_exit

  set +e
  scripts/codex-reviewer.sh "$task_id"
  review_exit="$?"

  case "$review_exit" in
    0)
      return 0
      ;;
    20)
      write_loop_event "codex_usage_limit" "$task_id" "Codex reviewer reported a usage or session limit."
      scripts/agent-notify.sh usage_limit "$task_id" || true
      return 20
      ;;
    21)
      write_loop_event "codex_auth_failed" "$task_id" "Codex reviewer reported an authentication failure."
      scripts/agent-notify.sh auth_failed "$task_id" || true
      return 21
      ;;
    *)
      write_loop_event "reviewer_failed" "$task_id" "Codex reviewer exited with status $review_exit."
      scripts/agent-notify.sh loop_failed "$task_id" || true
      return 10
      ;;
  esac
}

drive_task_to_terminal_review() {
  local task_id="$1"
  local current_status
  local review_file
  local verdict
  local summary
  local revision_attempts=0
  local pass_status

  printf 'Actionable task selected: %s\n' "$task_id"

  while :; do
    if ! current_status="$(task_status "$task_id")"; then
      write_loop_event "task_status_failed" "$task_id" "Could not read task status."
      scripts/agent-notify.sh loop_failed "$task_id" || true
      set +e
      return 10
    fi

    case "$current_status" in
      approved)
        scripts/agent-task-state.py mark-in-progress "$task_id"
        ;;
      in_progress)
        ;;
      needs_revision)
        revision_attempts=$((revision_attempts + 1))
        if [ "$revision_attempts" -gt "$max_revisions_per_task" ]; then
          scripts/agent-task-state.py mark-blocked "$task_id" --reason "Max revisions reached (${max_revisions_per_task}) before another Claude revision pass."
          write_loop_event "max_revisions_reached" "$task_id" "Max revisions reached before another Claude revision pass."
          scripts/agent-notify.sh implementation_blocked "$task_id" || true
          scripts/agent-notify.sh max_revisions_reached "$task_id" || true
          return 0
        fi
        write_loop_event "revision_attempt" "$task_id" "Starting revision attempt ${revision_attempts} of ${max_revisions_per_task}."
        ;;
      *)
        write_loop_event "task_not_actionable" "$task_id" "Task status is $current_status."
        scripts/agent-notify.sh loop_failed "$task_id" || true
        set +e
        return 10
        ;;
    esac

    set +e
    run_claude_pass "$task_id"
    pass_status="$?"
    set -e
    if [ "$pass_status" -ne 0 ]; then
      set +e
      return "$pass_status"
    fi

    set +e
    run_review_pass "$task_id"
    pass_status="$?"
    set -e
    if [ "$pass_status" -ne 0 ]; then
      set +e
      return "$pass_status"
    fi

    if ! review_file="$(latest_review_json "$task_id")"; then
      write_loop_event "review_missing" "$task_id" "Codex reviewer did not produce review JSON."
      scripts/agent-notify.sh loop_failed "$task_id" || true
      set +e
      return 10
    fi

    if ! verdict="$(review_verdict "$review_file")"; then
      write_loop_event "review_invalid" "$task_id" "Review JSON is missing required fields or has an invalid verdict."
      scripts/agent-notify.sh loop_failed "$task_id" || true
      set +e
      return 10
    fi

    summary="$(review_summary "$review_file")"
    case "$verdict" in
      accepted)
        scripts/agent-task-state.py mark-implemented "$task_id"
        append_completion_note "$task_id" "$review_file"
        if ! commit_loop_checkpoint "$task_id" "$verdict" "$review_file"; then
          write_loop_event "checkpoint_failed" "$task_id" "Could not commit loop checkpoint after accepted review."
          scripts/agent-notify.sh loop_failed "$task_id" || true
          set +e
          return 10
        fi
        scripts/agent-notify.sh review_ready "$task_id" || true
        write_loop_event "review_accepted" "$task_id" "Task implemented; human PR review and merge are still required."
        printf 'Task %s accepted by Codex review. Human PR review and merge are still required.\n' "$task_id"
        return 0
        ;;
      needs_revision)
        scripts/agent-task-state.py mark-needs-revision "$task_id" --review-file "$review_file"
        if [ "$revision_attempts" -ge "$max_revisions_per_task" ]; then
          scripts/agent-task-state.py mark-blocked "$task_id" --reason "Max revisions reached (${max_revisions_per_task}); latest Codex review still requires fixes."
          if ! commit_loop_checkpoint "$task_id" "max_revisions_reached" "$review_file"; then
            write_loop_event "checkpoint_failed" "$task_id" "Could not commit loop checkpoint after max revisions reached."
            scripts/agent-notify.sh loop_failed "$task_id" || true
            set +e
            return 10
          fi
          write_loop_event "max_revisions_reached" "$task_id" "Latest review still needs revision after ${max_revisions_per_task} revision attempt(s)."
          scripts/agent-notify.sh implementation_blocked "$task_id" || true
          scripts/agent-notify.sh max_revisions_reached "$task_id" || true
          printf 'Task %s blocked after reaching --max-revisions-per-task %s.\n' "$task_id" "$max_revisions_per_task"
          return 0
        fi
        if ! commit_loop_checkpoint "$task_id" "$verdict" "$review_file"; then
          write_loop_event "checkpoint_failed" "$task_id" "Could not commit loop checkpoint after needs_revision review."
          scripts/agent-notify.sh loop_failed "$task_id" || true
          set +e
          return 10
        fi
        write_loop_event "review_needs_revision" "$task_id" "Continuing bounded revision loop."
        printf 'Codex review requested fixes for %s. Continuing revision loop (%s/%s used).\n' "$task_id" "$revision_attempts" "$max_revisions_per_task"
        ;;
      blocked)
        scripts/agent-task-state.py mark-blocked "$task_id" --reason "$summary"
        if ! commit_loop_checkpoint "$task_id" "$verdict" "$review_file"; then
          write_loop_event "checkpoint_failed" "$task_id" "Could not commit loop checkpoint after blocked review."
          scripts/agent-notify.sh loop_failed "$task_id" || true
          set +e
          return 10
        fi
        scripts/agent-notify.sh implementation_blocked "$task_id" || true
        write_loop_event "review_blocked" "$task_id" "Codex review blocked the task."
        printf 'Task %s blocked by Codex review.\n' "$task_id"
        return 0
        ;;
      *)
        write_loop_event "review_invalid" "$task_id" "Unexpected review verdict: $verdict"
        scripts/agent-notify.sh loop_failed "$task_id" || true
        set +e
        return 10
        ;;
    esac
  done
}

run_cycle() {
  local selection_status
  local task_id
  local task_status_code

  set -e
  scripts/agent-env-check.sh
  refuse_main_branch

  set +e
  select_actionable_task
  selection_status="$?"

  case "$selection_status" in
    0)
      task_id="$selected_task_id"
      ;;
    2|10|20|21)
      return "$selection_status"
      ;;
    *)
      write_loop_event "selection_failed" "none" "Unexpected task selection status $selection_status."
      scripts/agent-notify.sh loop_failed || true
      return 10
      ;;
  esac

  set +e
  drive_task_to_terminal_review "$task_id"
  task_status_code="$?"
  set +e
  return "$task_status_code"
}

cycle=1
while :; do
  printf 'Starting agent loop cycle %s\n' "$cycle"

  set +e
  run_cycle
  cycle_status="$?"
  set -e

  case "$cycle_status" in
    0|2|20|21|22)
      consecutive_failures=0
      ;;
    *)
      consecutive_failures=$((consecutive_failures + 1))
      if [ "$consecutive_failures" -ge 3 ]; then
        write_loop_event "repeated_failures" "none" "Stopping after 3 consecutive loop failures."
        fail "Stopping after 3 consecutive loop failures."
      fi
      if [ "$forever" -ne 1 ]; then
        printf 'Agent loop stopped after failure. See .agent/logs/agent-loop-events.log.\n' >&2
        exit "$cycle_status"
      fi
      ;;
  esac

  if [ "$forever" -ne 1 ]; then
    case "$cycle_status" in
      20|21|22)
        printf 'Agent loop stopped after auth, usage, or max-turn condition. See .agent/handoff.md and .agent/logs/.\n'
        exit "$cycle_status"
        ;;
    esac
  fi

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
