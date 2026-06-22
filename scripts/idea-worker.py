#!/usr/bin/env python3
"""Claude-only idea worker.

Advances ideas in .agent/ideas/IDEA-*.json through their lifecycle, running
Claude (never Codex) for planning and implementation and reporting back over
Telegram. The Telegram bot only flips fields on these files; this worker does
the actual work, so the bot's poll loop never blocks on a long Claude run.

Lifecycle (state + a `paused` overlay):
    new       -> plan with Claude        -> planned   (sends plan for confirmation)
    planned   -> (human /confirm)        -> approved
    approved  -> implement with Claude   -> done | paused | failed
    paused    -> (human /continue clears `paused`) -> resumes the same stage
    done | rejected | failed             -> terminal

On a usage/session limit the worker checkpoints, estimates when the next session
starts, messages the human, and waits. /continue clears `paused` and the worker
resumes the captured Claude session (--resume), falling back to a fresh run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
IDEAS_DIR = ROOT_DIR / ".agent" / "ideas"
LOG_DIR = ROOT_DIR / ".agent" / "logs"

TERMINAL_STATES = {"done", "rejected", "failed"}
SAFE_IDEA_ID_RE = re.compile(r"^IDEA-[0-9]+$")
DEFAULT_WINDOW_HOURS = float(os.environ.get("CLAUDE_USAGE_WINDOW_HOURS", "5"))


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_delta(now: datetime, then: datetime) -> str:
    seconds = int((then - now).total_seconds())
    if seconds <= 0:
        return "now"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def parse_resume_after(
    text: str, now: datetime, window_hours: float = DEFAULT_WINDOW_HOURS
) -> tuple[datetime, str]:
    """Best-effort estimate of when Claude's limit resets.

    Tries, in order: an explicit epoch/ISO time, a clock time ("resets at 3pm"),
    a relative delay ("try again in 2 hours"); otherwise falls back to
    now + window_hours. The result is advisory — /continue works regardless.
    """
    blob = text or ""

    # 1) Unix epoch near a reset/again hint.
    m = re.search(r"(?:reset|again|retry)[^0-9]{0,40}(1[0-9]{9})", blob, re.IGNORECASE)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc), "epoch"
        except (ValueError, OverflowError, OSError):
            pass

    # 2) ISO 8601 timestamp.
    m = re.search(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)", blob
    )
    if m:
        raw = m.group(1).replace(" ", "T")
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt, "iso"
        except ValueError:
            pass

    # 3) Relative delay: "in 45 minutes", "in 2 hours".
    m = re.search(r"\bin\s+(\d+)\s*(second|minute|hour)s?\b", blob, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
        }[unit]
        return now + delta, "relative"

    # 4) Clock time: "resets at 3pm", "available again at 10:30 pm".
    m = re.search(
        r"(?:reset|again|available|try)[^0-9]{0,40}(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        blob,
        re.IGNORECASE,
    )
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            local_now = now.astimezone()
            candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= local_now:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc), "clock"

    return now + timedelta(hours=window_hours), "fallback"


def decide_action(idea: dict) -> str | None:
    """What stage of work, if any, an idea needs next. Ignores paused/terminal."""
    state = idea.get("state")
    if state == "new":
        return "plan"
    if state == "approved":
        return "implement"
    return None


def is_waiting(idea: dict, now: datetime, auto_continue: bool) -> bool:
    """True if a paused idea should be skipped this pass."""
    if not idea.get("paused"):
        return False
    if auto_continue:
        ra = idea.get("resume_after")
        if isinstance(ra, str):
            try:
                due = datetime.fromisoformat(ra.replace("Z", "+00:00"))
                if now >= due:
                    return False
            except ValueError:
                pass
    return True


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def load_ideas() -> list[dict]:
    if not IDEAS_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in IDEAS_DIR.glob("IDEA-*.json"):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and SAFE_IDEA_ID_RE.fullmatch(str(data.get("id", ""))):
            out.append(data)
    out.sort(key=lambda d: int(str(d["id"]).split("-", 1)[1]))
    return out


def save_idea(idea: dict) -> None:
    IDEAS_DIR.mkdir(parents=True, exist_ok=True)
    idea["updated_at"] = iso(utc_now())
    path = IDEAS_DIR / f"{idea['id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idea, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def send_telegram(message: str) -> None:
    sender = SCRIPTS_DIR / "telegram-send.sh"
    if not sender.exists():
        return
    try:
        subprocess.run([str(sender), message], cwd=str(ROOT_DIR), check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


def git_change_summary() -> str:
    def run(args: list[str]) -> str:
        try:
            return subprocess.run(
                args, cwd=str(ROOT_DIR), capture_output=True, text=True, check=False
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    subject = run(["git", "log", "-1", "--pretty=%s"])
    stat = run(["git", "show", "--stat", "--format=", "HEAD"])
    lines = [ln for ln in stat.splitlines() if ln.strip()][:8]
    parts = []
    if subject:
        parts.append(f"Latest commit: {subject}")
    if lines:
        parts.append("\n".join(lines))
    return "\n".join(parts)


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def do_plan(idea: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    idea_id = idea["id"]
    log_path = LOG_DIR / f"claude-planner-{idea_id}-{timestamp()}.log"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(str(idea.get("text", "")))
        text_file = tf.name
    plan_out = tempfile.NamedTemporaryFile("r", suffix=".md", delete=False).name

    try:
        result = subprocess.run(
            [
                str(SCRIPTS_DIR / "claude-planner.sh"),
                "--text-file", text_file,
                "--out", plan_out,
                "--log", str(log_path),
            ],
            cwd=str(ROOT_DIR),
            check=False,
        )
        code = result.returncode
        log_text = _read(log_path)

        if code == 0:
            plan = _read(Path(plan_out)).strip()
            idea["state"] = "planned"
            idea["plan"] = plan
            save_idea(idea)
            send_telegram(
                f"{idea_id} — plan ready\n\n{plan}\n\n"
                f"Reply /confirm {idea_id} to build it, or /cancel {idea_id}."
            )
        else:
            _handle_limit_or_fail(idea, code, log_text, stage="planning")
    finally:
        _unlink(text_file, plan_out)


def do_implement(idea: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    idea_id = idea["id"]
    log_path = LOG_DIR / f"claude-idea-implement-{idea_id}-{timestamp()}.log"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write(str(idea.get("text", "")))
        text_file = tf.name
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as pf:
        pf.write(str(idea.get("plan", "")))
        plan_file = pf.name
    session_out = tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False).name

    cmd = [
        str(SCRIPTS_DIR / "claude-idea-implement.sh"),
        "--id", idea_id,
        "--text-file", text_file,
        "--plan-file", plan_file,
        "--session-out", session_out,
        "--log", str(log_path),
    ]
    if idea.get("resume") and idea.get("session_id"):
        cmd += ["--resume", str(idea["session_id"])]

    try:
        code = subprocess.run(cmd, cwd=str(ROOT_DIR), check=False).returncode
        log_text = _read(log_path)
        session_id = _read(Path(session_out)).strip()
        if session_id:
            idea["session_id"] = session_id

        if code == 0:
            idea["state"] = "done"
            idea["resume"] = False
            save_idea(idea)
            summary = git_change_summary()
            send_telegram(f"{idea_id} — done\n{summary}".strip())
        else:
            _handle_limit_or_fail(idea, code, log_text, stage="implementation", resumable=True)
    finally:
        _unlink(text_file, plan_file, session_out)


def _handle_limit_or_fail(
    idea: dict, code: int, log_text: str, stage: str, resumable: bool = False
) -> None:
    idea_id = idea["id"]
    if code == 20:  # usage / session limit
        now = utc_now()
        resume_after, _ = parse_resume_after(log_text, now)
        idea["paused"] = True
        idea["pause_reason"] = "usage_limit"
        idea["resume_after"] = iso(resume_after)
        if resumable:
            idea["resume"] = True
        save_idea(idea)
        local = resume_after.astimezone().strftime("%H:%M")
        send_telegram(
            f"{idea_id} — paused (usage limit during {stage}).\n"
            f"Estimated next session ~{local} local (in ~{human_delta(now, resume_after)}).\n"
            f"Send /continue {idea_id} around then — it works anytime."
        )
    elif code == 22:  # max turns: needs another session, but no waiting required
        idea["paused"] = True
        idea["pause_reason"] = "max_turns"
        if resumable:
            idea["resume"] = True
        save_idea(idea)
        send_telegram(
            f"{idea_id} — paused (needs another session to finish, {stage}).\n"
            f"Send /continue {idea_id} to keep going."
        )
    else:
        idea["state"] = "failed"
        idea["pause_reason"] = f"exit_{code}"
        save_idea(idea)
        send_telegram(f"{idea_id} — failed during {stage} (exit {code}). Check the worker log.")


def _read(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _unlink(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def run_once(auto_continue: bool) -> bool:
    """Advance at most one idea by one stage. Returns True if it did work."""
    now = utc_now()
    for idea in load_ideas():
        if idea.get("state") in TERMINAL_STATES:
            continue
        if is_waiting(idea, now, auto_continue):
            continue
        action = decide_action(idea)
        if action == "plan":
            do_plan(idea)
            return True
        if action == "implement":
            do_implement(idea)
            return True
    return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Claude-only idea worker.")
    parser.add_argument("--once", action="store_true", help="Advance one idea and exit.")
    parser.add_argument(
        "--sleep-seconds", type=int, default=20, help="Poll interval (forever mode)."
    )
    parser.add_argument(
        "--auto-continue",
        action="store_true",
        default=os.environ.get("IDEA_AUTO_CONTINUE") == "1",
        help="Auto-resume paused ideas once their estimated reset time passes.",
    )
    args = parser.parse_args(argv[1:])

    if args.once:
        run_once(args.auto_continue)
        return 0

    print(
        f"idea-worker started (sleep={args.sleep_seconds}s, auto_continue={args.auto_continue})",
        flush=True,
    )
    try:
        while True:
            try:
                run_once(args.auto_continue)
            except Exception as exc:  # noqa: BLE001 - never let one idea kill the worker
                print(f"idea-worker error: {exc}", file=sys.stderr, flush=True)
            time.sleep(max(5, args.sleep_seconds))
    except KeyboardInterrupt:
        print("idea-worker stopped by user.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
