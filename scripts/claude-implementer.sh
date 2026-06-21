#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to read and update task JSON files."
}

find_oldest_approved_task() {
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
        created_at = data.get("created_at") or ""
        tasks.append((created_at, path.stat().st_mtime, str(path)))

if not tasks:
    sys.exit(1)

tasks.sort(key=lambda item: (item[0], item[1], item[2]))
print(tasks[0][2])
PY
}

json_field() {
  local path="$1"
  local field="$2"
  python3 - "$path" "$field" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
value = data.get(field)
if value is None:
    print("")
else:
    print(value)
PY
}

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

append_handoff_update() {
  local status="$1"
  local note="$2"
  {
    printf '\n## Script Update %s\n\n' "$timestamp"
    printf '%s\n' "- Current task: $task_id"
    printf '%s\n' "- Current branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
    printf '%s\n' "- Status: $status"
    printf '%s\n' "- Note: $note"
    printf '%s\n' "- Log: $log_path"
  } >> .agent/handoff.md
}

commit_if_changed() {
  local message="$1"
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    if git commit -m "$message"; then
      printf 'Committed local changes: %s\n' "$message"
    else
      printf 'WARN: Changes remain uncommitted because git commit failed.\n' >&2
      return 1
    fi
  else
    printf 'No local changes to commit.\n'
  fi
}

push_and_open_draft_pr() {
  local branch_name="$1"
  local remote_name
  local base_branch

  remote_name="$(git remote | sed -n '1p')"
  if [ -z "$remote_name" ]; then
    printf 'No git remote configured; skipping push and draft PR creation.\n'
    return 0
  fi

  if ! git push -u "$remote_name" "$branch_name"; then
    printf 'WARN: git push failed; skipping draft PR creation.\n' >&2
    return 0
  fi

  if ! gh auth status >/dev/null 2>&1; then
    printf 'gh is installed but not authenticated; skipping draft PR creation.\n'
    return 0
  fi

  if gh pr view "$branch_name" --json url -q .url >/dev/null 2>&1; then
    printf 'Draft PR already exists for %s.\n' "$branch_name"
    return 0
  fi

  base_branch="$(git symbolic-ref "refs/remotes/${remote_name}/HEAD" 2>/dev/null | sed "s#^refs/remotes/${remote_name}/##" || true)"
  if [ -z "$base_branch" ]; then
    if git show-ref --verify --quiet "refs/remotes/${remote_name}/main"; then
      base_branch="main"
    elif git show-ref --verify --quiet "refs/remotes/${remote_name}/master"; then
      base_branch="master"
    else
      base_branch="main"
    fi
  fi

  gh pr create \
    --draft \
    --fill \
    --base "$base_branch" \
    --head "$branch_name" || printf 'WARN: gh pr create failed.\n' >&2
}

scripts/agent-env-check.sh
require_python

current_branch="$(git rev-parse --abbrev-ref HEAD)"
case "$current_branch" in
  main|master)
    fail "Refusing to run Claude implementer on $current_branch. Create or switch to a non-main branch first."
    ;;
esac

if ! task_file="$(find_oldest_approved_task)"; then
  printf 'No approved task found in .agent/tasks/. Nothing to implement.\n'
  printf 'Approve a proposed task with: scripts/agent-approve.sh TASK-ID\n'
  exit 0
fi

task_id="$(json_field "$task_file" task_id)"
task_title="$(json_field "$task_file" title)"
[ -n "$task_id" ] || fail "Selected task file has no task_id: $task_file"

branch_slug="$(slugify "$task_id")"
branch_name="agent/${branch_slug}"

if [ "$current_branch" != "$branch_name" ]; then
  if git rev-parse --verify "$branch_name" >/dev/null 2>&1; then
    git switch "$branch_name"
  else
    git switch -c "$branch_name"
  fi
fi

mkdir -p .agent/logs
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
log_path=".agent/logs/claude-implementer-${timestamp}.log"

prompt="$(cat <<PROMPT
You are Claude Code acting as Implementer / Engineer for this repository.

Implement exactly one approved task:
- Task file: ${task_file}
- Task ID: ${task_id}
- Task title: ${task_title}

Rules:
- Read AGENTS.md, .agent/operating_rules.md, .agent/handoff.md, and the task file before editing.
- Implement only the approved task. Do not expand scope.
- Do not create, modify, print, or request secrets.
- Do not use paid API-key auth.
- Do not modify auth, security-sensitive behavior, database migrations, deployment, payment or billing behavior, destructive-command behavior, or external services unless the approved task explicitly says to do so.
- Run relevant lint, test, typecheck, or build commands when discoverable.
- Update README or docs only if behavior or setup changed.
- Update .agent/handoff.md before stopping with status, changed files, tests run, blockers, and next steps.
- If implementation is complete, update the task status to "implemented". If blocked, update it to "blocked" and explain why.
- Do not push, merge, or create PRs. The wrapper script handles commit, push, and draft PR creation.
PROMPT
)"

printf 'Writing Claude implementer log to %s\n' "$log_path"

unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

set +e
claude --print "$prompt" \
  --verbose \
  --max-turns 8 \
  --output-format stream-json \
  --permission-mode acceptEdits 2>&1 | tee "$log_path"
claude_exit="${PIPESTATUS[0]}"
set -e

if [ "$claude_exit" -ne 0 ]; then
  if grep -Eiq 'usage limit|session limit|rate limit|quota|too many requests|exceeded' "$log_path"; then
    append_handoff_update "blocked" "Claude stopped because a session or usage limit was detected. No retry was attempted."
    commit_if_changed "WIP: preserve ${task_id} after session limit" || true
    exit "$claude_exit"
  fi
  append_handoff_update "blocked" "Claude exited with status ${claude_exit}. See log for details."
  exit "$claude_exit"
fi

commit_if_changed "Implement ${task_id}: ${task_title}" || true
push_and_open_draft_pr "$branch_name"

printf 'Claude implementer finished for %s. Log: %s\n' "$task_id" "$log_path"
