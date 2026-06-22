#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/claude-implementer.sh [TASK-ID]\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to read and update task JSON files."
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

  [[ "$AGENT_WORK_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "AGENT_WORK_BRANCH must be a non-empty git branch name using safe characters."
  [[ "$AGENT_PROTECTED_BRANCHES" =~ ^[A-Za-z0-9._/,-]+$ ]] || fail "AGENT_PROTECTED_BRANCHES must be a comma-separated branch list using safe characters."
  [[ "$AGENT_BRANCH_MODE" =~ ^[A-Za-z0-9._-]+$ ]] || fail "AGENT_BRANCH_MODE must use safe characters."
}

branch_is_protected() {
  local branch="$1"
  local protected
  local old_ifs="$IFS"
  IFS=,
  for protected in $AGENT_PROTECTED_BRANCHES; do
    protected="$(printf '%s' "$protected" | tr -d '[:space:]')"
    if [ "$branch" = "$protected" ]; then
      IFS="$old_ifs"
      return 0
    fi
  done
  IFS="$old_ifs"
  return 1
}

verify_agent_work_branch() {
  local branch="$1"

  case "$branch" in
    main|master)
      fail "Refusing to run Claude implementer on protected human branch $branch."
      ;;
  esac

  if branch_is_protected "$branch"; then
    fail "Refusing to run Claude implementer on protected branch $branch."
  fi

  case "$AGENT_BRANCH_MODE" in
    single_work_branch)
      if [ "$branch" != "$AGENT_WORK_BRANCH" ]; then
        fail "Refusing to run in single_work_branch mode from $branch. Switch to $AGENT_WORK_BRANCH first."
      fi
      ;;
    *)
      fail "Unsupported AGENT_BRANCH_MODE: $AGENT_BRANCH_MODE"
      ;;
  esac
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

validate_task_id() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
import re
import sys

task_id = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    raise SystemExit(1)
if task_id == "TASK-TEMPLATE":
    raise SystemExit(1)
PY
}

ensure_task_run_state() {
  local task_id="$1"
  local branch
  local head

  branch="$(git rev-parse --abbrev-ref HEAD)"
  head="$(git rev-parse HEAD)"
  mkdir -p .agent/run-state

  python3 - "$task_id" "$branch" "$head" "$AGENT_BRANCH_MODE" <<'PY'
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

task_id, branch, head, mode = sys.argv[1:5]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    print(f"ERROR: Unsafe or invalid task id for run-state: {task_id!r}", file=sys.stderr)
    raise SystemExit(1)
if mode != "single_work_branch":
    print(f"ERROR: Unsupported run-state mode: {mode}", file=sys.stderr)
    raise SystemExit(1)

run_state_dir = Path(".agent/run-state")
if run_state_dir.is_symlink():
    print("ERROR: Refusing to write run-state through symlinked .agent/run-state.", file=sys.stderr)
    raise SystemExit(1)
run_state_dir.mkdir(parents=True, exist_ok=True)
path = run_state_dir / f"{task_id}.json"
if path.parent != run_state_dir:
    print(f"ERROR: Refusing unsafe run-state path: {path}", file=sys.stderr)
    raise SystemExit(1)
if path.exists() and path.is_symlink():
    print(f"ERROR: Refusing symlinked run-state file: {path}", file=sys.stderr)
    raise SystemExit(1)

if path.exists():
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: Existing run-state is invalid JSON: {path}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if data.get("task_id") != task_id:
        print(f"ERROR: Run-state task_id mismatch in {path}.", file=sys.stderr)
        raise SystemExit(1)
    if data.get("mode") != mode:
        print(f"ERROR: Run-state mode mismatch in {path}.", file=sys.stderr)
        raise SystemExit(1)
    if data.get("branch") != branch:
        print(f"ERROR: Run-state branch {data.get('branch')!r} does not match current branch {branch!r}.", file=sys.stderr)
        raise SystemExit(1)
    task_start_commit = data.get("task_start_commit")
    if not isinstance(task_start_commit, str) or not task_start_commit:
        print(f"ERROR: Run-state is missing task_start_commit: {path}", file=sys.stderr)
        raise SystemExit(1)
    print(task_start_commit)
    raise SystemExit(0)

data = {
    "task_id": task_id,
    "branch": branch,
    "task_start_commit": head,
    "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "mode": mode,
}
path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
print(head)
PY
}

