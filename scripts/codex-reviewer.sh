#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to find task JSON files."
}

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

find_task_by_id_or_branch() {
  local requested="${1-}"
  local branch_name
  branch_name="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')"
  python3 - "$requested" "$branch_name" <<'PY'
import json
import pathlib
import re
import sys

requested = sys.argv[1]
branch_name = sys.argv[2]

def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9._-]", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")

branch_slug = ""
if branch_name.startswith("agent/"):
    branch_slug = branch_name.split("/", 1)[1]

matches = []
for path in pathlib.Path(".agent/tasks").glob("*.json"):
    if path.name == "TASK-TEMPLATE.json":
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    task_id = data.get("task_id", "")
    if requested and task_id == requested:
        print(path)
        sys.exit(0)
    if not requested and branch_slug and slugify(task_id) == branch_slug:
        matches.append(path)

if matches:
    print(matches[0])
    sys.exit(0)

sys.exit(1)
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

scripts/agent-env-check.sh
require_python

branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main|master)
    fail "Refusing to review on $branch. Review an implementation branch instead."
    ;;
esac

if ! task_file="$(find_task_by_id_or_branch "${1-}")"; then
  fail "Could not infer task file. Pass TASK-ID explicitly, or run from an agent/<task-id-slug> branch."
fi

task_id="$(json_field "$task_file" task_id)"
[ -n "$task_id" ] || fail "Task file has no task_id: $task_file"

mkdir -p .agent/logs .agent/reviews
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
review_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.md"
log_path=".agent/logs/codex-reviewer-${timestamp}.log"

prompt="$(cat <<PROMPT
You are Codex acting only as Reviewer for this repository.

Review the current branch against this approved task:
- Task file: ${task_file}
- Task ID: ${task_id}

Read:
- AGENTS.md
- .agent/operating_rules.md
- .agent/handoff.md
- ${task_file}

Use read-only commands only. If gh is authenticated and a PR exists for this branch, inspect it for context. Do not edit files.

Write a Markdown review to stdout with:
- Summary
- Findings ordered by severity with file and line references when available
- Scope check
- Acceptance criteria check
- Test coverage and commands observed
- Docs check
- Security review
- Architecture review
- Required revisions, if any
- Residual risks

Do not modify application source code. Do not merge or approve a PR.
PROMPT
)"

printf 'Writing Codex review to %s\n' "$review_path"
codex -C "$ROOT_DIR" -s read-only -a never exec "$prompt" 2>&1 | tee "$log_path" "$review_path" >/dev/null
printf 'Codex reviewer finished. Review: %s Log: %s\n' "$review_path" "$log_path"
