#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

scripts/agent-env-check.sh

branch="$(git rev-parse --abbrev-ref HEAD)"
case "$branch" in
  main|master)
    printf 'ERROR: Refusing to run Codex planner on %s. Create or switch to a non-main branch first.\n' "$branch" >&2
    exit 1
    ;;
esac

mkdir -p .agent/logs .agent/plans .agent/tasks
timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
log_path=".agent/logs/codex-planner-${timestamp}.log"

prompt="$(cat <<'PROMPT'
You are Codex acting only as Planner / Product + Architecture + Security Analyst for this repository.

Read these files first:
- .agent/project_brief.md
- .agent/operating_rules.md
- .agent/backlog.md
- .agent/handoff.md
- .agent/schemas/task.schema.json
- AGENTS.md

Then inspect the current repository structure and recent git history using safe read-only commands. If gh is authenticated, inspect open pull requests and issues for planning context. Do not require gh auth; continue locally if it is unavailable.

Your allowed outputs are:
- Create or update planning notes in .agent/plans/.
- Create task JSON files in .agent/tasks/ that match .agent/schemas/task.schema.json.
- Update .agent/backlog.md or .agent/handoff.md only when needed for durable planning state.

Planning rules:
- Do not implement application code.
- Do not modify app source files, frontend source files, deployment files, or project behavior.
- Do not create or modify secrets.
- Keep this workflow subscription-first. Do not request or rely on API-key auth.
- Proposed new tasks must have status "proposed".
- Tasks explicitly listed under "Approved Now" in .agent/backlog.md may have status "approved".
- High-risk work must be proposed and must clearly require human approval.
- Create small, reviewable, one-task-per-run task files.
- Include objective, context, implementation plan, acceptance criteria, test plan, files likely to change, forbidden changes, and done definition.
- Record any assumptions in the plan files.

Stop after planning. Do not edit application code.
PROMPT
)"

printf 'Writing Codex planner log to %s\n' "$log_path"
codex -C "$ROOT_DIR" -s workspace-write -a never exec "$prompt" 2>&1 | tee "$log_path"
