#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Required command not found: $name"
}

blocked_vars=(
  OPENAI_API_KEY
  CODEX_API_KEY
  ANTHROPIC_API_KEY
  ANTHROPIC_AUTH_TOKEN
)

for var_name in "${blocked_vars[@]}"; do
  var_value="${!var_name-}"
  if [ -n "$var_value" ]; then
    fail "$var_name is set. Unset it before running the subscription-first agent workflow."
  fi
done

require_command git
require_command codex
require_command claude
require_command gh

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Current directory is not inside a git repository."

dirty=0
if ! git diff --quiet; then
  dirty=1
fi
if ! git diff --cached --quiet; then
  dirty=1
fi
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
  dirty=1
fi
if [ "$dirty" -ne 0 ]; then
  warn "Working tree has uncommitted or untracked changes."
fi

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"

printf 'Agent environment check passed.\n'
printf 'Current git branch: %s\n' "$branch"
printf 'Reminder: verify Codex is using ChatGPT login, not API-key auth.\n'
printf 'Reminder: verify Claude Code is using subscription OAuth, not API-key auth.\n'