append_handoff_update() {
  local status="$1"
  local note="$2"
  {
    printf '\n## Script Update %s\n\n' "$timestamp"
    printf '%s\n' "- Current task: $task_id"
    printf '%s\n' "- Current branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
    printf '%s\n' "- Configured work branch: $AGENT_WORK_BRANCH"
    printf '%s\n' "- Branch mode: $AGENT_BRANCH_MODE"
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

  case "$branch_name" in
    main|master)
      fail "Refusing to push protected branch $branch_name."
      ;;
  esac
  if branch_is_protected "$branch_name"; then
    fail "Refusing to push protected branch $branch_name."
  fi

  remote_name="$(git remote | sed -n '1p')"
  if [ -z "$remote_name" ]; then
    append_handoff_update "review_pending" "No git remote configured; work remains on ${branch_name} for human review and merge/cherry-pick."
    printf 'No git remote configured; leaving work on %s for human review.\n' "$branch_name"
    return 0
  fi

  if ! git push -u "$remote_name" "$branch_name"; then
    append_handoff_update "review_pending" "git push of ${branch_name} failed; human review must use the local work branch."
    printf 'WARN: git push failed; skipping draft PR creation.\n' >&2
    return 0
  fi

  if ! gh auth status >/dev/null 2>&1; then
    append_handoff_update "review_pending" "gh is not authenticated; review ${branch_name} manually or create a draft PR yourself."
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

  if ! gh pr create \
    --draft \
    --fill \
    --base "$base_branch" \
    --head "$branch_name"; then
    append_handoff_update "review_pending" "gh pr create failed; implementation changes are preserved on ${branch_name}."
    printf 'WARN: gh pr create failed.\n' >&2
  fi
}

classify_claude_failure() {
  if grep -Eiq 'max(imum)?[ -]?turns|turn limit|reached.*turn' "$log_path"; then
    printf 'max_turns\n'
  elif grep -Eiq 'auth|oauth|login|not authenticated|not logged in|unauthorized|forbidden' "$log_path"; then
    printf 'auth_failed\n'
  elif grep -Eiq 'usage limit|session limit|rate limit|quota|too many requests|exceeded|overloaded' "$log_path"; then
    printf 'usage_limit\n'
  else
    printf 'failed\n'
  fi
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

load_agent_config
scripts/agent-env-check.sh
require_python

current_branch="$(git rev-parse --abbrev-ref HEAD)"
verify_agent_work_branch "$current_branch"

task_id="${1-}"
if [ -z "$task_id" ]; then
  if ! task_id="$(scripts/agent-task-state.py get-next)"; then
    printf 'No actionable task found in .agent/tasks/. Nothing to implement.\n'
    printf 'Approve a proposed task with: scripts/agent-approve.sh TASK-ID\n'
    exit 0
  fi
fi

validate_task_id "$task_id" || fail "Invalid task id: $task_id"

task_file=".agent/tasks/${task_id}.json"
[ -f "$task_file" ] || fail "Task file not found: $task_file"

task_status="$(json_field "$task_file" status)"
case "$task_status" in
  approved|in_progress|needs_revision)
    ;;
  *)
    fail "Task $task_id has status $task_status; expected approved, in_progress, or needs_revision."
    ;;
esac

task_title="$(json_field "$task_file" title)"
[ -n "$task_title" ] || task_title="$task_id"

task_start_commit="$(ensure_task_run_state "$task_id")" || fail "Could not record or reuse task_start_commit for $task_id."
printf 'Task %s base commit: %s\n' "$task_id" "$task_start_commit"

revision_prompt=""
if [ "$task_status" = "needs_revision" ]; then
  revision_prompt="This task has review feedback. Read the latest .agent/reviews/REVIEW-${task_id}-*.json and fix only the required_fixes within the original approved task scope."
fi

claude_max_turns="${CLAUDE_MAX_TURNS:-16}"
case "$claude_max_turns" in
  ''|*[!0-9]*|0)
    fail "CLAUDE_MAX_TURNS must be a positive integer."
    ;;
esac

branch_name="$current_branch"

if [ "$task_status" = "approved" ]; then
  scripts/agent-task-state.py mark-in-progress "$task_id"
fi

