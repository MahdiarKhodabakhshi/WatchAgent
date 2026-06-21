#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Required command not found: $name"
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

  append_section "$title"
  printf '```text\n' >> "$context_path"
  if ! "$@" >> "$context_path" 2>&1; then
    printf '\n[command failed]\n' >> "$context_path"
  fi
  printf '```\n' >> "$context_path"
}

append_file_if_present() {
  local label="$1"
  local path="$2"

  append_section "$label"
  if [ -f "$path" ]; then
    printf 'Source: `%s`\n\n' "$path" >> "$context_path"
    printf '```text\n' >> "$context_path"
    sed -n '1,260p' "$path" >> "$context_path"
    printf '```\n' >> "$context_path"
  else
    printf 'Not present: `%s`\n' "$path" >> "$context_path"
  fi
}

append_existing_task_files() {
  append_section "Existing Task Files"

  shopt -s nullglob
  local task_files=(.agent/tasks/*.json)
  shopt -u nullglob

  if [ "${#task_files[@]}" -eq 0 ]; then
    printf 'No task JSON files found.\n' >> "$context_path"
    return
  fi

  local path
  for path in "${task_files[@]}"; do
    printf '\n### `%s`\n\n' "$path" >> "$context_path"
    printf '```json\n' >> "$context_path"
    sed -n '1,220p' "$path" >> "$context_path"
    printf '```\n' >> "$context_path"
  done
}

append_recent_logs() {
  append_section "Recent Agent Logs"

  shopt -s nullglob
  local log_files=(.agent/logs/*.log)
  shopt -u nullglob

  if [ "${#log_files[@]}" -eq 0 ]; then
    printf 'No recent agent logs found.\n' >> "$context_path"
    return
  fi

  local recent_logs
  recent_logs="$(
    printf '%s\n' "${log_files[@]}" |
      xargs -r ls -t 2>/dev/null |
      sed -n '1,3p'
  )"

  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    printf '\n### `%s`\n\n' "$path" >> "$context_path"
    printf '```text\n' >> "$context_path"
    tail -n 80 "$path" >> "$context_path" 2>&1 || printf '[could not read log]\n' >> "$context_path"
    printf '```\n' >> "$context_path"
  done <<EOF
$recent_logs
EOF
}

scripts/agent-env-check.sh
require_command python3

branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main|master)
    fail "Refusing to run Codex planner on $branch. Create or switch to a non-main branch first."
    ;;
esac

mkdir -p .agent/logs .agent/tmp .agent/plans .agent/tasks .agent/approvals/pending

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
context_path=".agent/tmp/planner-context.md"
prompt_path=".agent/tmp/planner-prompt-${timestamp}.md"
planner_output_path=".agent/tmp/planner-output-${timestamp}.json"
log_path=".agent/logs/codex-planner-${timestamp}.log"

{
  printf '# Planner Context\n\n'
  printf '%s\n' "- Generated at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s\n' "- Repository root: $ROOT_DIR"
} > "$context_path"

append_command_output "Current Branch" git rev-parse --abbrev-ref HEAD
append_command_output "Git Status" git status --short --branch
append_command_output "Recent Git History" git log --oneline --decorate -n 10
append_command_output "Top-Level File Tree" bash -lc "find . -maxdepth 3 \\( -name .git -o -name node_modules -o -name .venv -o -name dist -o -name build \\) -prune -o -print | sort"
append_file_if_present "AGENTS.md" "AGENTS.md"
append_file_if_present ".agent/project_brief.md" ".agent/project_brief.md"
append_file_if_present ".agent/operating_rules.md" ".agent/operating_rules.md"
append_file_if_present ".agent/backlog.md" ".agent/backlog.md"
append_file_if_present ".agent/handoff.md" ".agent/handoff.md"
append_file_if_present "README.md" "README.md"
append_file_if_present ".agent/schemas/task.schema.json" ".agent/schemas/task.schema.json"
append_existing_task_files
append_recent_logs

{
  cat <<'PROMPT'
You are Codex acting only as Planner / Product + Architecture + Security Analyst for this repository.

The local wrapper has already gathered repository context below. Use that context only for planning. Do not edit files, do not call apply_patch, do not run `codex apply`, and do not write planning/task files yourself.

Return a single JSON object only:
- No Markdown prose outside the JSON.
- No comments.
- No code fences unless unavoidable; the materializer can strip one surrounding JSON fence.
- The JSON must match `.agent/schemas/planner-output.schema.json`.
- Every task object must match `.agent/schemas/task.schema.json`.

Planner output shape:
{
  "summary": "string",
  "observations": ["string"],
  "risks": ["string"],
  "recommended_order": ["TASK-id"],
  "tasks": [
    {
      "task_id": "TASK-...",
      "title": "string",
      "status": "proposed",
      "source": "codex-planner",
      "created_at": "YYYY-MM-DDTHH:MM:SSZ",
      "risk": "low|medium|high",
      "area": "string",
      "approval_required": true,
      "approved_by": null,
      "objective": "string",
      "context": "string",
      "implementation_plan": ["string"],
      "acceptance_criteria": ["string"],
      "test_plan": ["string"],
      "files_likely_to_change": ["string"],
      "forbidden_changes": ["string"],
      "done_definition": ["string"]
    }
  ]
}

Planning rules:
- Do not implement application code.
- Do not modify app source files, frontend source files, backend source files, Docker/deployment files, package files, or project behavior.
- Do not create, modify, print, or request secrets.
- Keep this workflow subscription-first. Do not request or rely on API-key auth.
- Proposed new tasks must have status "proposed".
- Do not re-emit an existing task_id unless you are refreshing an existing unapproved "proposed" task.
- Never re-emit or mutate approved, in_progress, needs_revision, implemented, blocked, or rejected task ids.
- Tasks explicitly listed under "Approved Now" in `.agent/backlog.md` may have status "approved".
- High-risk work must be proposed unless it is explicitly approved by the human, and it must clearly require approval.
- Create small, reviewable, one-task-per-run task objects.
- Include objective, context, implementation plan, acceptance criteria, test plan, files likely to change, forbidden changes, and done definition.
- Prefer tasks that remain within the repository's agent workflow constraints.
- Record assumptions in the JSON fields, not in prose around the JSON.

Repository context follows.
PROMPT
  printf '\n'
  cat "$context_path"
} > "$prompt_path"

printf 'Writing planner context to %s\n' "$context_path"
printf 'Writing Codex planner log to %s\n' "$log_path"

set +e
codex -C "$ROOT_DIR" -s read-only -a never exec \
  --color never \
  --ephemeral \
  --output-schema ".agent/schemas/planner-output.schema.json" \
  --output-last-message "$planner_output_path" \
  - < "$prompt_path" 2>&1 | tee "$log_path"
codex_exit="${PIPESTATUS[0]}"
set -e

if [ "$codex_exit" -ne 0 ]; then
  fail "Codex planner exited with status $codex_exit. See $log_path"
fi

if [ ! -s "$planner_output_path" ]; then
  fail "Codex planner did not produce a final JSON message at $planner_output_path. See $log_path"
fi

scripts/materialize-planner-output.py "$planner_output_path"
