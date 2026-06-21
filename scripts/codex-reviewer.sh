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
    if path.name == "TASK-TEMPLATE.json" or path.is_symlink():
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
    f"- JSON artifact: `{json_path}`",
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

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

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

mkdir -p .agent/logs .agent/reviews .agent/tmp
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
review_md_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.md"
review_json_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.json"
prompt_path=".agent/tmp/review-prompt-${task_id}-${timestamp}.md"
log_path=".agent/logs/codex-reviewer-${timestamp}.log"

{
  cat <<PROMPT
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

Return a single JSON object only. It must match .agent/schemas/review.schema.json exactly:
{
  "task_id": "${task_id}",
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
- If accepted, do not merge; human still reviews and merges the PR.
- Put blocking required changes in required_fixes. Include file and line references when available.
- Use recommended_followups only for non-blocking work that should not expand the approved task.
PROMPT
} > "$prompt_path"

printf 'Writing Codex review JSON to %s\n' "$review_json_path"
printf 'Writing Codex reviewer log to %s\n' "$log_path"

set +e
codex -C "$ROOT_DIR" -s read-only -a never exec \
  --color never \
  --ephemeral \
  --output-schema ".agent/schemas/review.schema.json" \
  --output-last-message "$review_json_path" \
  - < "$prompt_path" 2>&1 | tee "$log_path"
codex_exit="${PIPESTATUS[0]}"
set -e

if [ "$codex_exit" -ne 0 ]; then
  fail "Codex reviewer exited with status $codex_exit. See $log_path"
fi

if [ ! -s "$review_json_path" ]; then
  fail "Codex reviewer did not produce JSON at $review_json_path. See $log_path"
fi

render_markdown_review "$review_json_path" "$review_md_path" "$task_id"

printf 'Codex reviewer finished. Review: %s JSON: %s Log: %s\n' "$review_md_path" "$review_json_path" "$log_path"