mkdir -p .agent/logs
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
log_path=".agent/logs/claude-implementer-${timestamp}.log"

prompt="$(cat <<PROMPT
You are Claude Code acting as Implementer / Engineer for this repository.

Implement exactly one actionable task:
- Task file: ${task_file}
- Task ID: ${task_id}
- Task title: ${task_title}
- Current branch: ${current_branch}
- Configured work branch: ${AGENT_WORK_BRANCH}
- Branch mode: ${AGENT_BRANCH_MODE}
- Task start commit: ${task_start_commit}

${revision_prompt}

Rules:
- Read AGENTS.md, .agent/operating_rules.md, .agent/handoff.md, and the task file before editing.
- If .agent/operating_rules.md contains older per-task branch wording, follow AGENTS.md and this prompt for branch behavior.
- Implement only this task. Do not expand scope or start another task.
- Stay on the current work branch. Do not create, checkout, switch, rename, or merge branches.
- Commit only to the current work branch when you commit.
- Do not push to main, master, or any protected branch.
- Do not create a new task for review fixes.
- Do not ask for new human approval for needs_revision fixes when required fixes stay within the original approved task scope.
- Do not create, modify, print, or request secrets.
- Do not use paid API-key auth.
- Do not modify auth, security-sensitive behavior, database migrations, deployment, payment or billing behavior, destructive-command behavior, or external services unless the approved task explicitly says to do so.
- Use one simple Bash command per tool call. Avoid semicolons, pipes, redirects, &&, and || unless necessary. If a command needs approval, skip it and document it in .agent/handoff.md instead of retrying.
- Run relevant lint, test, typecheck, or build commands when discoverable.
- Update README or docs only if behavior or setup changed.
- Update .agent/handoff.md before stopping with status, changed files, tests run, blockers, and next steps.
- Open or update a draft PR from the current work branch when possible. If gh cannot create the PR, document that in .agent/handoff.md and continue.
- Do not merge pull requests.
- Human final review and merge/cherry-pick to main or master are still required after acceptance.
- Do not mark the task implemented, needs_revision, or blocked. The wrapper and Codex reviewer make the final task status decision.
PROMPT
)"

printf 'Writing Claude implementer log to %s\n' "$log_path"

unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN

set +e
claude --print "$prompt" \
  --verbose \
  --max-turns "$claude_max_turns" \
  --output-format stream-json \
  --permission-mode acceptEdits 2>&1 | tee "$log_path"
claude_exit="${PIPESTATUS[0]}"
set -e

if [ "$claude_exit" -ne 0 ]; then
  failure_kind="$(classify_claude_failure)"
  case "$failure_kind" in
    usage_limit)
      append_handoff_update "blocked" "Claude stopped because a session or usage limit was detected. No retry was attempted."
      commit_if_changed "WIP: preserve ${task_id} after usage limit" || true
      printf 'Claude usage or session limit detected. Checkpoint attempted; see %s\n' "$log_path" >&2
      exit 20
      ;;
    auth_failed)
      append_handoff_update "blocked" "Claude stopped because authentication failed. Re-authenticate with subscription OAuth before retrying."
      commit_if_changed "WIP: preserve ${task_id} after auth failure" || true
      printf 'Claude authentication failure detected. Checkpoint attempted; see %s\n' "$log_path" >&2
      exit 21
      ;;
    max_turns)
      append_handoff_update "blocked" "Claude stopped after reaching the configured max turns (${claude_max_turns}). Resume the same task after reviewing the handoff."
      commit_if_changed "WIP: preserve ${task_id} after max turns" || true
      printf 'Claude reached max turns. Checkpoint attempted; see %s\n' "$log_path" >&2
      exit 22
      ;;
    *)
      append_handoff_update "blocked" "Claude exited with status ${claude_exit}. See log for details."
      commit_if_changed "WIP: preserve ${task_id} after Claude failure" || true
      exit "$claude_exit"
      ;;
  esac
fi

append_handoff_update "implemented_by_claude" "Claude completed its implementation pass. Final task status is reserved for Codex review and the loop."
commit_if_changed "Implement ${task_id}: ${task_title}" || true
push_and_open_draft_pr "$branch_name"
commit_if_changed "Record PR status for ${task_id}" || true

printf 'Claude implementer finished for %s. Log: %s\n' "$task_id" "$log_path"
