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

load_agent_config() {
  AGENT_WORK_BRANCH="${AGENT_WORK_BRANCH:-agent_developed}"
  AGENT_PROTECTED_BRANCHES="${AGENT_PROTECTED_BRANCHES:-main,master}"
  AGENT_BRANCH_MODE="${AGENT_BRANCH_MODE:-single_work_branch}"

  local config_file=".agent/config.env"
  local line key value
  if [ -f "$config_file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      case "$line" in
        ''|\#*)
          continue
          ;;
      esac
      case "$line" in
        AGENT_WORK_BRANCH=*|AGENT_PROTECTED_BRANCHES=*|AGENT_BRANCH_MODE=*)
          key="${line%%=*}"
          value="${line#*=}"
          ;;
        *)
          fail "Unsupported config line in $config_file. Use simple KEY=value entries only."
          ;;
      esac
      case "$value" in
        \"*\")
          value="${value#\"}"
          value="${value%\"}"
          ;;
        \'*\')
          value="${value#\'}"
          value="${value%\'}"
          ;;
      esac
      case "$key" in
        AGENT_WORK_BRANCH)
          AGENT_WORK_BRANCH="$value"
          ;;
        AGENT_PROTECTED_BRANCHES)
          AGENT_PROTECTED_BRANCHES="$value"
          ;;
        AGENT_BRANCH_MODE)
          AGENT_BRANCH_MODE="$value"
          ;;
      esac
    done < "$config_file"
  fi
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

load_agent_config
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

task_status="none"
if [ "$current_task" != "none" ]; then
  task_status="$(python3 - "$current_task" <<'PY'
import json
import re
import sys
from pathlib import Path

task_id = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    print("unknown")
    raise SystemExit(0)
path = Path(".agent/tasks") / f"{task_id}.json"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("unknown")
else:
    status = data.get("status")
    print(status if isinstance(status, str) and status else "unknown")
PY
)"
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
      printf 'Inspect .agent/handoff.md and the latest .agent/logs/ entry on %s, then rerun scripts/agent-loop.sh.\n' "$AGENT_WORK_BRANCH"
      ;;
    review_ready)
      printf 'Review %s manually or review its draft PR if one exists; only a human may merge/cherry-pick to main/master.\n' "$AGENT_WORK_BRANCH"
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

pending_approval_count() {
  shopt -s nullglob
  local pending=(.agent/approvals/pending/*.md)
  shopt -u nullglob
  printf '%s\n' "${#pending[@]}"
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

approval_needed="no"
if [ "$reason" = "approval_needed" ] || [ "$(pending_approval_count)" -gt 0 ]; then
  approval_needed="yes"
fi

final_review_needed="no"
if [ "$reason" = "review_ready" ] || [ "$task_status" = "implemented" ]; then
  final_review_needed="yes"
fi

{
  printf '# Agent Notification: %s\n\n' "$reason"
  printf '%s\n' "- Reason: $reason"
  printf '%s\n' "- Timestamp: $timestamp"
  printf '%s\n' "- Current branch: $branch"
  printf '%s\n' "- Configured work branch: $AGENT_WORK_BRANCH"
  printf '%s\n' "- Branch mode: $AGENT_BRANCH_MODE"
  printf '%s\n' "- Current task: $current_task"
  printf '%s\n' "- Task status: $task_status"
  printf '%s\n' "- Human approval needed: $approval_needed"
  printf '%s\n' "- Human final review/merge needed: $final_review_needed"
  printf '%s\n' "- Merge policy: agents never merge or push to main/master"
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
    github_title="Agent work branch ready for review"
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

latest_review_summary() {
  local task_id="$1"
  [ "$task_id" != "none" ] || return 0
  python3 - "$task_id" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

task_id = sys.argv[1]
reviews = sorted(Path(".agent/reviews").glob(f"REVIEW-{task_id}-*.json"))
if not reviews:
    raise SystemExit(0)
try:
    data = json.loads(reviews[-1].read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
summary = data.get("summary")
if isinstance(summary, str) and summary.strip():
    print(summary.strip()[:300])
PY
}

build_telegram_message() {
  local icon
  case "$reason" in
    approval_needed) icon="[approval]" ;;
    review_ready) icon="[done]" ;;
    implementation_blocked) icon="[blocked]" ;;
    usage_limit) icon="[paused/limit]" ;;
    auth_failed) icon="[auth]" ;;
    loop_failed) icon="[failed]" ;;
    max_revisions_reached) icon="[max-revisions]" ;;
    *) icon="[info]" ;;
  esac

  printf 'WatchAgent %s %s\n' "$icon" "$reason"
  printf 'Task: %s (%s)\n' "$current_task" "$task_status"
  printf 'Branch: %s\n' "$branch"
  [ "$approval_needed" = "yes" ] && printf 'Approval needed.\n'
  [ "$final_review_needed" = "yes" ] && printf 'Final review/merge needed.\n'

  if [ "$reason" = "review_ready" ]; then
    local summary
    summary="$(latest_review_summary "$current_task")"
    [ -n "$summary" ] && printf 'Review: %s\n' "$summary"
  fi

  if git rev-parse HEAD >/dev/null 2>&1; then
    printf '\nLatest commit: %s\n' "$(git log -1 --pretty=%s 2>/dev/null)"
    git show --stat --format='' HEAD 2>/dev/null | sed '/^$/d' | head -n 8
  fi

  printf '\nNext: %s' "$(next_command)"
}

# Best-effort push to Telegram. Never breaks the notification flow.
if [ -x scripts/telegram-send.sh ]; then
  build_telegram_message | scripts/telegram-send.sh || true
fi

printf 'Wrote notification: %s\n' "$notification_path"
