#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/codex-reviewer.sh [TASK-ID]\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to find and validate task JSON files."
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
      fail "Refusing to review on protected human branch $branch."
      ;;
  esac

  if branch_is_protected "$branch"; then
    fail "Refusing to review on protected branch $branch."
  fi

  case "$AGENT_BRANCH_MODE" in
    single_work_branch)
      if [ "$branch" != "$AGENT_WORK_BRANCH" ]; then
        fail "Refusing to review in single_work_branch mode from $branch. Switch to $AGENT_WORK_BRANCH first."
      fi
      ;;
    *)
      fail "Unsupported AGENT_BRANCH_MODE: $AGENT_BRANCH_MODE"
      ;;
  esac
}

find_task_by_id() {
  local requested="$1"
  python3 - "$requested" <<'PY'
import json
import pathlib
import re
import sys

requested = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", requested):
    raise SystemExit(1)
if requested == "TASK-TEMPLATE":
    raise SystemExit(1)

task_dir = pathlib.Path(".agent/tasks")
path = task_dir / f"{requested}.json"
if path.parent != task_dir or path.is_symlink():
    raise SystemExit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if data.get("task_id") != requested:
    raise SystemExit(1)
print(path)
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

render_markdown_review() {
  local json_path="$1"
  local markdown_path="$2"
  local expected_task_id="$3"
  python3 - "$json_path" "$markdown_path" "$expected_task_id" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

json_path = Path(sys.argv[1])
markdown_path = Path(sys.argv[2])
expected_task_id = sys.argv[3]

try:
    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"ERROR: Review JSON is invalid: {exc.msg} at line {exc.lineno}, column {exc.colno}", file=sys.stderr)
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

errors: list[str] = []
for key in required_strings:
    if not isinstance(data.get(key), str) or not data[key].strip():
        errors.append(f"{key} must be a non-empty string.")
for key in required_arrays:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{key} must be an array of strings.")
if data.get("task_id") != expected_task_id:
    errors.append(f"task_id must be {expected_task_id!r}.")
if data.get("verdict") not in {"accepted", "needs_revision", "blocked"}:
    errors.append("verdict must be accepted, needs_revision, or blocked.")
if set(data) - set(required_strings) - set(required_arrays):
    errors.append(f"Unexpected keys: {sorted(set(data) - set(required_strings) - set(required_arrays))!r}")

if errors:
    print("ERROR: Review JSON validation failed:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)


def add_list(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- None")
        return
    for item in items:
        lines.append(f"- {item}")


generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
lines = [
    f"# Codex Review: {data['task_id']}",
    "",
    f"- Generated at: {generated_at}",
    f"- Verdict: {data['verdict']}",
    f"- JSON artifact: {json_path}",
    "",
    "## Summary",
    "",
    data["summary"],
    "",
    "## Scope Check",
    "",
    data["scope_check"],
    "",
    "## Tests Check",
    "",
    data["tests_check"],
    "",
    "## Docs Check",
    "",
    data["docs_check"],
    "",
    "## Security Check",
    "",
    data["security_check"],
    "",
    "## Required Fixes",
    "",
]
add_list(lines, data["required_fixes"])
lines.extend(["", "## Recommended Followups", ""])
add_list(lines, data["recommended_followups"])
lines.append("")

markdown_path.write_text("\n".join(lines), encoding="utf-8")
PY
}

write_blocked_review() {
  local task_id="$1"
  local json_path="$2"
  local markdown_path="$3"
  local summary="$4"
  local required_fix="$5"

  python3 - "$task_id" "$json_path" "$summary" "$required_fix" <<'PY'
import json
import sys
from pathlib import Path

task_id, json_path, summary, required_fix = sys.argv[1:5]
data = {
    "task_id": task_id,
    "verdict": "blocked",
    "summary": summary,
    "scope_check": "Blocked before scope review because the wrapper could not establish the task-specific diff.",
    "tests_check": "Blocked before test review because no reliable task diff was available.",
    "docs_check": "Blocked before docs review because no reliable task diff was available.",
    "security_check": "Blocked before security review because no reliable task diff was available.",
    "required_fixes": [required_fix],
    "recommended_followups": [],
}
Path(json_path).write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
PY
  render_markdown_review "$json_path" "$markdown_path" "$task_id"
}

append_plain_block() {
  local content="$1"
  local line

  if [ -z "$content" ]; then
    printf '    (empty)\n'
    return
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    printf '    %s\n' "$line"
  done <<< "$content"
}

append_context_section() {
  local title="$1"
  local content="$2"

  printf '## %s\n\n' "$title"
  append_plain_block "$content"
  printf '\n'
}

write_reviewer_context() {
  local context_path="$1"
  local task_file="$2"
  local task_id="$3"
  local branch="$4"
  local work_branch="$5"
  local branch_mode="$6"
  local task_start_commit="$7"
  local current_head="$8"
  local git_status="$9"
  local diff_stat="${10}"
  local task_json="${11}"
  local handoff_content="${12}"
  local git_diff="${13}"

  {
    cat <<'CONTEXT'
You are Codex acting only as Reviewer for this repository.

Review the wrapper-provided task diff against this approved task:
CONTEXT
    printf '%s\n' "- Task file: ${task_file}"
    printf '%s\n' "- Task ID: ${task_id}"
    printf '\n'
    cat <<'CONTEXT'
Branch context:
CONTEXT
    printf '%s\n' "- Current branch: ${branch}"
    printf '%s\n' "- Configured work branch: ${work_branch}"
    printf '%s\n' "- Branch mode: ${branch_mode}"
    printf '%s\n' "- task_start_commit: ${task_start_commit}"
    printf '%s\n' "- Current HEAD: ${current_head}"
    printf '\n'
    cat <<'CONTEXT'
Review only the diff from task_start_commit to HEAD. Ignore older work already present on the work branch before task_start_commit.

Do not review main...HEAD.
Do not review setup branch...HEAD.
Do not treat older work already on the configured work branch before task_start_commit as part of this task.
Codex must not run shell commands.
Codex must not run Bash, git, gh, or file-inspection commands.
Codex must not modify files.
Codex must emit structured review JSON only.

Return a single JSON object only. It must match .agent/schemas/review.schema.json exactly. Use this shape:
CONTEXT
    printf '{\n'
    printf '  "task_id": "%s",\n' "$task_id"
    cat <<'CONTEXT'
  "verdict": "accepted | needs_revision | blocked",
  "summary": "...",
  "scope_check": "...",
  "tests_check": "...",
  "docs_check": "...",
  "security_check": "...",
  "required_fixes": ["..."],
  "recommended_followups": ["..."]
}

Reviewer rules:
- Codex may not modify app source.
- Codex may only write review artifacts through the wrapper output.
- Check acceptance criteria, tests, docs, security, architecture, and scope.
- If implementation is incomplete but fixable within the original approved scope, use verdict "needs_revision".
- If the task needs a human decision, use verdict "blocked".
- If a context section reports a wrapper collection error, use verdict "blocked".
- If accepted, do not merge; human still reviews and merges or cherry-picks to main or master.
- Put blocking required changes in required_fixes. Include file and line references when available.
- Use recommended_followups only for non-blocking work that should not expand the approved task.

CONTEXT
    append_context_section "Git Status" "$git_status"
    append_context_section "Diff Stat (${task_start_commit}..HEAD)" "$diff_stat"
    append_context_section "Task JSON" "$task_json"
    append_context_section "Handoff" "$handoff_content"
    append_context_section "Git Diff (${task_start_commit}..HEAD)" "$git_diff"
  } > "$context_path"
}

read_run_state() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
import json
import re
import sys
from pathlib import Path

task_id = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    print(f"Unsafe or invalid task id: {task_id!r}", file=sys.stderr)
    raise SystemExit(1)

run_state_dir = Path(".agent/run-state")
path = run_state_dir / f"{task_id}.json"
if path.parent != run_state_dir:
    print(f"Refusing unsafe run-state path: {path}", file=sys.stderr)
    raise SystemExit(1)
if run_state_dir.is_symlink():
    print("Refusing to read symlinked .agent/run-state directory.", file=sys.stderr)
    raise SystemExit(1)
if not path.exists():
    print(f"Missing run-state file: {path}", file=sys.stderr)
    raise SystemExit(1)
if path.is_symlink():
    print(f"Refusing symlinked run-state file: {path}", file=sys.stderr)
    raise SystemExit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"Run-state is invalid JSON: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)

task_start_commit = data.get("task_start_commit")
branch = data.get("branch")
mode = data.get("mode")
if data.get("task_id") != task_id:
    print(f"Run-state task_id mismatch in {path}.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(branch, str) or not branch:
    print(f"Run-state is missing branch: {path}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(task_start_commit, str) or not task_start_commit:
    print(f"Run-state is missing task_start_commit: {path}", file=sys.stderr)
    raise SystemExit(1)
if not re.fullmatch(r"[0-9A-Fa-f]{7,64}", task_start_commit):
    print(f"Run-state task_start_commit has invalid format in {path}.", file=sys.stderr)
    raise SystemExit(1)
if mode != "single_work_branch":
    print(f"Run-state mode must be single_work_branch: {path}", file=sys.stderr)
    raise SystemExit(1)

print(f"{branch}\t{task_start_commit}\t{mode}")
PY
}

classify_codex_failure() {
  if grep -Eiq 'auth|oauth|login|not authenticated|not logged in|unauthorized|forbidden' "$log_path"; then
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

branch="$(git rev-parse --abbrev-ref HEAD)"
verify_agent_work_branch "$branch"

requested_task_id="${1-}"
if [ -z "$requested_task_id" ]; then
  if ! requested_task_id="$(scripts/agent-task-state.py get-next)"; then
    fail "Could not infer task file. Pass TASK-ID explicitly."
  fi
fi

if ! task_file="$(find_task_by_id "$requested_task_id")"; then
  fail "Could not find valid task file for $requested_task_id."
fi

task_id="$(json_field "$task_file" task_id)"
[ -n "$task_id" ] || fail "Task file has no task_id: $task_file"

mkdir -p .agent/logs .agent/reviews .agent/tmp
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
review_md_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.md"
review_json_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.json"
context_path=".agent/tmp/reviewer-context-${task_id}-${timestamp}.md"
log_path=".agent/logs/codex-reviewer-${timestamp}.log"

task_json="$(cat "$task_file")"
if [ -f .agent/handoff.md ]; then
  handoff_content="$(cat .agent/handoff.md)"
else
  handoff_content="No .agent/handoff.md present."
fi

if current_head_output="$(git rev-parse HEAD 2>&1)"; then
  current_head="$current_head_output"
else
  current_head_exit="$?"
  current_head="$(printf 'ERROR: git rev-parse HEAD failed with exit status %s.\n%s' "$current_head_exit" "$current_head_output")"
fi

if git_status_output="$(git status --short --branch 2>&1)"; then
  git_status="$git_status_output"
  [ -n "$git_status" ] || git_status="git status --short --branch produced no output."
else
  git_status_exit="$?"
  git_status="$(printf 'ERROR: git status --short --branch failed with exit status %s.\n%s' "$git_status_exit" "$git_status_output")"
fi

case "$AGENT_BRANCH_MODE" in
  single_work_branch)
    if ! run_state_line="$(read_run_state "$task_id" 2>&1)"; then
      task_start_commit="UNAVAILABLE"
      diff_stat="Not collected because task_start_commit/run-state is missing or invalid for single_work_branch review. Detail: ${run_state_line}"
      git_diff="$diff_stat"
      write_reviewer_context \
        "$context_path" \
        "$task_file" \
        "$task_id" \
        "$branch" \
        "$AGENT_WORK_BRANCH" \
        "$AGENT_BRANCH_MODE" \
        "$task_start_commit" \
        "$current_head" \
        "$git_status" \
        "$diff_stat" \
        "$task_json" \
        "$handoff_content" \
        "$git_diff"
      write_blocked_review \
        "$task_id" \
        "$review_json_path" \
        "$review_md_path" \
        "Missing or invalid task_start_commit/run-state for ${task_id}; task_start_commit is required for single_work_branch review." \
        "Run scripts/agent-loop.sh for this task to create .agent/run-state/${task_id}.json, or rerun with --reset-task-base after manually confirming the current HEAD should be the new task base. Detail: ${run_state_line}"
      printf 'Codex reviewer blocked before Codex run. Context: %s Review: %s JSON: %s\n' "$context_path" "$review_md_path" "$review_json_path"
      exit 0
    fi
    IFS=$'\t' read -r run_state_branch task_start_commit run_state_mode <<< "$run_state_line"
    if [ -z "${task_start_commit:-}" ]; then
      task_start_commit="UNAVAILABLE"
      diff_stat="Not collected because task_start_commit/run-state is missing for single_work_branch review."
      git_diff="$diff_stat"
      write_reviewer_context \
        "$context_path" \
        "$task_file" \
        "$task_id" \
        "$branch" \
        "$AGENT_WORK_BRANCH" \
        "$AGENT_BRANCH_MODE" \
        "$task_start_commit" \
        "$current_head" \
        "$git_status" \
        "$diff_stat" \
        "$task_json" \
        "$handoff_content" \
        "$git_diff"
      write_blocked_review \
        "$task_id" \
        "$review_json_path" \
        "$review_md_path" \
        "Missing task_start_commit/run-state for ${task_id}; task_start_commit is required for single_work_branch review." \
        "Repair .agent/run-state/${task_id}.json or rerun scripts/agent-loop.sh --reset-task-base after manually confirming the current HEAD should be the new task base."
      printf 'Codex reviewer blocked before Codex run. Context: %s Review: %s JSON: %s\n' "$context_path" "$review_md_path" "$review_json_path"
      exit 0
    fi
    if [ "$run_state_branch" != "$branch" ]; then
      diff_stat="Not collected because run-state branch ${run_state_branch} does not match current branch ${branch}."
      git_diff="$diff_stat"
      write_reviewer_context \
        "$context_path" \
        "$task_file" \
        "$task_id" \
        "$branch" \
        "$AGENT_WORK_BRANCH" \
        "$AGENT_BRANCH_MODE" \
        "$task_start_commit" \
        "$current_head" \
        "$git_status" \
        "$diff_stat" \
        "$task_json" \
        "$handoff_content" \
        "$git_diff"
      write_blocked_review \
        "$task_id" \
        "$review_json_path" \
        "$review_md_path" \
        "Run-state branch ${run_state_branch} does not match current branch ${branch}." \
        "Switch to ${run_state_branch} or reset the task base on ${branch} with scripts/agent-loop.sh --reset-task-base after confirming the current HEAD should be the new task base."
      printf 'Codex reviewer blocked before Codex run. Context: %s Review: %s JSON: %s\n' "$context_path" "$review_md_path" "$review_json_path"
      exit 0
    fi
    ;;
  *)
    fail "Unsupported AGENT_BRANCH_MODE: $AGENT_BRANCH_MODE"
    ;;
esac

if ! git rev-parse --verify "${task_start_commit}^{commit}" >/dev/null 2>&1; then
  diff_stat="Not collected because task_start_commit ${task_start_commit} is not a valid commit in this repository."
  git_diff="$diff_stat"
  write_reviewer_context \
    "$context_path" \
    "$task_file" \
    "$task_id" \
    "$branch" \
    "$AGENT_WORK_BRANCH" \
    "$AGENT_BRANCH_MODE" \
    "$task_start_commit" \
    "$current_head" \
    "$git_status" \
    "$diff_stat" \
    "$task_json" \
    "$handoff_content" \
    "$git_diff"
  write_blocked_review \
    "$task_id" \
    "$review_json_path" \
    "$review_md_path" \
    "task_start_commit ${task_start_commit} is not a valid commit in this repository." \
    "Repair .agent/run-state/${task_id}.json or rerun scripts/agent-loop.sh --reset-task-base after manually confirming the current HEAD should be the new task base."
  printf 'Codex reviewer blocked before Codex run. Context: %s Review: %s JSON: %s\n' "$context_path" "$review_md_path" "$review_json_path"
  exit 0
fi

diff_range="${task_start_commit}..HEAD"
if diff_stat_output="$(git diff --stat "$diff_range" 2>&1)"; then
  diff_stat="$diff_stat_output"
  [ -n "$diff_stat" ] || diff_stat="git diff --stat ${diff_range} produced no output."
else
  diff_stat_exit="$?"
  diff_stat="$(printf 'ERROR: git diff --stat %s failed with exit status %s.\n%s' "$diff_range" "$diff_stat_exit" "$diff_stat_output")"
fi

if git_diff_output="$(git diff "$diff_range" 2>&1)"; then
  git_diff="$git_diff_output"
  [ -n "$git_diff" ] || git_diff="git diff ${diff_range} produced no output."
else
  git_diff_exit="$?"
  git_diff="$(printf 'ERROR: git diff %s failed with exit status %s.\n%s' "$diff_range" "$git_diff_exit" "$git_diff_output")"
fi

write_reviewer_context \
  "$context_path" \
  "$task_file" \
  "$task_id" \
  "$branch" \
  "$AGENT_WORK_BRANCH" \
  "$AGENT_BRANCH_MODE" \
  "$task_start_commit" \
  "$current_head" \
  "$git_status" \
  "$diff_stat" \
  "$task_json" \
  "$handoff_content" \
  "$git_diff"

printf 'Writing Codex reviewer context to %s\n' "$context_path"
printf 'Writing Codex review JSON to %s\n' "$review_json_path"
printf 'Writing Codex reviewer log to %s\n' "$log_path"

set +e
codex -C "$ROOT_DIR" -s read-only -a never exec \
  --color never \
  --ephemeral \
  --output-schema ".agent/schemas/review.schema.json" \
  --output-last-message "$review_json_path" \
  - < "$context_path" 2>&1 | tee "$log_path"
codex_exit="${PIPESTATUS[0]}"
set -e

if [ "$codex_exit" -ne 0 ]; then
  failure_kind="$(classify_codex_failure)"
  case "$failure_kind" in
    usage_limit)
      printf 'Codex reviewer usage or session limit detected. See %s\n' "$log_path" >&2
      exit 20
      ;;
    auth_failed)
      printf 'Codex reviewer authentication failure detected. See %s\n' "$log_path" >&2
      exit 21
      ;;
    *)
      fail "Codex reviewer exited with status $codex_exit. See $log_path"
      ;;
  esac
fi

if [ ! -s "$review_json_path" ]; then
  failure_kind="$(classify_codex_failure)"
  case "$failure_kind" in
    usage_limit)
      printf 'Codex reviewer usage or session limit detected before JSON output. See %s\n' "$log_path" >&2
      exit 20
      ;;
    auth_failed)
      printf 'Codex reviewer authentication failure detected before JSON output. See %s\n' "$log_path" >&2
      exit 21
      ;;
    *)
      fail "Codex reviewer did not produce JSON at $review_json_path. See $log_path"
      ;;
  esac
fi

render_markdown_review "$review_json_path" "$review_md_path" "$task_id"

printf 'Codex reviewer finished. Review: %s JSON: %s Log: %s\n' "$review_md_path" "$review_json_path" "$log_path"
