#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT_DIR / ".agent" / "tasks"

SAFE_TASK_ID_RE = re.compile(r"^TASK-[A-Za-z0-9._-]+$")
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


class TaskStateError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_task_id(task_id: str) -> None:
    if not SAFE_TASK_ID_RE.fullmatch(task_id):
        raise TaskStateError(f"Unsafe or invalid task id: {task_id!r}")
    if task_id == "TASK-TEMPLATE":
        raise TaskStateError("Refusing to operate on TASK-TEMPLATE.")


def task_path_for(task_id: str) -> Path:
    validate_task_id(task_id)
    path = TASK_DIR / f"{task_id}.json"
    if path.parent != TASK_DIR:
        raise TaskStateError(f"Refusing unsafe task path: {path}")
    return path


def load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise TaskStateError(f"Refusing symlinked task file: {relative(path)}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskStateError(f"Task file not found: {relative(path)}") from exc
    except json.JSONDecodeError as exc:
        raise TaskStateError(
            f"Task file is invalid JSON: {relative(path)} line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(data, dict):
        raise TaskStateError(f"Task file must contain a JSON object: {relative(path)}")
    return data


def load_task(task_id: str) -> tuple[Path, dict[str, Any]]:
    path = task_path_for(task_id)
    data = load_json(path)
    actual = data.get("task_id")
    if actual != task_id:
        raise TaskStateError(f"{relative(path)} has task_id {actual!r}, expected {task_id!r}.")
    return path, data


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR))


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
    if path.parent != TASK_DIR:
        raise TaskStateError(f"Refusing to write outside .agent/tasks/: {path}")
    if path.is_symlink():
        raise TaskStateError(f"Refusing symlinked task file: {relative(path)}")
    raw = path.read_text(encoding="utf-8")
    indent = infer_indent(raw)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=True) + "\n", encoding="utf-8")


def iter_task_records() -> list[tuple[int, str, str, Path]]:
    records: list[tuple[int, str, str, Path]] = []
    for path in sorted(TASK_DIR.glob("*.json")):
        if path.name == "TASK-TEMPLATE.json" or path.is_symlink():
            continue
        try:
            data = load_json(path)
        except TaskStateError:
            continue
        task_id = data.get("task_id")
        status = data.get("status")
        if not isinstance(task_id, str) or not isinstance(status, str):
            continue
        if not SAFE_TASK_ID_RE.fullmatch(task_id):
            continue
        if status not in ACTIONABLE_PRIORITY:
            continue
        created_at = data.get("created_at")
        created_key = created_at if isinstance(created_at, str) else ""
        records.append((ACTIONABLE_PRIORITY[status], created_key, task_id, path))
    records.sort(key=lambda item: (item[0], item[1], item[2]))
    return records


def list_actionable(_: argparse.Namespace) -> int:
    for _, _, task_id, _ in iter_task_records():
        print(task_id)
    return 0


def get_next(_: argparse.Namespace) -> int:
    records = iter_task_records()
    if not records:
        return 1
    print(records[0][2])
    return 0


def update_status(task_id: str, status: str, reason: str | None = None, review_file: str | None = None) -> Path:
    if status not in ALLOWED_STATUSES:
        raise TaskStateError(f"Invalid status {status!r}; allowed statuses: {', '.join(sorted(ALLOWED_STATUSES))}")
    path, data = load_task(task_id)
    data["status"] = status
    data["status_updated_at"] = utc_now()
    if reason is not None:
        data["status_reason"] = reason
    if review_file is not None:
        data["last_review_file"] = review_file
    write_task(path, data)
    return path


def set_status(args: argparse.Namespace) -> int:
    path = update_status(args.task_id, args.status, args.reason)
    print(f"Updated {relative(path)} to {args.status}")
    return 0


def mark_in_progress(args: argparse.Namespace) -> int:
    path = update_status(args.task_id, "in_progress", "Selected for implementation by the agent loop.")
    print(f"Updated {relative(path)} to in_progress")
    return 0


def mark_implemented(args: argparse.Namespace) -> int:
    path = update_status(args.task_id, "implemented", "Codex review accepted the implementation.")
    print(f"Updated {relative(path)} to implemented")
    return 0


def mark_needs_revision(args: argparse.Namespace) -> int:
    reason = f"Codex review requires revision. Review file: {args.review_file}"
    path = update_status(args.task_id, "needs_revision", reason, args.review_file)
    print(f"Updated {relative(path)} to needs_revision")
    return 0


def mark_blocked(args: argparse.Namespace) -> int:
    path = update_status(args.task_id, "blocked", args.reason)
    print(f"Updated {relative(path)} to blocked")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage .agent task state deterministically.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-actionable", help="Print actionable task ids in priority order.")
    list_parser.set_defaults(func=list_actionable)

    get_parser = subparsers.add_parser("get-next", help="Print the next actionable task id.")
    get_parser.set_defaults(func=get_next)

    set_parser = subparsers.add_parser("set-status", help="Set an explicit task status.")
    set_parser.add_argument("task_id")
    set_parser.add_argument("status")
    set_parser.add_argument("--reason")
    set_parser.set_defaults(func=set_status)

    progress_parser = subparsers.add_parser("mark-in-progress", help="Mark a task in_progress.")
    progress_parser.add_argument("task_id")
    progress_parser.set_defaults(func=mark_in_progress)

    implemented_parser = subparsers.add_parser("mark-implemented", help="Mark a task implemented.")
    implemented_parser.add_argument("task_id")
    implemented_parser.set_defaults(func=mark_implemented)

    revision_parser = subparsers.add_parser("mark-needs-revision", help="Mark a task needs_revision.")
    revision_parser.add_argument("task_id")
    revision_parser.add_argument("--review-file", required=True)
    revision_parser.set_defaults(func=mark_needs_revision)

    blocked_parser = subparsers.add_parser("mark-blocked", help="Mark a task blocked.")
    blocked_parser.add_argument("task_id")
    blocked_parser.add_argument("--reason", required=True)
    blocked_parser.set_defaults(func=mark_blocked)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        return args.func(args)
    except TaskStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
