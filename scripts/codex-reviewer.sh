#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  printf 'Usage: scripts/codex-reviewer.sh TASK-ID\n' >&2
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_python() {
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to validate review JSON files."
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Required command not found: $name"
}

validate_task_id_arg() {
  local task_id="$1"
  python3 - "$task_id" <<'PY'
import re
import sys

task_id = sys.argv[1]
if not re.fullmatch(r"TASK-[A-Za-z0-9._-]+", task_id):
    print(f"ERROR: invalid task id: {task_id!r}", file=sys.stderr)
    raise SystemExit(1)
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

append_section() {
  local title="$1"
  {
    printf '\n## %s\n\n' "$title"
  } >> "$context_path"
}

append_command_output() {
  local title="$1"
  shift
  local status

  append_section "$title"
  printf 'Command:' >> "$context_path"
  printf ' `%q`' "$@" >> "$context_path"
  printf '\n\n```text\n' >> "$context_path"
  set +e
  "$@" >> "$context_path" 2>&1
  status="$?"
  set -e
  if [ "$status" -ne 0 ]; then
    printf '\n[command failed with exit %s]\n' "$status" >> "$context_path"
  fi
  printf '```\n' >> "$context_path"
  return "$status"
}

append_file_if_present() {
  local label="$1"
  local path="$2"

  append_section "$label"
  if [ -f "$path" ]; then
    printf 'Source: `%s`\n\n' "$path" >> "$context_path"
    printf '```text\n' >> "$context_path"
    cat "$path" >> "$context_path"
    printf '\n```\n' >> "$context_path"
  else
    printf 'Not present: `%s`\n' "$path" >> "$context_path"
  fi
}

append_recent_claude_log_path() {
  append_section "Recent Claude Implementer Log"
  python3 - <<'PY' >> "$context_path"
from pathlib import Path

paths = sorted(
    Path(".agent/logs").glob("claude-implementer-*.log"),
    key=lambda path: (path.stat().st_mtime_ns, path.name),
    reverse=True,
)
if not paths:
    print("No Claude implementer log found.")
else:
    path = paths[0]
    print(f"- Latest path: `{path}`")
    print(f"- Size bytes: {path.stat().st_size}")
    print("- Contents omitted by default; reviewer should rely on task JSON, handoff, and diff context.")
PY
}

validate_review_json() {
  local json_path="$1"
  local expected_task_id="$2"
  python3 - "$json_path" "$expected_task_id" <<'PY'
import json
import sys
from pathlib import Path
from typing import Any

json_path = Path(sys.argv[1])
expected_task_id = sys.argv[2]

try:
    data: Any = json.loads(json_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"ERROR: Review JSON is invalid: {exc.msg} at line {exc.lineno}, column {exc.colno}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(data, dict):
    print("ERROR: Review JSON must be an object.", file=sys.stderr)
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
PY
}

render_markdown_review() {
  local json_path="$1"
  local markdown_path="$2"
  local expected_task_id="$3"
  validate_review_json "$json_path" "$expected_task_id"
  python3 - "$json_path" "$markdown_path" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

json_path = Path(sys.argv[1])
markdown_path = Path(sys.argv[2])
data = json.loads(json_path.read_text(encoding="utf-8"))


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

write_blocked_review() {
  local json_path="$1"
  local summary="$2"
  local scope_check="$3"
  local tests_check="$4"
  local docs_check="$5"
  local security_check="$6"
  local required_fix="$7"
  python3 - "$json_path" "$task_id" "$summary" "$scope_check" "$tests_check" "$docs_check" "$security_check" "$required_fix" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
data = {
    "task_id": sys.argv[2],
    "verdict": "blocked",
    "summary": sys.argv[3],
    "scope_check": sys.argv[4],
    "tests_check": sys.argv[5],
    "docs_check": sys.argv[6],
    "security_check": sys.argv[7],
    "required_fixes": [sys.argv[8]],
    "recommended_followups": [],
}
json_path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
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

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

scripts/agent-env-check.sh
require_python
require_command git

requested_task_id="$1"
validate_task_id_arg "$requested_task_id"

branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main|master)
    fail "Refusing to review on $branch. Review an implementation branch instead."
    ;;
esac

task_file=".agent/tasks/${requested_task_id}.json"
[ -f "$task_file" ] || fail "Task file not found: $task_file"
[ ! -L "$task_file" ] || fail "Refusing symlinked task file: $task_file"

task_id="$(json_field "$task_file" task_id)"
[ "$task_id" = "$requested_task_id" ] || fail "Task file $task_file has task_id ${task_id:-<empty>}, expected $requested_task_id."

mkdir -p .agent/logs .agent/reviews .agent/tmp
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
context_path=".agent/tmp/reviewer-context-${task_id}-${timestamp}.md"
review_md_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.md"
review_json_path=".agent/reviews/REVIEW-${task_id}-${timestamp}.json"
raw_review_json_path=".agent/tmp/reviewer-output-${task_id}-${timestamp}.json"
prompt_path=".agent/tmp/review-prompt-${task_id}-${timestamp}.md"
log_path=".agent/logs/codex-reviewer-${timestamp}.log"

{
  printf '# Reviewer Context\n\n'
  printf '%s\n' "- Generated at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s\n' "- Repository root: $ROOT_DIR"
  printf '%s\n' "- Task ID: $task_id"
  printf '%s\n' "- Task file: $task_file"
} > "$context_path"

append_command_output "Current Branch" git rev-parse --abbrev-ref HEAD || true
append_command_output "Git Status" git status --short --branch || true

diff_failed=0
if ! append_command_output "Git Diff Stat (main...HEAD)" git diff main...HEAD --stat; then
  diff_failed=1
fi
if ! append_command_output "Git Diff (main...HEAD)" git diff main...HEAD; then
  diff_failed=1
fi

append_file_if_present "Task JSON" "$task_file"
append_file_if_present "AGENTS.md" "AGENTS.md"
append_file_if_present ".agent/operating_rules.md" ".agent/operating_rules.md"
append_file_if_present ".agent/handoff.md" ".agent/handoff.md"
append_recent_claude_log_path

printf 'Writing reviewer context to %s\n' "$context_path"

if [ "$diff_failed" -ne 0 ]; then
  printf 'Local wrapper could not gather git diff; writing blocked review without invoking Codex.\n' >&2
  write_blocked_review \
    "$review_json_path" \
    "Local wrapper diff gathering failed, so Codex was not invoked." \
    "The wrapper could not gather git diff main...HEAD. Scope and acceptance criteria cannot be verified from local context. See $context_path for command output." \
    "Tests were not evaluated because the wrapper could not gather the branch diff needed for review." \
    "Docs were not evaluated because the wrapper could not gather the branch diff needed for review." \
    "Security impact was not evaluated because the wrapper could not gather the branch diff needed for review." \
    "Fix local git diff gathering for main...HEAD, then rerun scripts/codex-reviewer.sh $task_id. Do not ask Codex to recover by running shell commands."
  render_markdown_review "$review_json_path" "$review_md_path" "$task_id"
  printf 'Codex reviewer blocked before Codex invocation. Context: %s Review: %s JSON: %s\n' "$context_path" "$review_md_path" "$review_json_path"
  exit 0
fi

{
  cat <<PROMPT
You are Codex acting only as Reviewer for this repository.

The local wrapper has already gathered all review context below. You are reviewing only the context provided by the wrapper.

Do not run shell commands.
Do not run Bash.
Do not run git.
Do not run gh.
Do not inspect files directly.
Do not modify files.
Emit structured JSON only.

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
- The JSON verdict must be exactly one of accepted, needs_revision, or blocked.
- Check acceptance criteria, tests, docs, security, architecture, and scope using only the provided wrapper context.
- If the implementation is incomplete but fixable within the approved task scope, use needs_revision.
- If a human decision is needed, use blocked.
- If accepted, the human still reviews and merges the PR; do not merge.
- Put blocking required changes in required_fixes. Include file and line references when available in the provided context.
- Use recommended_followups only for non-blocking work that should not expand the approved task.
- If the provided context is insufficient, explain the missing wrapper context in JSON; do not recover by running commands or inspecting files.
- Do not include Markdown prose, comments, or code fences outside the JSON.

Repository review context follows.
PROMPT
  printf '\n'
  cat "$context_path"
} > "$prompt_path"

printf 'Writing raw Codex review JSON to %s\n' "$raw_review_json_path"
printf 'Writing validated Codex review JSON to %s\n' "$review_json_path"
printf 'Writing Codex reviewer log to %s\n' "$log_path"

set +e
codex -C "$ROOT_DIR" -s read-only -a never \
  --disable shell_tool \
  --disable shell_snapshot \
  --disable unified_exec \
  exec \
  --color never \
  --ephemeral \
  --output-schema ".agent/schemas/review.schema.json" \
  --output-last-message "$raw_review_json_path" \
  - < "$prompt_path" 2>&1 | tee "$log_path"
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

if [ ! -s "$raw_review_json_path" ]; then
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
      fail "Codex reviewer did not produce JSON at $raw_review_json_path. See $log_path"
      ;;
  esac
fi

validate_review_json "$raw_review_json_path" "$task_id"
cp "$raw_review_json_path" "$review_json_path"
render_markdown_review "$review_json_path" "$review_md_path" "$task_id"

printf 'Codex reviewer finished. Context: %s Review: %s JSON: %s Log: %s\n' "$context_path" "$review_md_path" "$review_json_path" "$log_path"
