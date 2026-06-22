#!/usr/bin/env bash
# Claude-only implementer for the idea pipeline. Implements one confirmed idea
# from its plan, or resumes a previous Claude session after a usage/turn limit.
#
# Usage:
#   scripts/claude-idea-implement.sh --id IDEA-n --text-file PATH --plan-file PATH \
#       --session-out PATH --log PATH [--resume SESSION_ID] [--max-turns N]
#
# Exit codes: 0 ok, 20 usage/session limit, 21 auth, 22 max turns, other.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

idea_id=""
text_file=""
plan_file=""
session_out=""
log=""
resume_session=""
max_turns="${CLAUDE_MAX_TURNS:-40}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --id) shift; idea_id="${1-}"; shift ;;
    --text-file) shift; text_file="${1-}"; shift ;;
    --plan-file) shift; plan_file="${1-}"; shift ;;
    --session-out) shift; session_out="${1-}"; shift ;;
    --log) shift; log="${1-}"; shift ;;
    --resume) shift; resume_session="${1-}"; shift ;;
    --max-turns) shift; max_turns="${1-}"; shift ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ "$idea_id" =~ ^IDEA-[0-9]+$ ]] || fail "--id must look like IDEA-<n>."
[ -n "$text_file" ] && [ -f "$text_file" ] || fail "--text-file is required and must exist."
[ -n "$plan_file" ] && [ -f "$plan_file" ] || fail "--plan-file is required and must exist."
[ -n "$session_out" ] || fail "--session-out is required."
[ -n "$log" ] || fail "--log is required."
case "$max_turns" in ''|*[!0-9]*|0) fail "--max-turns must be a positive integer." ;; esac
if [ -n "$resume_session" ]; then
  [[ "$resume_session" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Unsafe --resume session id."
fi

# Enforce branch safety + subscription-only auth (refuses main/master, API keys).
scripts/agent-env-check.sh >/dev/null

idea_text="$(cat "$text_file")"
plan_text="$(cat "$plan_file")"

base_rules="$(cat <<'RULES'
Rules:
- Read AGENTS.md, .agent/operating_rules.md, and .agent/handoff.md before editing.
- Stay on the current work branch. Do not create, switch, rename, or merge branches.
- Commit your work to the current branch. Do not push to main, master, or any protected branch, and do not merge pull requests.
- Do not create, modify, print, or request secrets. Do not use paid API-key auth.
- Run relevant lint, test, typecheck, or build commands when discoverable.
- Update .agent/handoff.md before stopping with status, changed files, tests run, and next steps.
- Human final review and merge to a protected branch are still required.
RULES
)"

if [ -n "$resume_session" ]; then
  prompt="$(cat <<PROMPT
You are Claude Code resuming work on idea ${idea_id}. Continue the implementation
exactly where you left off, finishing the remaining steps of the plan below.

Original idea:
---
${idea_text}
---

Plan:
---
${plan_text}
---

${base_rules}
PROMPT
)"
else
  prompt="$(cat <<PROMPT
You are Claude Code acting as Implementer for this repository. Implement the
following confirmed idea according to the plan. Implement only this idea; do not
expand scope.

Idea ${idea_id}:
---
${idea_text}
---

Plan:
---
${plan_text}
---

${base_rules}
PROMPT
)"
fi

unset ANTHROPIC_API_KEY 2>/dev/null || true
unset ANTHROPIC_AUTH_TOKEN 2>/dev/null || true

set +e
if [ -n "$resume_session" ]; then
  claude --print "$prompt" \
    --resume "$resume_session" \
    --verbose \
    --max-turns "$max_turns" \
    --output-format stream-json \
    --permission-mode acceptEdits 2>&1 | tee "$log"
  claude_exit="${PIPESTATUS[0]}"
  # If resume failed (e.g. session expired), fall back to a fresh run once.
  if [ "$claude_exit" -ne 0 ] && grep -Eiq 'no conversation|session.*not found|unknown session|no session' "$log"; then
    printf '\n[resume failed; starting a fresh session]\n' | tee -a "$log"
    claude --print "$prompt" \
      --verbose \
      --max-turns "$max_turns" \
      --output-format stream-json \
      --permission-mode acceptEdits 2>&1 | tee -a "$log"
    claude_exit="${PIPESTATUS[0]}"
  fi
else
  claude --print "$prompt" \
    --verbose \
    --max-turns "$max_turns" \
    --output-format stream-json \
    --permission-mode acceptEdits 2>&1 | tee "$log"
  claude_exit="${PIPESTATUS[0]}"
fi
set -e

# Capture the Claude session id so a later /continue can resume this session.
session_id="$(grep -oE '"session_id":"[^"]+"' "$log" 2>/dev/null | head -n 1 | sed 's/.*:"//; s/"$//')"
if [ -n "$session_id" ]; then
  printf '%s\n' "$session_id" > "$session_out"
fi

commit_if_changed() {
  local message="$1"
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "$message" >/dev/null 2>&1 || printf 'WARN: commit failed; changes remain staged.\n' >&2
  fi
}

classify() {
  if grep -Eiq 'max(imum)?[ -]?turns|turn limit|reached.*turn' "$log"; then
    printf 'max_turns\n'
  elif grep -Eiq 'auth|oauth|login|not authenticated|not logged in|unauthorized|forbidden' "$log"; then
    printf 'auth_failed\n'
  elif grep -Eiq 'usage limit|session limit|rate limit|quota|too many requests|exceeded|overloaded' "$log"; then
    printf 'usage_limit\n'
  else
    printf 'failed\n'
  fi
}

if [ "$claude_exit" -ne 0 ]; then
  case "$(classify)" in
    usage_limit) commit_if_changed "WIP: ${idea_id} preserved after usage limit"; exit 20 ;;
    auth_failed) commit_if_changed "WIP: ${idea_id} preserved after auth failure"; exit 21 ;;
    max_turns) commit_if_changed "WIP: ${idea_id} preserved after max turns"; exit 22 ;;
    *) commit_if_changed "WIP: ${idea_id} preserved after Claude failure"; exit "$claude_exit" ;;
  esac
fi

commit_if_changed "Implement ${idea_id}"
exit 0
