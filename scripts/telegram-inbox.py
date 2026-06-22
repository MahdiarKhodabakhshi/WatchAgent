#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT_DIR / ".agent"
TELEGRAM_INBOX_DIR = AGENT_DIR / "inbox" / "telegram"
STATE_DIR = AGENT_DIR / "state"
OFFSET_PATH = STATE_DIR / "telegram-offset.json"
SUPPORTED_COMMANDS = {"/status", "/approve", "/reject", "/pause", "/resume", "/goal", "/task", "/details", "/help"}
HELP_TEXT = (
    "Supported commands: /status, /approve TASK-ID, "
    "/reject TASK-ID reason, /pause, /resume, /goal text..., "
    "/task text..., /details TASK-ID, /help"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def ensure_dirs() -> None:
    TELEGRAM_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def write_local_message(name: str, content: str) -> Path:
    ensure_dirs()
    path = TELEGRAM_INBOX_DIR / f"{name}-{file_timestamp()}.md"
    path.write_text(content, encoding="utf-8")
    return path


def load_offset() -> int | None:
    try:
        data = json.loads(OFFSET_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    offset = data.get("offset") if isinstance(data, dict) else None
    return offset if isinstance(offset, int) else None


def save_offset(last_update_id: int) -> None:
    ensure_dirs()
    data = {
        "last_update_id": last_update_id,
        "offset": last_update_id + 1,
        "updated_at": utc_now(),
    }
    OFFSET_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def telegram_api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response_body = response.read().decode("utf-8")
    data = json.loads(response_body)
    if not isinstance(data, dict):
        raise RuntimeError("Telegram response was not an object.")
    return data


def store_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def update_chat_id(update: dict[str, Any]) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    return str(chat_id)


def update_text(update: dict[str, Any]) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    return text if isinstance(text, str) else None


def store_foreign_update(update: dict[str, Any], reason: str) -> None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return
    message = update.get("message")
    chat_id = update_chat_id(update)
    safe_record: dict[str, Any] = {
        "update_id": update_id,
        "rejected_at": utc_now(),
        "reason": reason,
        "chat_id_present": chat_id is not None,
    }
    if isinstance(message, dict) and isinstance(message.get("date"), int):
        safe_record["message_date"] = message["date"]
    store_json(TELEGRAM_INBOX_DIR / f"rejected-update-{update_id}.json", safe_record)


def parse_command(text: str) -> tuple[str | None, list[str], str | None]:
    stripped = text.strip()
    if not stripped:
        return None, [], HELP_TEXT
    command = stripped.split(maxsplit=1)[0]
    if command not in SUPPORTED_COMMANDS:
        return None, [], HELP_TEXT
    rest = stripped[len(command) :].strip()
    if command in {"/status", "/pause", "/resume", "/help"}:
        return command, rest.split() if rest else [], None
    if command == "/approve":
        return command, rest.split() if rest else [], None
    if command == "/reject":
        parts = rest.split(maxsplit=1)
        return command, parts, None
    if command in {"/goal", "/task"}:
        return command, [rest] if rest else [], None
    if command == "/details":
        return command, rest.split() if rest else [], None
    return None, [], HELP_TEXT


def dispatch_command(command: str, args: list[str]) -> tuple[int, str]:
    dispatcher = ROOT_DIR / "scripts" / "agent-command-dispatch.py"
    result = subprocess.run(
        [sys.executable, str(dispatcher), command, *args],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return result.returncode, output.strip()


def write_response(update_id: int, text: str) -> None:
    path = TELEGRAM_INBOX_DIR / f"response-{update_id}.md"
    path.write_text(
        "\n".join(
            [
                f"# Telegram Response {update_id}",
                "",
                f"- Written at: {utc_now()}",
                "",
                text,
                "",
            ]
        ),
        encoding="utf-8",
    )


def send_response(text: str) -> None:
    sender = ROOT_DIR / "scripts" / "telegram-send.sh"
    if not sender.exists():
        return
    subprocess.run([str(sender), text], cwd=ROOT_DIR, text=True, capture_output=True, check=False)


def process_update(update: dict[str, Any], allowed_chat_id: str) -> None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return

    chat_id = update_chat_id(update)
    if chat_id != allowed_chat_id:
        store_foreign_update(update, "foreign_chat")
        save_offset(update_id)
        return

    text = update_text(update)
    if text is None:
        store_foreign_update(update, "missing_text")
        save_offset(update_id)
        return

    store_json(TELEGRAM_INBOX_DIR / f"update-{update_id}.json", update)
    command, args, help_text = parse_command(text)
    if command is None:
        response = help_text or HELP_TEXT
        write_response(update_id, response)
        send_response(response)
        save_offset(update_id)
        return

    _, output = dispatch_command(command, args)
    response = output or "Command processed."
    write_response(update_id, response)
    send_response(response)
    save_offset(update_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll Telegram once for strict local agent commands.")
    parser.add_argument("--once", action="store_true", help="Poll once and exit. This is the default behavior.")
    return parser


def main(argv: list[str]) -> int:
    build_parser().parse_args(argv[1:])
    ensure_dirs()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        path = write_local_message(
            "telegram-inbox-skipped",
            "\n".join(
                [
                    "# Telegram Inbox Skipped",
                    "",
                    f"- Timestamp: {utc_now()}",
                    "- Reason: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured.",
                    "",
                ]
            ),
        )
        print(f"Telegram inbox skipped; missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Wrote {path}")
        return 0

    payload: dict[str, Any] = {
        "limit": 50,
        "timeout": 0,
        "allowed_updates": ["message"],
    }
    offset = load_offset()
    if offset is not None:
        payload["offset"] = offset

    try:
        response = telegram_api(token, "getUpdates", payload)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        path = write_local_message(
            "telegram-inbox-error",
            "\n".join(
                [
                    "# Telegram Inbox Error",
                    "",
                    f"- Timestamp: {utc_now()}",
                    f"- Error type: {exc.__class__.__name__}",
                    "- Note: Telegram getUpdates failed; no token was printed or written.",
                    "",
                ]
            ),
        )
        print(f"Telegram inbox failed gracefully. Wrote {path}")
        return 0

    if response.get("ok") is not True:
        path = write_local_message(
            "telegram-inbox-error",
            "\n".join(
                [
                    "# Telegram Inbox Error",
                    "",
                    f"- Timestamp: {utc_now()}",
                    "- Error type: Telegram response ok=false",
                    "- Note: Telegram getUpdates failed; no token was printed or written.",
                    "",
                ]
            ),
        )
        print(f"Telegram inbox failed gracefully. Wrote {path}")
        return 0

    updates = response.get("result")
    if not isinstance(updates, list):
        print("Telegram inbox returned no update list.")
        return 0

    for update in updates:
        if isinstance(update, dict):
            process_update(update, chat_id)

    print(f"Telegram inbox processed {len(updates)} update(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
