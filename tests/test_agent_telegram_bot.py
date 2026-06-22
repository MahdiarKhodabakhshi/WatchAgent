"""Unit tests for scripts/agent-telegram-bot.py.

The bot is loaded by file path because scripts/ is not an importable package.
File-writing commands are redirected to a tmp directory via the module globals,
and subprocess execution is replaced with a fake runner, so these tests touch
neither the network nor the real workflow scripts.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent-telegram-bot.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_telegram_bot", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bot = _load_module()


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect the bot's filesystem globals into a temp repo root."""
    root = tmp_path
    state = root / ".agent" / "state"
    inbox = root / ".agent" / "inbox"
    monkeypatch.setattr(bot, "ROOT_DIR", root)
    monkeypatch.setattr(bot, "STATE_DIR", state)
    monkeypatch.setattr(bot, "INBOX_DIR", inbox)
    monkeypatch.setattr(bot, "PAUSE_FILE", state / "paused")
    monkeypatch.setattr(bot, "OFFSET_FILE", state / "telegram-offset.json")
    return root


class FakeRunner:
    def __init__(self, returncode=0, output="ok"):
        self.returncode = returncode
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(args)
        return self.returncode, self.output


# --------------------------------------------------------------------------- #
# parse_command
# --------------------------------------------------------------------------- #
def test_parse_command_basic():
    assert bot.parse_command("/status") == ("/status", "")
    assert bot.parse_command("/approve TASK-foo") == ("/approve", "TASK-foo")


def test_parse_command_strips_mention_and_lowercases():
    assert bot.parse_command("/Status@MyBot") == ("/status", "")
    assert bot.parse_command("/reject@bot TASK-x bad idea") == ("/reject", "TASK-x bad idea")


def test_parse_command_non_command():
    assert bot.parse_command("hello there") is None
    assert bot.parse_command("") is None


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_parse_env_file_handles_comments_and_quotes():
    text = '# comment\nTELEGRAM_BOT_TOKEN="abc"\nTELEGRAM_ALLOWED_CHAT_IDS=1,2\n\nbad line\n'
    values = bot.parse_env_file(text)
    assert values == {"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_ALLOWED_CHAT_IDS": "1,2"}


