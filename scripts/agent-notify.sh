#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/agent-notify.sh EVENT [TASK-ID] [--phase PHASE] [--task-id TASK-ID] [--verdict VERDICT] [--blocker TEXT] [--next-action TEXT]\n' >&2
  printf 'Events: step_started, step_finished, approval_needed, review_ready, implementation_blocked, auth_failed, usage_limit, loop_failed, max_revisions_reached\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

is_supported_event() {
  case "$1" in
    step_started|step_finished|approval_needed|review_ready|implementation_blocked|auth_failed|usage_limit|loop_failed|max_revisions_reached)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

truncate_text() {
  local text="$1"
  local limit="${2:-900}"
  if [ "${#text}" -gt "$limit" ]; then
    printf '%s...' "${text:0:$((limit - 3))}"
  else
    printf '%s' "$text"
  fi
}

safe_filename_part() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

unique_notification_path() {
  local timestamp="$1"
  local event="$2"
  local phase_name="$3"
  local suffix=""
  local path
  local counter=1

  if [ -n "$phase_name" ]; then
    suffix="-$(safe_filename_part "$phase_name")"
  fi

  path=".agent/notifications/${timestamp}-${event}${suffix}.md"
  while [ -e "$path" ]; do
    path=".agent/notifications/${timestamp}-${event}${suffix}-${counter}.md"
    counter=$((counter + 1))
  done
  printf '%s\n' "$path"
}

event="${1-}"
if [ -z "$event" ]; then
  usage
  exit 2
fi
shift

if ! is_supported_event "$event"; then
  usage
  fail "Unsupported notification event: $event"
fi

task_id=""
phase=""
verdict=""
blocker=""
next_action=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-id)
      shift
      [ "$#" -gt 0 ] || fail "--task-id requires a value"
      task_id="$1"
      shift
      ;;
    --phase)
      shift
      [ "$#" -gt 0 ] || fail "--phase requires a value"
      phase="$1"
      shift
      ;;
    --verdict)
      shift
      [ "$#" -gt 0 ] || fail "--verdict requires a value"
      verdict="$1"
      shift
      ;;
    --blocker)
      shift
      [ "$#" -gt 0 ] || fail "--blocker requires a value"
      blocker="$1"
      shift
      ;;
    --next-action)
      shift
      [ "$#" -gt 0 ] || fail "--next-action requires a value"
      next_action="$1"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --*)
      usage
      fail "Unknown option: $1"
      ;;
    *)
      if [ -n "$task_id" ]; then
        usage
        fail "Only one positional TASK-ID may be provided."
      fi
      task_id="$1"
      shift
      ;;
  esac
done

mkdir -p .agent/notifications

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
filename_timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
notification_path="$(unique_notification_path "$filename_timestamp" "$event" "$phase")"

current_task="$task_id"
if [ -z "$current_task" ] && [ -x scripts/agent-task-state.py ]; then
  current_task="$(scripts/agent-task-state.py get-next 2>/dev/null || true)"
fi
if [ -z "$current_task" ]; then
  current_task="none"
fi

