#!/usr/bin/env bash
# Best-effort outbound Telegram sender for agent notifications.
#
# Sends a message to every chat id in TELEGRAM_ALLOWED_CHAT_IDS using the bot
# token. Configuration comes from the environment, falling back to
# .agent/telegram.env (the same file the inbound bot uses). This script never
# fails the caller (always exits 0) and never prints the token.
#
# Usage:
#   scripts/telegram-send.sh "message text"
#   printf 'message text' | scripts/telegram-send.sh
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ "$#" -gt 0 ]; then
  message="$*"
else
  message="$(cat)"
fi

if [ -z "${message//[[:space:]]/}" ]; then
  exit 0
fi

TG_MESSAGE="$message" python3 - <<'PY' || true
import os
import json
import urllib.parse
import urllib.request
from pathlib import Path


def parse_env_file(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


env_file = Path(".agent/telegram.env")
file_values = parse_env_file(env_file.read_text(encoding="utf-8")) if env_file.is_file() else {}


def resolve(key):
    value = os.environ.get(key) or ""
    if not value:
        value = file_values.get(key, "")
    return value.strip()


token = resolve("TELEGRAM_BOT_TOKEN")
chats = resolve("TELEGRAM_ALLOWED_CHAT_IDS")
message = os.environ.get("TG_MESSAGE", "")

if not token or not chats or not message:
    raise SystemExit(0)

for chat_id in (c.strip() for c in chats.split(",")):
    if not chat_id:
        continue
    try:
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": message[:4096], "disable_web_page_preview": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except Exception:
        # Best effort: a delivery failure must never break the agent loop.
        pass
PY

exit 0