def test_load_config_from_env(tmp_path):
    token, allowed = bot.load_config(
        {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_CHAT_IDS": "10, 20"},
        env_file=tmp_path / "missing.env",
    )
    assert token == "t"
    assert allowed == {10, 20}


def test_load_config_requires_token(tmp_path):
    with pytest.raises(bot.ConfigError):
        bot.load_config({"TELEGRAM_ALLOWED_CHAT_IDS": "1"}, env_file=tmp_path / "x.env")


def test_load_config_requires_allowlist(tmp_path):
    with pytest.raises(bot.ConfigError):
        bot.load_config({"TELEGRAM_BOT_TOKEN": "t"}, env_file=tmp_path / "x.env")


def test_load_config_rejects_bad_chat_id(tmp_path):
    with pytest.raises(bot.ConfigError):
        bot.load_config(
            {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_ALLOWED_CHAT_IDS": "notanint"},
            env_file=tmp_path / "x.env",
        )


def test_load_config_env_overrides_file(tmp_path):
    env_file = tmp_path / "telegram.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=fromfile\nTELEGRAM_ALLOWED_CHAT_IDS=99\n")
    token, allowed = bot.load_config({"TELEGRAM_BOT_TOKEN": "fromenv"}, env_file=env_file)
    assert token == "fromenv"
    assert allowed == {99}


# --------------------------------------------------------------------------- #
# handle_update / allowlist
# --------------------------------------------------------------------------- #
def _update(chat_id, text, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def test_handle_update_rejects_unauthorized_chat():
    runner = FakeRunner()
    dispatcher = bot.Dispatcher(runner=runner)
    replies: list[tuple[int, str]] = []
    bot.handle_update(_update(999, "/status"), dispatcher, {1}, lambda c, t: replies.append((c, t)))
    assert replies == []
    assert runner.calls == []


def test_handle_update_dispatches_authorized():
    runner = FakeRunner(output="status text")
    dispatcher = bot.Dispatcher(runner=runner)
    replies: list[tuple[int, str]] = []
    bot.handle_update(_update(1, "/status"), dispatcher, {1}, lambda c, t: replies.append((c, t)))
    assert len(replies) == 1
    assert replies[0][0] == 1
    assert "status text" in replies[0][1]
    assert runner.calls and runner.calls[0][0].endswith("agent-status.sh")


def test_handle_update_unknown_command_returns_help():
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    replies: list[tuple[int, str]] = []
    update = _update(1, "/frobnicate")
    bot.handle_update(update, dispatcher, {1}, lambda c, t: replies.append((c, t)))
    assert "Available" in replies[0][1]


def test_handle_update_ignores_non_message_update():
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    replies: list[tuple[int, str]] = []
    bot.handle_update({"update_id": 5}, dispatcher, {1}, lambda c, t: replies.append((c, t)))
    assert replies == []


# --------------------------------------------------------------------------- #
# dispatcher commands
# --------------------------------------------------------------------------- #
def test_approve_valid_id_runs_script():
    runner = FakeRunner(output="Approved TASK-foo")
    dispatcher = bot.Dispatcher(runner=runner)
    out = dispatcher.approve("TASK-foo", 1)
    assert "Approved" in out
    assert runner.calls[0][-1] == "TASK-foo"
    assert "--allow-high-risk" not in runner.calls[0]


def test_approve_allow_high_risk_passthrough():
    runner = FakeRunner()
    dispatcher = bot.Dispatcher(runner=runner)
    dispatcher.approve("TASK-foo --allow-high-risk", 1)
    assert "--allow-high-risk" in runner.calls[0]


def test_approve_invalid_id_does_not_run():
    runner = FakeRunner()
    dispatcher = bot.Dispatcher(runner=runner)
    out = dispatcher.approve("not-a-task; rm -rf /", 1)
    assert "Invalid task id" in out
    assert runner.calls == []


def test_reject_requires_reason():
    runner = FakeRunner()
    dispatcher = bot.Dispatcher(runner=runner)
    out = dispatcher.reject("TASK-foo", 1)
    assert "Usage" in out
    assert runner.calls == []


def test_reject_passes_task_and_reason():
    runner = FakeRunner(output="Rejected TASK-foo")
    dispatcher = bot.Dispatcher(runner=runner)
    dispatcher.reject("TASK-foo not needed anymore", 1)
    assert runner.calls[0][-2:] == ["TASK-foo", "not needed anymore"]


def test_pause_and_resume(tmp_state):
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    out = dispatcher.pause("", 1)
    assert "paused" in out.lower()
    assert bot.PAUSE_FILE.exists()

    out = dispatcher.resume("", 1)
    assert "resumed" in out.lower()
    assert not bot.PAUSE_FILE.exists()

    # Resuming again is a no-op, not an error.
    assert "not paused" in dispatcher.resume("", 1).lower()


def test_task_writes_inbox(tmp_state):
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    out = dispatcher.task("add retry logic to the poller", 1)
    files = list((tmp_state / ".agent" / "inbox").glob("*-task.md"))
    assert len(files) == 1
    assert "add retry logic" in files[0].read_text()
    assert ".agent/inbox" in out


def test_goal_writes_inbox(tmp_state):
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    dispatcher.goal("focus on test coverage this week", 1)
    files = list((tmp_state / ".agent" / "inbox").glob("*-goal.md"))
    assert len(files) == 1
    assert "test coverage" in files[0].read_text()


def test_task_requires_text(tmp_state):
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    assert "Usage" in dispatcher.task("   ", 1)


def test_help_lists_commands():
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    out = dispatcher.help("", 1)
    assert "/status" in out and "/details TASK-ID" in out


def test_details_reads_task_file(tmp_state):
    import json as _json

    tasks = tmp_state / ".agent" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "TASK-foo.json").write_text(
        _json.dumps(
            {
                "task_id": "TASK-foo",
                "title": "Do a thing",
                "status": "approved",
                "risk": "low",
                "area": "backend",
                "approved_by": "human",
                "objective": "make it work",
            }
        )
    )
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    out = dispatcher.details("TASK-foo", 1)
    assert "Do a thing" in out
    assert "Status: approved" in out
    assert "Latest review: none" in out


def test_details_missing_task(tmp_state):
    (tmp_state / ".agent" / "tasks").mkdir(parents=True)
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    assert "not found" in dispatcher.details("TASK-nope", 1).lower()


def test_details_invalid_id(tmp_state):
    dispatcher = bot.Dispatcher(runner=FakeRunner())
    assert "Invalid task id" in dispatcher.details("../etc/passwd", 1)


# --------------------------------------------------------------------------- #
# offset persistence
# --------------------------------------------------------------------------- #
def test_offset_roundtrip(tmp_state):
    assert bot.read_offset() is None
    bot.write_offset(42)
    assert bot.read_offset() == 42


def test_read_offset_invalid_json(tmp_state):
    bot.STATE_DIR.mkdir(parents=True, exist_ok=True)
    bot.OFFSET_FILE.write_text("not json")
    assert bot.read_offset() is None
