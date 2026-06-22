#!/usr/bin/env python3
"""Telegram inbound command handler for the local two-agent workflow.

This bot is the human's remote control surface. It long-polls the Telegram
Bot API (``getUpdates``) so it needs no inbound port and no webhook. Only chat
IDs in an explicit allowlist may issue commands; everything else is logged and
ignored (fail closed).

Supported commands::

    /status                 Summarize branch, loop pause state, tasks, approvals.
    /approve TASK-ID        Approve a proposed task for implementation.
    /reject TASK-ID reason  Reject a proposed/blocked task with a reason.
    /pause                  Pause the autonomous loop before its next cycle.
    /resume                 Clear the pause flag.
    /task your request      Drop a task request into the planner inbox.
    /goal your direction    Drop a high-level goal into the planner inbox.

Configuration comes from the environment (optionally seeded from a gitignored
``.agent/telegram.env`` file)::

    TELEGRAM_BOT_TOKEN         Bot token from @BotFather (required).
    TELEGRAM_ALLOWED_CHAT_IDS  Comma-separated allowlist of chat IDs (required).

The token is never written to logs or replies.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
STATE_DIR = ROOT_DIR / ".agent" / "state"
INBOX_DIR = ROOT_DIR / ".agent" / "inbox"
IDEAS_DIR = ROOT_DIR / ".agent" / "ideas"
LOG_DIR = ROOT_DIR / ".agent" / "logs"
PAUSE_FILE = STATE_DIR / "paused"
OFFSET_FILE = STATE_DIR / "telegram-offset.json"
TELEGRAM_ENV_FILE = ROOT_DIR / ".agent" / "telegram.env"

SAFE_TASK_ID_RE = re.compile(r"^TASK-[A-Za-z0-9._-]+$")
SAFE_IDEA_ID_RE = re.compile(r"^IDEA-[0-9]+$")
MAX_REQUEST_CHARS = 4000

logger = logging.getLogger("agent-telegram-bot")


class ConfigError(Exception):
    """Raised when required bot configuration is missing or invalid."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def parse_env_file(text: str) -> dict[str, str]:
    """Parse a simple ``KEY=value`` env file. Ignores blanks and ``#`` comments."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(
    environ: dict[str, str] | None = None,
    env_file: Path = TELEGRAM_ENV_FILE,
) -> tuple[str, set[int]]:
    """Resolve the bot token and chat-ID allowlist.

    Environment variables take precedence over the optional env file. Raises
    ``ConfigError`` (fail closed) when the token or allowlist is missing.
    """
    environ = dict(os.environ if environ is None else environ)

    file_values: dict[str, str] = {}
    if env_file.is_file():
        file_values = parse_env_file(env_file.read_text(encoding="utf-8"))

    def resolve(key: str) -> str:
        value = environ.get(key)
        if value is None or value == "":
            value = file_values.get(key, "")
        return value.strip()

    token = resolve("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN is not set. Configure it in the environment or "
            ".agent/telegram.env."
        )

    raw_ids = resolve("TELEGRAM_ALLOWED_CHAT_IDS")
    allowed: set[int] = set()
    for piece in raw_ids.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            allowed.add(int(piece))
        except ValueError as exc:
            raise ConfigError(f"Invalid chat id in TELEGRAM_ALLOWED_CHAT_IDS: {piece!r}") from exc
    if not allowed:
        raise ConfigError(
            "TELEGRAM_ALLOWED_CHAT_IDS is empty. Refusing to start without an "
            "explicit allowlist."
        )

    return token, allowed


def load_token(
    environ: dict[str, str] | None = None,
    env_file: Path = TELEGRAM_ENV_FILE,
) -> str:
    """Resolve just the bot token (used by the allowlist-free discovery mode)."""
    environ = dict(os.environ if environ is None else environ)
    token = environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and env_file.is_file():
        token = parse_env_file(env_file.read_text(encoding="utf-8")).get(
            "TELEGRAM_BOT_TOKEN", ""
        ).strip()
    if not token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN is not set. Configure it in the environment or "
            ".agent/telegram.env."
        )
    return token


# --------------------------------------------------------------------------- #
# Telegram transport (stdlib only)
# --------------------------------------------------------------------------- #
class TelegramClient:
    """Thin Telegram Bot API client using urllib."""

    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        self._token = token
        self._base = f"{base_url}/bot{token}"

    def _call(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(f"{self._base}/{method}", data=data)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error for {method}: {payload.get('description')}")
        return payload.get("result")

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        # Network timeout must exceed the long-poll timeout.
        return self._call("getUpdates", params, timeout=timeout + 15)

    def send_message(self, chat_id: int, text: str) -> None:
        # Telegram caps messages at 4096 chars.
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True},
            timeout=30,
        )


# --------------------------------------------------------------------------- #
# Offset persistence
# --------------------------------------------------------------------------- #
def read_offset() -> int | None:
    try:
        data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    offset = data.get("offset")
    return offset if isinstance(offset, int) else None


def write_offset(offset: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the richer record shape so the file stays self-describing and
    # compatible with any other tool that reads last_update_id/updated_at.
    data = {
        "last_update_id": offset - 1,
        "offset": offset,
        "updated_at": _utc_now(),
    }
    OFFSET_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Command dispatch
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CommandRunner = Callable[[list[str]], tuple[int, str]]


# --------------------------------------------------------------------------- #
# Idea pipeline state (Claude-only idea -> plan -> confirm -> implement)
# Each idea is a JSON file .agent/ideas/IDEA-<n>.json. The bot only flips fields;
# scripts/idea-worker.py runs Claude and advances the work.
# --------------------------------------------------------------------------- #
def idea_path(idea_id: str) -> Path:
    return IDEAS_DIR / f"{idea_id}.json"


def read_idea(idea_id: str) -> dict[str, Any] | None:
    path = idea_path(idea_id)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_idea(data: dict[str, Any]) -> None:
    IDEAS_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _utc_now()
    path = idea_path(data["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def list_ideas() -> list[dict[str, Any]]:
    if not IDEAS_DIR.is_dir():
        return []
    ideas: list[dict[str, Any]] = []
    for path in IDEAS_DIR.glob("IDEA-*.json"):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and SAFE_IDEA_ID_RE.fullmatch(str(data.get("id", ""))):
            ideas.append(data)
    ideas.sort(key=lambda d: int(str(d["id"]).split("-", 1)[1]))
    return ideas


def next_idea_id() -> str:
    highest = 0
    for idea in list_ideas():
        highest = max(highest, int(str(idea["id"]).split("-", 1)[1]))
    return f"IDEA-{highest + 1}"


def default_runner(args: list[str]) -> tuple[int, str]:
    """Run a workflow script and capture combined output."""
    completed = subprocess.run(
        args,
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip()


class Dispatcher:
    """Maps parsed commands onto workflow scripts and state files."""

    def __init__(
        self,
        runner: CommandRunner = default_runner,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self._run = runner
        self._now = now

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _valid_task_id(task_id: str) -> bool:
        return bool(SAFE_TASK_ID_RE.fullmatch(task_id)) and task_id != "TASK-TEMPLATE"

    def _write_inbox(self, kind: str, chat_id: int, text: str) -> Path:
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = INBOX_DIR / f"{stamp}-{kind}.md"
        body = (
            f"# Human {kind} request\n\n"
            f"- Received at: {self._now()}\n"
            f"- Source: telegram\n"
            f"- Chat id: {chat_id}\n\n"
            f"{text.strip()}\n"
        )
        path.write_text(body, encoding="utf-8")
        return path

    # -- commands --------------------------------------------------------- #
    def status(self, _args: str, _chat_id: int) -> str:
        code, output = self._run([str(SCRIPTS_DIR / "agent-status.sh")])
        if code != 0:
            return f"status failed (exit {code}):\n{output}"
        return output or "No status output."

    def approve(self, args: str, _chat_id: int) -> str:
        tokens = args.split()
        if not tokens:
            return "Usage: /approve TASK-ID [--allow-high-risk]"
        task_id = tokens[0]
        if not self._valid_task_id(task_id):
            return f"Invalid task id: {task_id}"
        cmd = [str(SCRIPTS_DIR / "agent-approve.sh")]
        if "--allow-high-risk" in tokens[1:]:
            cmd.append("--allow-high-risk")
        cmd.append(task_id)
        code, output = self._run(cmd)
        prefix = "Approved" if code == 0 else f"Approve failed (exit {code})"
        return f"{prefix}:\n{output}"

    def reject(self, args: str, _chat_id: int) -> str:
        tokens = args.split(maxsplit=1)
        if len(tokens) < 2 or not tokens[1].strip():
            return "Usage: /reject TASK-ID reason"
        task_id, reason = tokens[0], tokens[1].strip()
        if not self._valid_task_id(task_id):
            return f"Invalid task id: {task_id}"
        code, output = self._run([str(SCRIPTS_DIR / "agent-reject.sh"), task_id, reason])
        prefix = "Rejected" if code == 0 else f"Reject failed (exit {code})"
        return f"{prefix}:\n{output}"

    def pause(self, args: str, chat_id: int) -> str:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        reason = args.strip() or "Paused via Telegram"
        PAUSE_FILE.write_text(
            f"{reason}\nPaused at {self._now()} by chat {chat_id}\n", encoding="utf-8"
        )
        return "Loop paused. It will not start a new cycle until /resume."

    def resume(self, _args: str, _chat_id: int) -> str:
        if PAUSE_FILE.exists():
            PAUSE_FILE.unlink()
            return "Loop resumed."
        return "Loop was not paused."

    def task(self, args: str, chat_id: int) -> str:
        if not args.strip():
            return "Usage: /task your request"
        path = self._write_inbox("task", chat_id, args[:MAX_REQUEST_CHARS])
        return f"Task request queued for the planner: {path.relative_to(ROOT_DIR)}"

    def goal(self, args: str, chat_id: int) -> str:
        if not args.strip():
            return "Usage: /goal your high-level direction"
        path = self._write_inbox("goal", chat_id, args[:MAX_REQUEST_CHARS])
        return f"Goal recorded for the planner: {path.relative_to(ROOT_DIR)}"

    def details(self, args: str, _chat_id: int) -> str:
        tokens = args.split()
        if len(tokens) != 1:
            return "Usage: /details TASK-ID"
        task_id = tokens[0]
        if not self._valid_task_id(task_id):
            return f"Invalid task id: {task_id}"
        path = ROOT_DIR / ".agent" / "tasks" / f"{task_id}.json"
        if path.is_symlink() or not path.is_file():
            return f"Task not found: {task_id}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return f"Could not read task file for {task_id}."
        if not isinstance(data, dict):
            return f"Task file for {task_id} is not a JSON object."
        reviews = sorted((ROOT_DIR / ".agent" / "reviews").glob(f"REVIEW-{task_id}-*.json"))
        latest_review = reviews[-1].name if reviews else "none"
        objective = data.get("objective", "")
        objective = objective if isinstance(objective, str) else ""
        if len(objective) > 200:
            objective = objective[:197].rstrip() + "..."
        return "\n".join(
            [
                f"Task: {task_id}",
                f"Title: {data.get('title', 'untitled')}",
                f"Status: {data.get('status')}",
                f"Risk: {data.get('risk', 'unknown')}",
                f"Area: {data.get('area', 'unknown')}",
                f"Approved by: {data.get('approved_by')}",
                f"Latest review: {latest_review}",
                f"Objective: {objective}",
            ]
        )

    def help(self, _args: str, _chat_id: int) -> str:
        return COMMAND_LIST

    # -- idea pipeline (Claude-only) ------------------------------------- #
    @staticmethod
    def _valid_idea_id(idea_id: str) -> bool:
        return bool(SAFE_IDEA_ID_RE.fullmatch(idea_id))

    def _resolve_idea(self, arg: str, want_states: set[str]) -> tuple[str | None, str]:
        """Resolve an idea id from an explicit arg, or the latest in want_states."""
        token = arg.split()[0] if arg.split() else ""
        if token:
            if not self._valid_idea_id(token):
                return None, f"Invalid idea id: {token}"
            if read_idea(token) is None:
                return None, f"Idea not found: {token}"
            return token, ""
        candidates = [i for i in list_ideas() if i.get("state") in want_states]
        if not candidates:
            return None, f"No idea is currently in state: {', '.join(sorted(want_states))}."
        return str(candidates[-1]["id"]), ""

    def idea(self, args: str, chat_id: int) -> str:
        text = args.strip()
        if not text:
            return "Usage: /idea your idea in plain words"
        idea_id = next_idea_id()
        write_idea(
            {
                "id": idea_id,
                "text": text[:MAX_REQUEST_CHARS],
                "state": "new",
                "paused": False,
                "chat_id": chat_id,
                "created_at": _utc_now(),
            }
        )
        return f"Got it — queued as {idea_id}. Planning it now; I'll send you the plan to confirm."

    def confirm(self, args: str, _chat_id: int) -> str:
        idea_id, err = self._resolve_idea(args, {"planned"})
        if err:
            return err
        data = read_idea(idea_id)
        if data is None:
            return f"Idea not found: {idea_id}"
        if data.get("state") != "planned":
            return f"{idea_id} is {data.get('state')}, not awaiting confirmation."
        data["state"] = "approved"
        data["resume"] = False
        data["paused"] = False
        write_idea(data)
        return f"Confirmed {idea_id}. Implementing it now; I'll report when it's done."

    def cancel(self, args: str, _chat_id: int) -> str:
        idea_id, err = self._resolve_idea(args, {"new", "planned", "approved"})
        if err:
            return err
        data = read_idea(idea_id)
        if data is None:
            return f"Idea not found: {idea_id}"
        if data.get("state") in {"done", "rejected"}:
            return f"{idea_id} is already {data.get('state')}."
        data["state"] = "rejected"
        data["paused"] = False
        write_idea(data)
        return f"Cancelled {idea_id}."

    def cont(self, args: str, _chat_id: int) -> str:
        idea_id, err = self._resolve_idea(args, {"new", "approved"})
        if err:
            # Most often the user means "resume the paused one".
            paused = [i for i in list_ideas() if i.get("paused")]
            if not args.split() and paused:
                idea_id = str(paused[-1]["id"])
            else:
                return err
        data = read_idea(idea_id)
        if data is None:
            return f"Idea not found: {idea_id}"
        if not data.get("paused"):
            return f"{idea_id} is not paused (state: {data.get('state')})."
        data["paused"] = False
        data.pop("resume_after", None)
        write_idea(data)
        return f"Resuming {idea_id} in a new session now."

    def ideas(self, _args: str, _chat_id: int) -> str:
        items = list_ideas()
        if not items:
            return "No ideas yet. Send one with /idea your idea."
        lines = ["Ideas:"]
        for i in items:
            if i.get("state") in {"done", "rejected"}:
                continue
            flag = " (paused)" if i.get("paused") else ""
            text = str(i.get("text", ""))[:48]
            lines.append(f"{i['id']}: {i.get('state')}{flag} — {text}")
        if len(lines) == 1:
            return "No active ideas. Send one with /idea your idea."
        return "\n".join(lines)


COMMANDS: dict[str, str] = {
    "/status": "status",
    "/approve": "approve",
    "/reject": "reject",
    "/pause": "pause",
    "/resume": "resume",
    "/task": "task",
    "/goal": "goal",
    "/details": "details",
    "/help": "help",
    "/idea": "idea",
    "/confirm": "confirm",
    "/cancel": "cancel",
    "/continue": "cont",
    "/ideas": "ideas",
}

COMMAND_LIST = (
    "Available commands:\n"
    "/status\n"
    "/approve TASK-ID\n"
    "/reject TASK-ID reason\n"
    "/pause\n"
    "/resume\n"
    "/task your request\n"
    "/goal your high-level direction\n"
    "/details TASK-ID\n"
    "/help\n"
    "\nClaude idea pipeline:\n"
    "/idea your idea — plan it and send back for confirmation\n"
    "/confirm [IDEA-n] — approve the plan; implement it\n"
    "/cancel [IDEA-n] — drop an idea\n"
    "/continue [IDEA-n] — resume after a usage limit\n"
    "/ideas — list active ideas"
)

HELP_TEXT = "Unknown command.\n" + COMMAND_LIST


def parse_command(text: str) -> tuple[str, str] | None:
    """Split message text into a normalized command and its argument string.

    Strips a trailing ``@botname`` mention. Returns ``None`` if the text is not
    a command (does not start with ``/``).
    """
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    command = head.split("@", 1)[0].lower()
    return command, rest.strip()


def handle_update(
    update: dict[str, Any],
    dispatcher: Dispatcher,
    allowed_ids: set[int],
    reply: Callable[[int, str], None],
) -> None:
    """Process one Telegram update: enforce allowlist, dispatch, reply."""
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str):
        return
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return

    if chat_id not in allowed_ids:
        logger.warning("Ignoring message from unauthorized chat id %s", chat_id)
        return

    parsed = parse_command(text)
    if parsed is None:
        reply(chat_id, HELP_TEXT)
        return

    command, args = parsed
    method_name = COMMANDS.get(command)
    if method_name is None:
        reply(chat_id, HELP_TEXT)
        return

    logger.info("Handling %s from chat %s", command, chat_id)
    try:
        response = getattr(dispatcher, method_name)(args, chat_id)
    except Exception:  # noqa: BLE001 - never let one command kill the bot
        logger.exception("Command %s failed", command)
        response = f"Command {command} failed with an internal error. Check the bot log."
    reply(chat_id, response)


# --------------------------------------------------------------------------- #
# Polling loop
# --------------------------------------------------------------------------- #
def run_loop(
    client: TelegramClient,
    dispatcher: Dispatcher,
    allowed_ids: set[int],
    poll_timeout: int = 30,
    once: bool = False,
) -> None:
    offset = read_offset()
    logger.info("Bot started. Allowlist size=%d, starting offset=%s", len(allowed_ids), offset)

    def reply(chat_id: int, text: str) -> None:
        try:
            client.send_message(chat_id, text)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send reply to chat %s", chat_id)

    while True:
        try:
            updates = client.get_updates(offset, poll_timeout)
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            logger.warning("getUpdates failed: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            try:
                handle_update(update, dispatcher, allowed_ids, reply)
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error handling update %s", update_id)
            if isinstance(update_id, int):
                write_offset(offset)

        if once:
            return


def print_chat_ids(client: TelegramClient, poll_timeout: int = 10) -> int:
    """Bootstrap helper: print chat IDs of incoming messages.

    Token-only, requires no allowlist, and never dispatches commands or
    advances the stored offset. Send your bot a message, then run this to learn
    the chat ID to put in TELEGRAM_ALLOWED_CHAT_IDS.
    """
    try:
        updates = client.get_updates(read_offset(), poll_timeout)
    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
        logger.error("getUpdates failed: %s", exc)
        return 1

    if not updates:
        print("No messages received. Send your bot a message, then run this again.")
        return 0

    print("Chat IDs of recent messages (add the right one to TELEGRAM_ALLOWED_CHAT_IDS):")
    seen: set[int] = set()
    for update in updates:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue
        chat_id = chat.get("id")
        if not isinstance(chat_id, int) or chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("username") or chat.get("title") or chat.get("first_name") or ""
        preview = (message.get("text") or "")[:40]
        print(f"  chat_id={chat_id}  type={chat.get('type')}  name={name}  text={preview!r}")
    if not seen:
        print("Received updates but none carried a chat id.")
    return 0


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(LOG_DIR / f"agent-telegram-bot-{stamp}.log"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Telegram inbound command handler.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Drain pending updates once and exit (useful for testing).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=30,
        help="Long-poll timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--print-chat-ids",
        action="store_true",
        help="Bootstrap: poll once and print incoming chat IDs, then exit. "
        "Needs only TELEGRAM_BOT_TOKEN; does not run commands or advance the offset.",
    )
    args = parser.parse_args(argv[1:])

    configure_logging()

    if args.print_chat_ids:
        try:
            token = load_token()
        except ConfigError as exc:
            logger.error("%s", exc)
            return 2
        return print_chat_ids(TelegramClient(token))

    try:
        token, allowed_ids = load_config()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    client = TelegramClient(token)
    dispatcher = Dispatcher()
    try:
        run_loop(client, dispatcher, allowed_ids, poll_timeout=args.poll_timeout, once=args.once)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
