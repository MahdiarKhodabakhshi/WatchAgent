#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT_DIR / ".agent"
TASK_DIR = AGENT_DIR / "tasks"
INBOX_DIR = AGENT_DIR / "inbox"
APPROVED_DIR = AGENT_DIR / "approvals" / "approved"
PENDING_DIR = AGENT_DIR / "approvals" / "pending"
REJECTED_DIR = AGENT_DIR / "approvals" / "rejected"

SAFE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_STATUSES = {
    "proposed",
    "approved",
    "rejected",
    "in_progress",
    "implemented",
    "needs_revision",
    "blocked",
}
ACTIONABLE_PRIORITY = {
    "needs_revision": 0,
    "in_progress": 1,
    "approved": 2,
}
HELP_TEXT = (
    "Supported commands: /status, /approve TASK-ID, "
    "/reject TASK-ID reason, /pause, /resume, /goal text..., "
    "/task text..., /details TASK-ID, /help"
)


class DispatchError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR))


def ensure_agent_path(path: Path) -> Path:
    agent_root = AGENT_DIR.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(agent_root)
    except ValueError as exc:
        raise DispatchError(f"Refusing path outside .agent/: {path}") from exc
    return path


def agent_path(*parts: str) -> Path:
    return ensure_agent_path(AGENT_DIR.joinpath(*parts))


def ensure_dir(path: Path) -> None:
    ensure_agent_path(path)
    if path.exists() and path.is_symlink():
        raise DispatchError(f"Refusing symlinked directory: {relative(path)}")
    path.mkdir(parents=True, exist_ok=True)


def validate_task_id(task_id: str) -> None:
    if not task_id or not SAFE_TASK_ID_RE.fullmatch(task_id):
        raise DispatchError(f"Unsafe task id: {task_id!r}")
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise DispatchError(f"Unsafe task id: {task_id!r}")


def task_path_for(task_id: str) -> Path:
    validate_task_id(task_id)
    path = agent_path("tasks", f"{task_id}.json")
    if path.parent != TASK_DIR:
        raise DispatchError(f"Refusing unsafe task path: {path}")
    return path