first_pending_task() {
  shopt -s nullglob
  local pending=(.agent/approvals/pending/*.md)
  shopt -u nullglob
  if [ "${#pending[@]}" -eq 0 ]; then
    return 1
  fi
  basename "${pending[0]}" .md
}

default_next_action() {
  local pending_task
  case "$event" in
    step_started)
      printf 'Wait for the %s phase to finish.\n' "${phase:-current}"
      ;;
    step_finished)
      printf 'Continue the agent loop based on the %s result.\n' "${phase:-phase}"
      ;;
    approval_needed)
      if pending_task="$(first_pending_task)"; then
        printf 'Approve with Telegram command /approve %s or run scripts/agent-approve.sh %s locally.\n' "$pending_task" "$pending_task"
      else
        printf 'Review .agent/approvals/pending/ and approve one task, or send a new /task or /goal command.\n'
      fi
      ;;
    implementation_blocked)
      printf 'Inspect .agent/handoff.md and the latest .agent/logs/ entry, then decide whether to revise, resume, or stop.\n'
      ;;
    review_ready)
      printf 'Review the draft PR manually; agents must not merge it.\n'
      ;;
    auth_failed)
      printf 'Re-authenticate Codex/Claude with subscription login, unset API-key variables, then rerun scripts/agent-loop.sh.\n'
      ;;
    usage_limit)
      printf 'Wait for usage to reset, inspect .agent/handoff.md, then rerun scripts/agent-loop.sh.\n'
      ;;
    loop_failed)
      printf 'Inspect .agent/logs/agent-loop-events.log and rerun scripts/agent-loop.sh after fixing the cause.\n'
      ;;
    max_revisions_reached)
      printf 'Inspect the latest .agent/reviews/ entry and .agent/handoff.md, then decide whether to approve a new task or manually intervene.\n'
      ;;
  esac
}

resolved_next_action="$next_action"
if [ -z "$resolved_next_action" ]; then
  resolved_next_action="$(default_next_action)"
fi

write_pending_approvals() {
  shopt -s nullglob
  local pending=(.agent/approvals/pending/*.md)
  shopt -u nullglob

  if [ "${#pending[@]}" -eq 0 ]; then
    printf '%s\n' '- None'
    return
  fi

  local path
  local count=0
  for path in "${pending[@]}"; do
    count=$((count + 1))
    if [ "$count" -gt 10 ]; then
      printf -- '- ...and %s more\n' "$((${#pending[@]} - 10))"
      break
    fi
    printf -- '- %s\n' "$(basename "$path" .md)"
  done
}

short_message() {
  {
    printf 'Agent event: %s\n' "$event"
    printf 'Task: %s\n' "$current_task"
    if [ -n "$phase" ]; then
      printf 'Phase: %s\n' "$phase"
    fi
    printf 'Branch: %s\n' "$branch"
    if [ -n "$verdict" ]; then
      printf 'Verdict: %s\n' "$verdict"
    fi
    if [ -n "$blocker" ]; then
      printf 'Blocker: %s\n' "$blocker"
    fi
    printf 'Next: %s\n' "$resolved_next_action"
  }
}

message="$(truncate_text "$(short_message)" 900)"

{
  printf '# Agent Notification: %s\n\n' "$event"
  printf '%s\n' "- Event: $event"
  printf '%s\n' "- Timestamp: $timestamp"
  printf '%s\n' "- Branch: $branch"
  printf '%s\n' "- Task ID: $current_task"
  if [ -n "$phase" ]; then
    printf '%s\n' "- Phase: $phase"
  fi
  if [ -n "$verdict" ]; then
    printf '%s\n' "- Verdict: $verdict"
  fi
  if [ -n "$blocker" ]; then
    printf '%s\n' "- Blocker: $blocker"
  fi
  printf '\n## Summary\n\n'
  printf '```text\n%s\n```\n' "$message"
  printf '\n## Pending Approvals\n\n'
  write_pending_approvals
  printf '\n## Next Action\n\n'
  printf '```text\n%s\n```\n' "$(truncate_text "$resolved_next_action" 900)"
} > "$notification_path"

if [ "${AGENT_NOTIFY_CHANNEL:-}" = "telegram" ]; then
  scripts/telegram-send.sh "$message" || true
fi

github_label=""
github_title=""
case "$event" in
  approval_needed)
    github_label="agent/approval-needed"
    github_title="Agent approval needed"
    ;;
  review_ready)
    github_label="agent/review-needed"
    github_title="Agent PR ready for review"
    ;;
esac

if [ -n "$github_label" ] && command -v gh >/dev/null 2>&1 && git remote >/dev/null 2>&1 && [ -n "$(git remote)" ]; then
  if gh auth status >/dev/null 2>&1; then
    gh label create "$github_label" --description "Local agent workflow notification" --color "ededed" >/dev/null 2>&1 || true
    issue_number="$(gh issue list --state open --label "$github_label" --json number --jq '.[0].number' 2>/dev/null || true)"
    if [ -n "$issue_number" ] && [ "$issue_number" != "null" ]; then
      gh issue comment "$issue_number" --body-file "$notification_path" >/dev/null 2>&1 || true
    else
      gh issue create --title "$github_title" --body-file "$notification_path" --label "$github_label" >/dev/null 2>&1 || true
    fi
  fi
fi

printf 'Wrote notification: %s\n' "$notification_path"
