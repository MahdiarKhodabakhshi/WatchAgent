#!/usr/bin/env bash
# Claude-only planner for the idea pipeline. Turns a free-text idea into a short
# implementation plan. Read-only: it is told not to edit files.
#
# Usage:
#   scripts/claude-planner.sh --text-file PATH --out PATH --log PATH [--max-turns N]
#
# Exit codes: 0 ok, 20 usage/session limit, 22 max turns, other = claude exit.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

text_file=""
out=""
log=""
max_turns="${CLAUDE_PLANNER_MAX_TURNS:-12}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --text-file) shift; text_file="${1-}"; shift ;;
    --out) shift; out="${1-}"; shift ;;
    --log) shift; log="${1-}"; shift ;;
    --max-turns) shift; max_turns="${1-}"; shift ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[ -n "$text_file" ] && [ -f "$text_file" ] || fail "--text-file is required and must exist."
[ -n "$out" ] || fail "--out is required."
[ -n "$log" ] || fail "--log is required."
case "$max_turns" in ''|*[!0-9]*|0) fail "--max-turns must be a positive integer." ;; esac
command -v claude >/dev/null 2>&1 || fail "claude CLI not found."

idea_text="$(cat "$text_file")"

prompt="$(cat <<PROMPT
You are Claude Code acting as Planner for this repository.

A human proposed this idea:
---
${idea_text}
---

Produce a SHORT, concrete implementation plan tailored to THIS codebase. Read
files as needed to be accurate. Output plain text with:
- A one-line title.
- 3 to 7 numbered, concrete steps.
- "Files likely to change:" with a short list.
- "Risks / open questions:" with anything the human should decide.

Keep it brief enough to read on a phone. Do NOT write or edit any files now;
output only the plan.
PROMPT
)"

unset ANTHROPIC_API_KEY 2>/dev/null || true
unset ANTHROPIC_AUTH_TOKEN 2>/dev/null || true

json_out="$(mktemp)"
trap 'rm -f "$json_out"' EXIT

set +e
claude --print "$prompt" --output-format json --max-turns "$max_turns" >"$json_out" 2>"$log"
code=$?
set -e

# Keep the model output in the log for debugging and failure classification.
cat "$json_out" >> "$log" 2>/dev/null || true

classify() {
  if grep -Eiq 'max(imum)?[ -]?turns|turn limit|reached.*turn' "$log"; then
    printf 'max_turns\n'
  elif grep -Eiq 'usage limit|session limit|rate limit|quota|too many requests|exceeded|overloaded' "$log"; then
    printf 'usage_limit\n'
  else
    printf 'failed\n'
  fi
}

if [ "$code" -ne 0 ]; then
  case "$(classify)" in
    usage_limit) exit 20 ;;
    max_turns) exit 22 ;;
    *) exit "$code" ;;
  esac
fi

# Success: extract the plan text from the result JSON.
python3 - "$json_out" "$out" <<'PY'
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(src, encoding="utf-8"))
except Exception:
    data = {}
result = data.get("result")
if not isinstance(result, str) or not result.strip():
    raise SystemExit(3)
open(dst, "w", encoding="utf-8").write(result.strip() + "\n")
PY
plan_code=$?
if [ "$plan_code" -ne 0 ]; then
  # Claude returned success but no usable plan text; treat as a soft failure.
  exit 23
fi
exit 0