def load_task(task_id: str) -> tuple[Path, dict[str, Any]]:
    path = task_path_for(task_id)
    if path.is_symlink():
        raise DispatchError(f"Refusing symlinked task file: {relative(path)}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError(f"Task not found: {task_id}") from exc
    except json.JSONDecodeError as exc:
        raise DispatchError(f"Task JSON is invalid: {relative(path)} line {exc.lineno}") from exc
    if not isinstance(data, dict):
        raise DispatchError(f"Task JSON must be an object: {relative(path)}")
    actual_task_id = data.get("task_id")
    if actual_task_id != task_id:
        raise DispatchError(f"{relative(path)} has task_id {actual_task_id!r}, expected {task_id!r}")
    status = data.get("status")
    if status not in ALLOWED_STATUSES:
        raise DispatchError(f"Task {task_id} has invalid status {status!r}")
    return path, data


def infer_indent(raw: str) -> int | str:
    for line in raw.splitlines():
        match = re.match(r"^([ \t]+)\"", line)
        if not match:
            continue
        indent = match.group(1)
        if "\t" in indent:
            return "\t"
        return max(1, len(indent))
    return 2


def write_task(path: Path, data: dict[str, Any]) -> None:
    ensure_agent_path(path)
    if path.parent != TASK_DIR:
        raise DispatchError(f"Refusing to write outside .agent/tasks/: {path}")
    if path.is_symlink():
        raise DispatchError(f"Refusing symlinked task file: {relative(path)}")
    status = data.get("status")
    if status not in ALLOWED_STATUSES:
        raise DispatchError(f"Refusing to write invalid status {status!r}")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    path.write_text(
        json.dumps(data, indent=infer_indent(raw), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def truncate(text: str, limit: int = 900) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def append_command_log(command: str, args: list[str], exit_code: int, output: str) -> None:
    ensure_dir(INBOX_DIR)
    safe_args = list(args)
    if command == "/approve" and len(safe_args) > 1:
        safe_args[1:] = ["[TOKEN_REDACTED]"] * (len(safe_args) - 1)
    record = {
        "timestamp": utc_now(),
        "command": command,
        "args": safe_args,
        "exit_code": exit_code,
        "output": truncate(output, 1200),
    }
    log_path = agent_path("inbox", "command-log.jsonl")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def approval_markdown(task_id: str, task_path: Path, source: str = "telegram") -> str:
    return "\n".join(
        [
            f"# Approval: {task_id}",
            "",
            "- Approved by: human",
            f"- Approved at: {utc_now()}",
            f"- Source: {source}",
            f"- Task file: {relative(task_path)}",
            "",
        ]
    )


def append_approval_metadata(task_id: str) -> None:
    ensure_dir(APPROVED_DIR)
    path = agent_path("approvals", "approved", f"{task_id}.md")
    if path.exists() and path.is_symlink():
        raise DispatchError(f"Refusing symlinked approval file: {relative(path)}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                [
                    f"\n## Telegram Approval {utc_now()}",
                    "",
                    "- Source: telegram",
                    "",
                ]
            )
        )


def approve_internally(task_id: str) -> str:
    path, data = load_task(task_id)
    status = data.get("status")
    if status != "proposed":
        raise DispatchError(f"Task {task_id} has status {status!r}; only proposed tasks can be approved.")
    data["status"] = "approved"
    data["approved_by"] = "human"
    data["status_updated_at"] = utc_now()
    data["status_reason"] = "Approved from Telegram command."
    write_task(path, data)

    ensure_dir(APPROVED_DIR)
    approval_path = agent_path("approvals", "approved", f"{task_id}.md")
    if approval_path.exists() and approval_path.is_symlink():
        raise DispatchError(f"Refusing symlinked approval file: {relative(approval_path)}")
    approval_path.write_text(approval_markdown(task_id, path), encoding="utf-8")
    return f"Approved {task_id}. Updated {relative(path)} and wrote {relative(approval_path)}."


def approve_task(args: list[str]) -> str:
    if len(args) != 1:
        raise DispatchError("Usage: /approve TASK-ID")
    task_id = args[0]
    validate_task_id(task_id)

    path, data = load_task(task_id)
    helper = ROOT_DIR / "scripts" / "agent-approve.sh"
    if helper.exists() and os.access(helper, os.X_OK):
        cmd = [str(helper)]
        if data.get("risk") == "high":
            cmd.append("--allow-high-risk")
        cmd.append(task_id)
        result = subprocess.run(cmd, cwd=ROOT_DIR, text=True, capture_output=True, check=False)
        combined = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        if result.returncode != 0:
            raise DispatchError(combined or f"Approval helper failed for {task_id}.")
        append_approval_metadata(task_id)
        return truncate(combined or f"Approved {task_id}.")

    return approve_internally(task_id)


def reject_task(args: list[str]) -> str:
    if len(args) < 2:
        raise DispatchError("Usage: /reject TASK-ID reason")
    task_id = args[0]
    reason = " ".join(args[1:]).strip()
    if not reason:
        raise DispatchError("Reject reason is required.")
    path, data = load_task(task_id)
    data["status"] = "rejected"
    data["status_updated_at"] = utc_now()
    data["status_reason"] = reason
    write_task(path, data)

    ensure_dir(REJECTED_DIR)
    rejected_path = agent_path("approvals", "rejected", f"{task_id}.md")
    if rejected_path.exists() and rejected_path.is_symlink():
        raise DispatchError(f"Refusing symlinked rejected approval file: {relative(rejected_path)}")
    rejected_path.write_text(
        "\n".join(
            [
                f"# Rejection: {task_id}",
                "",
                "- Rejected by: human",
                f"- Rejected at: {utc_now()}",
                "- Source: telegram",
                f"- Task file: {relative(path)}",
                "",
                "## Reason",
                "",
                reason,
                "",
            ]
        ),
        encoding="utf-8",
    )

    pending_path = agent_path("approvals", "pending", f"{task_id}.md")
    if pending_path.exists():
        if pending_path.is_symlink():
            raise DispatchError(f"Refusing symlinked pending approval file: {relative(pending_path)}")
        pending_path.unlink()

    return f"Rejected {task_id}. Reason recorded in {relative(rejected_path)}."


def pause_loop(_: list[str]) -> str:
    paused_path = agent_path("PAUSED")
    paused_path.write_text(
        "\n".join(
            [
                "# Agent Loop Paused",
                "",
                f"- Paused at: {utc_now()}",
                "- Source: telegram",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return "Agent loop paused. .agent/PAUSED was created."


def resume_loop(_: list[str]) -> str:
    paused_path = agent_path("PAUSED")
    if paused_path.exists():
        if paused_path.is_symlink():
            raise DispatchError(f"Refusing symlinked pause file: {relative(paused_path)}")
        paused_path.unlink()
        return "Agent loop resumed. .agent/PAUSED was removed."
    return "Agent loop was not paused."


def write_human_inbox(kind: str, args: list[str]) -> str:
    if not args:
        raise DispatchError(f"Usage: /{kind} text...")
    text = " ".join(args).strip()
    if not text:
        raise DispatchError(f"Usage: /{kind} text...")
    ensure_dir(INBOX_DIR)
    path = agent_path("inbox", f"human-{kind}-{file_timestamp()}.md")
    path.write_text(
        "\n".join(
            [
                f"# Human {kind.title()}",
                "",
                f"- Received at: {utc_now()}",
                "- Source: telegram",
                "",
                text,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return f"Recorded {kind} in {relative(path)}."


def current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def iter_tasks() -> list[tuple[str, dict[str, Any], Path]]:
    records: list[tuple[str, dict[str, Any], Path]] = []
    if not TASK_DIR.exists():
        return records
    for path in sorted(TASK_DIR.glob("*.json")):
        if path.name == "TASK-TEMPLATE.json" or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        task_id = data.get("task_id")
        status = data.get("status")
        if not isinstance(task_id, str) or not SAFE_TASK_ID_RE.fullmatch(task_id):
            continue
        if status not in ALLOWED_STATUSES:
            continue
        records.append((task_id, data, path))
    return records


def next_actionable_task() -> str:
    candidates = []
    for task_id, data, _ in iter_tasks():
        status = data.get("status")
        if status in ACTIONABLE_PRIORITY:
            created_at = data.get("created_at") if isinstance(data.get("created_at"), str) else ""
            candidates.append((ACTIONABLE_PRIORITY[status], created_at, task_id))
    if not candidates:
        return "none"
    candidates.sort()
    return candidates[0][2]


def task_ids_with_status(status: str) -> list[str]:
    rows = []
    for task_id, data, _ in iter_tasks():
        if data.get("status") != status:
            continue
        updated_at = data.get("status_updated_at")
        if not isinstance(updated_at, str):
            updated_at = data.get("created_at") if isinstance(data.get("created_at"), str) else ""
        rows.append((updated_at, task_id))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [task_id for _, task_id in rows]


def summarize_task_ids(task_ids: list[str], limit: int = 5) -> str:
    if not task_ids:
        return "none"
    text = ", ".join(task_ids[:limit])
    if len(task_ids) > limit:
        text += f", +{len(task_ids) - limit} more"
    return text


def pending_approvals() -> list[str]:
    if not PENDING_DIR.exists():
        return []
    return [path.stem for path in sorted(PENDING_DIR.glob("*.md")) if not path.is_symlink() and path.name != ".gitkeep"]


def latest_notification() -> str:
    notification_dir = AGENT_DIR / "notifications"
    if not notification_dir.exists():
        return "none"
    files = [path for path in notification_dir.glob("*.md") if not path.is_symlink()]
    if not files:
        return "none"
    latest = max(files, key=lambda path: (path.stat().st_mtime_ns, path.name))
    return relative(latest)


def status_summary(_: list[str]) -> str:
    paused = "yes" if agent_path("PAUSED").exists() else "no"
    return "\n".join(
        [
            f"Branch: {current_branch()}",
            f"Paused: {paused}",
            f"Actionable task: {next_actionable_task()}",
            f"Pending proposed tasks: {summarize_task_ids(task_ids_with_status('proposed'))}",
            f"Implemented tasks: {summarize_task_ids(task_ids_with_status('implemented'))}",
            f"Last notification: {latest_notification()}",
        ]
    )


def task_details(args: list[str]) -> str:
    if len(args) != 1:
        raise DispatchError("Usage: /details TASK-ID")
    task_id = args[0]
    path, data = load_task(task_id)
    objective = data.get("objective", "")
    objective_text = truncate(objective if isinstance(objective, str) else "", 180)
    reviews = sorted((AGENT_DIR / "reviews").glob(f"REVIEW-{task_id}-*.json"))
    latest_review = relative(reviews[-1]) if reviews else "none"
    approval_state = []
    for label, directory in [("pending", PENDING_DIR), ("approved", APPROVED_DIR), ("rejected", REJECTED_DIR)]:
        candidate = directory / f"{task_id}.md"
        if candidate.exists() and not candidate.is_symlink():
            approval_state.append(label)
    approval_text = ", ".join(approval_state) if approval_state else "none"
    return "\n".join(
        [
            f"Task: {task_id}",
            f"Title: {data.get('title', 'untitled')}",
            f"Status: {data.get('status')}",
            f"Risk: {data.get('risk', 'unknown')}",
            f"Area: {data.get('area', 'unknown')}",
            f"Approved by: {data.get('approved_by')}",
            f"Approvals: {approval_text}",
            f"Latest review: {latest_review}",
            f"Task file: {relative(path)}",
            f"Objective: {objective_text}",
        ]
    )


def dispatch(command: str, args: list[str]) -> str:
    if command == "/status":
        if args:
            raise DispatchError("Usage: /status")
        return status_summary(args)
    if command == "/approve":
        return approve_task(args)
    if command == "/reject":
        return reject_task(args)
    if command == "/pause":
        if args:
            raise DispatchError("Usage: /pause")
        return pause_loop(args)
    if command == "/resume":
        if args:
            raise DispatchError("Usage: /resume")
        return resume_loop(args)
    if command == "/goal":
        return write_human_inbox("goal", args)
    if command == "/task":
        return write_human_inbox("task", args)
    if command == "/details":
        return task_details(args)
    if command == "/help":
        return HELP_TEXT
    raise DispatchError(HELP_TEXT)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(HELP_TEXT)
        return 2
    command = argv[1]
    args = argv[2:]
    try:
        output = dispatch(command, args)
    except DispatchError as exc:
        output = f"ERROR: {exc}"
        append_command_log(command, args, 2, output)
        print(output)
        return 2
    append_command_log(command, args, 0, output)
    print(truncate(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
