#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
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

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Required command not found: $name"
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

load_agent_config

blocked_vars=(
  OPENAI_API_KEY
  CODEX_API_KEY
  ANTHROPIC_API_KEY
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

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"

printf 'Current git branch: %s\n' "$branch"
printf 'Configured work branch: %s\n' "$AGENT_WORK_BRANCH"
printf 'Configured branch mode: %s\n' "$AGENT_BRANCH_MODE"
printf 'Configured protected branches: %s\n' "$AGENT_PROTECTED_BRANCHES"

case "$branch" in
  main|master)
    fail "Refusing to run on protected human branch $branch."
    ;;
esac

if branch_is_protected "$branch"; then
  fail "Refusing to run on protected branch $branch."
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

printf 'Agent environment check passed.\n'
printf 'Reminder: verify Codex is using ChatGPT login, not API-key auth.\n'
printf 'Reminder: verify Claude Code is using subscription OAuth, not API-key auth.\n'
