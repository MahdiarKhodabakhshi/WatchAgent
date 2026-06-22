#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MAX_TELEGRAM_CHARS=900
NOTIFICATION_DIR=".agent/notifications"
SEND_LOG="${NOTIFICATION_DIR}/telegram-send.md"

mkdir -p "$NOTIFICATION_DIR"

timestamp() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

append_local_note() {
  local status="$1"
  local note="$2"
  local preview="$3"
  {
    printf '## Telegram Send %s\n\n' "$(timestamp)"
    printf '%s\n' "- Status: $status"
    printf '%s\n' "- Note: $note"
    if [ -n "$preview" ]; then
      printf '%s\n' "- Message preview: $preview"
    fi
    printf '\n'
  } >> "$SEND_LOG"
}

truncate_text() {
  local text="$1"
  if [ "${#text}" -gt "$MAX_TELEGRAM_CHARS" ]; then
    printf '%s...' "${text:0:$((MAX_TELEGRAM_CHARS - 3))}"
  else
    printf '%s' "$text"
  fi
}

message=""
if [ "$#" -gt 0 ]; then
  message="$*"
elif [ ! -t 0 ]; then
  message="$(cat)"
fi

message="$(truncate_text "$message")"
if [ -z "$message" ]; then
  message="Agent notification."
fi

preview="${message//$'\n'/ }"
preview="$(truncate_text "$preview")"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  append_local_note "skipped" "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured." "$preview"
  printf 'Telegram skipped; missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Wrote %s\n' "$SEND_LOG"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  append_local_note "skipped" "curl is not available." "$preview"
  printf 'Telegram skipped; curl is not available. Wrote %s\n' "$SEND_LOG"
  exit 0
fi

curl_output=""
if curl_output="$(
  curl -fsS \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    --data-urlencode "disable_web_page_preview=true" \
    2>&1
)"; then
  append_local_note "sent" "Telegram sendMessage completed." "$preview"
  printf 'Telegram message sent. Wrote %s\n' "$SEND_LOG"
  exit 0
fi

append_local_note "skipped" "Telegram sendMessage failed; local notification preserved." "$preview"
printf 'Telegram send failed gracefully. Wrote %s\n' "$SEND_LOG"
exit 0
