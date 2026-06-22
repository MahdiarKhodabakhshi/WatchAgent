#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/agent-notify.sh REASON [TASK-ID]\n' >&2
  printf 'Reasons: approval_needed, implementation_blocked, review_ready, auth_failed, usage_limit, loop_failed, max_revisions_reached\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

reason="${1-}"
task_id="${2-}"

if [ -z "$reason" ]; then
  usage
  exit 2
fi

case "$reason" in
  approval_needed|implementation_blocked|review_ready|auth_failed|usage_limit|loop_failed|max_revisions_reached)
    ;;
  *)
    usage
    fail "Unsupported notification reason: $reason"
    ;;
esac

mkdir -p .agent/notifications

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
filename_timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
notification_path=".agent/notifications/${filename_timestamp}-${reason}.md"

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

next_command() {
  local pending_task
  case "$reason" in
    approval_needed)
      if pending_task="$(first_pending_task)"; then
        printf 'scripts/agent-approve.sh %s\n' "$pending_task"
      else
        printf 'Review .agent/approvals/pending/ and run scripts/agent-approve.sh TASK-ID.\n'
      fi
      ;;
    implementation_blocked)
      printf 'Inspect .agent/handoff.md and the latest .agent/logs/ entry, then rerun scripts/agent-loop.sh.\n'
      ;;
    review_ready)
      printf 'Review the draft PR manually; agents must not merge it.\n'
      ;;
    auth_failed)
      printf 'Re-authenticate Codex/Claude with subscription login, unset API-key variables, then rerun scripts/agent-loop.sh.\n'
      ;;
    usage_limit)
      printf 'Wait for Claude usage to reset, inspect .agent/handoff.md, then rerun scripts/agent-loop.sh.\n'
      ;;
    loop_failed)
      printf 'Inspect .agent/logs/agent-loop-events.log and rerun scripts/agent-loop.sh after fixing the cause.\n'
      ;;
    max_revisions_reached)
      printf 'Inspect the latest .agent/reviews/ entry and .agent/handoff.md, then decide whether to approve a new task or manually intervene.\n'
      ;;
  esac
}

write_pending_approvals() {
  shopt -s nullglob
  local pending=(.agent/approvals/pending/*.md)
  shopt -u nullglob

  if [ "${#pending[@]}" -eq 0 ]; then
    printf '%s\n' '- None'
    return
  fi

  local path
  for path in "${pending[@]}"; do
    printf -- '- %s\n' "$(basename "$path" .md)"
  done
}

{
  printf '# Agent Notification: %s\n\n' "$reason"
  printf '%s\n' "- Reason: $reason"
  printf '%s\n' "- Timestamp: $timestamp"
  printf '%s\n' "- Branch: $branch"
  printf '%s\n' "- Current actionable task: $current_task"
  printf '\n## Pending Approvals\n\n'
  write_pending_approvals
  printf '\n## Next Command\n\n'
  printf '```bash\n'
  next_command
  printf '```\n'
} > "$notification_path"

github_label=""
github_title=""
case "$reason" in
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
