#!/usr/bin/env bash
set -uo pipefail

# Keep a long-running agent process alive: run it, and if it exits, restart it
# after a capped exponential backoff. This is the portable always-on option for
# machines without systemd (use the units in deploy/systemd/ where available).
#
# Usage:
#   scripts/agent-supervisor.sh NAME -- COMMAND [ARGS...]
#
# Examples:
#   scripts/agent-supervisor.sh telegram -- scripts/agent-telegram-bot.py
#   scripts/agent-supervisor.sh loop -- scripts/agent-loop.sh --forever
#
# Stop a supervised process cleanly by creating its stop file:
#   touch .agent/state/supervisor-<NAME>.stop
# The supervisor removes the stop file when it starts, and exits (without
# restarting) the next time it sees the file or when it receives SIGINT/SIGTERM.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

name=""
if [ "$#" -ge 1 ] && [ "$1" != "--" ]; then
  name="$1"
  shift
fi
[ -n "$name" ] || fail "Usage: scripts/agent-supervisor.sh NAME -- COMMAND [ARGS...]"
[[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || fail "NAME must use safe characters: $name"

[ "${1-}" = "--" ] || fail "Expected -- before COMMAND. Usage: scripts/agent-supervisor.sh NAME -- COMMAND [ARGS...]"
shift
[ "$#" -ge 1 ] || fail "No COMMAND given after --."

mkdir -p .agent/state .agent/logs
stop_file=".agent/state/supervisor-${name}.stop"
log_file=".agent/logs/supervisor-${name}.log"

# Clear any stale stop request from a previous run.
rm -f "$stop_file"

stop_requested=0
request_stop() {
  stop_requested=1
}
trap request_stop INT TERM

log() {
  local timestamp
  timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '%s supervisor=%s %s\n' "$timestamp" "$name" "$*" | tee -a "$log_file"
}

min_backoff=2
max_backoff=300
backoff="$min_backoff"

log "starting; command: $*"

while :; do
  if [ "$stop_requested" -eq 1 ] || [ -f "$stop_file" ]; then
    log "stop requested; not starting a new run"
    break
  fi

  start_epoch="$(date +%s)"
  "$@"
  exit_code="$?"
  end_epoch="$(date +%s)"
  ran_for=$(( end_epoch - start_epoch ))

  log "child exited code=$exit_code after ${ran_for}s"

  if [ "$stop_requested" -eq 1 ] || [ -f "$stop_file" ]; then
    log "stop requested; supervisor exiting"
    break
  fi

  # Reset backoff if the child ran for a healthy stretch; otherwise grow it so a
  # crash-looping child does not hammer the machine or burn usage.
  if [ "$ran_for" -ge 60 ]; then
    backoff="$min_backoff"
  fi

  log "restarting in ${backoff}s"
  slept=0
  while [ "$slept" -lt "$backoff" ]; do
    if [ "$stop_requested" -eq 1 ] || [ -f "$stop_file" ]; then
      break
    fi
    sleep 1
    slept=$(( slept + 1 ))
  done

  backoff=$(( backoff * 2 ))
  if [ "$backoff" -gt "$max_backoff" ]; then
    backoff="$max_backoff"
  fi
done

rm -f "$stop_file"
log "supervisor stopped"
