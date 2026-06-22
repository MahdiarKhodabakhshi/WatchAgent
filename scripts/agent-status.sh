#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"

printf 'Agent status\n'
printf -- '- Branch: %s\n' "$branch"

pause_file=".agent/state/paused"
if [ -f "$pause_file" ]; then
  pause_reason="$(sed -n '1p' "$pause_file" 2>/dev/null || true)"
  if [ -n "$pause_reason" ]; then
    printf -- '- Loop: PAUSED (%s)\n' "$pause_reason"
  else
    printf -- '- Loop: PAUSED\n'
  fi
else
  printf -- '- Loop: active\n'
fi

printf -- '- Actionable tasks:\n'
actionable="$(scripts/agent-task-state.py list-actionable 2>/dev/null || true)"
if [ -n "$actionable" ]; then
  while IFS= read -r task_id; do
    [ -n "$task_id" ] || continue
    printf -- '    - %s\n' "$task_id"
  done <<EOF
$actionable
EOF
else
  printf -- '    - none\n'
fi

printf -- '- Pending approvals:\n'
shopt -s nullglob
pending=(.agent/approvals/pending/*.md)
shopt -u nullglob
if [ "${#pending[@]}" -eq 0 ]; then
  printf -- '    - none\n'
else
  for path in "${pending[@]}"; do
    printf -- '    - %s\n' "$(basename "$path" .md)"
  done
fi

printf -- '- Inbox requests (awaiting planner):\n'
shopt -s nullglob
inbox=(.agent/inbox/*.md)
shopt -u nullglob
if [ "${#inbox[@]}" -eq 0 ]; then
  printf -- '    - none\n'
else
  printf -- '    - %s file(s)\n' "${#inbox[@]}"
fi

last_event=""
if [ -f .agent/logs/agent-loop-events.log ]; then
  last_event="$(tail -n 1 .agent/logs/agent-loop-events.log 2>/dev/null || true)"
fi
if [ -n "$last_event" ]; then
  printf -- '- Last loop event: %s\n' "$last_event"
fi
