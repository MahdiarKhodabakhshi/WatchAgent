#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT_DIR / ".agent"
TASK_SCHEMA_PATH = AGENT_DIR / "schemas" / "task.schema.json"

TOP_LEVEL_KEYS = {
    "summary",
    "observations",
    "risks",
    "recommended_order",
    "tasks",
}
ALLOWED_STATUSES = {
    "proposed",
    "approved",
    "rejected",
    "in_progress",
    "implemented",
    "needs_revision",
    "blocked",
}
ALLOWED_RISKS = {"low", "medium", "high"}
TASK_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ValidationFailure(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def usage() -> str:
    return "Usage: scripts/materialize-planner-output.py [planner-output.json|-]"


def read_input(argv: list[str]) -> str:
    if len(argv) > 2:
        raise ValidationFailure([usage()])

    if len(argv) == 2 and argv[1] != "-":
        return Path(argv[1]).read_text(encoding="utf-8")

    return sys.stdin.read()


def strip_surrounding_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 2:
        raise ValidationFailure(["Planner output starts with a Markdown fence but is incomplete."])

    opening = lines[0].strip().lower()
    closing = lines[-1].strip()
    if opening not in {"```", "```json"}:
        raise ValidationFailure([f"Unsupported Markdown fence for planner output: {lines[0].strip()!r}"])
    if closing != "```":
        raise ValidationFailure(["Planner output has extra prose after the Markdown fence."])

    return "\n".join(lines[1:-1]).strip()


def load_json_document(raw: str) -> dict[str, Any]:
    stripped = strip_surrounding_fence(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValidationFailure([f"Planner output is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"]) from exc

    if not isinstance(data, dict):
        raise ValidationFailure(["Planner output must be a JSON object."])

    return data


def load_task_schema() -> dict[str, Any]:
    try:
        schema = json.loads(TASK_SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationFailure([f"Task schema not found: {TASK_SCHEMA_PATH.relative_to(ROOT_DIR)}"]) from exc
    except json.JSONDecodeError as exc:
        raise ValidationFailure([f"Task schema is invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"]) from exc

    if not isinstance(schema, dict):
        raise ValidationFailure(["Task schema must be a JSON object."])
    return schema


def json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def type_matches(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for expected_type in expected_types:
        if expected_type == "null" and value is None:
            return True
        if expected_type == "string" and isinstance(value, str):
            return True
        if expected_type == "boolean" and isinstance(value, bool):
            return True
        if expected_type == "array" and isinstance(value, list):
            return True
        if expected_type == "object" and isinstance(value, dict):
            return True
    return False


def validate_iso_datetime(value: str, path: str, errors: list[str]) -> None:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{path} must be an ISO 8601 date-time string.")
        return

    if parsed.tzinfo is None:
        errors.append(f"{path} must include timezone information.")


def validate_schema_value(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not type_matches(value, expected_type):
        errors.append(f"{path} must be {expected_type}; got {json_type_name(value)}.")
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}; got {value!r}.")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path} must not be empty.")

        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.fullmatch(pattern, value):
            errors.append(f"{path} must match pattern {pattern!r}; got {value!r}.")

        if schema.get("format") == "date-time":
            validate_iso_datetime(value, path, errors)

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema_value(item, item_schema, f"{path}[{index}]", errors)


def validate_safe_task_id(task_id: Any, path: str, errors: list[str]) -> None:
    if not isinstance(task_id, str):
        return

    if not task_id:
        errors.append(f"{path} must not be empty.")
        return

    if not TASK_ID_SAFE_RE.fullmatch(task_id):
        errors.append(f"{path} contains unsafe characters: {task_id!r}.")

    if "/" in task_id or "\\" in task_id:
        errors.append(f"{path} must not contain path separators: {task_id!r}.")

    if os.path.isabs(task_id):
        errors.append(f"{path} must not be an absolute path: {task_id!r}.")

    if task_id in {".", ".."}:
        errors.append(f"{path} must not be a path traversal token: {task_id!r}.")


def validate_task(task: Any, index: int, task_schema: dict[str, Any], errors: list[str]) -> str | None:
    path = f"tasks[{index}]"
    if not isinstance(task, dict):
        errors.append(f"{path} must be an object.")
        return None

    required = task_schema.get("required", [])
    properties = task_schema.get("properties", {})
    if not isinstance(required, list) or not isinstance(properties, dict):
        errors.append("Task schema has unsupported required/properties structure.")
        return None

    allowed_keys = set(properties)
    missing = [name for name in required if name not in task]
    for name in missing:
        errors.append(f"{path}.{name} is required.")

    if task_schema.get("additionalProperties") is False:
        for name in sorted(set(task) - allowed_keys):
            errors.append(f"{path}.{name} is not allowed by task.schema.json.")

    for name, value in task.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, dict):
            validate_schema_value(value, property_schema, f"{path}.{name}", errors)

    task_id = task.get("task_id")
    validate_safe_task_id(task_id, f"{path}.task_id", errors)

    status = task.get("status")
    if isinstance(status, str) and status not in ALLOWED_STATUSES:
        errors.append(f"{path}.status must be one of {sorted(ALLOWED_STATUSES)!r}; got {status!r}.")

    risk = task.get("risk")
    if isinstance(risk, str) and risk not in ALLOWED_RISKS:
        errors.append(f"{path}.risk must be one of {sorted(ALLOWED_RISKS)!r}; got {risk!r}.")

    return task_id if isinstance(task_id, str) else None


def validate_string_array(data: dict[str, Any], key: str, errors: list[str]) -> None:
    value = data.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be an array.")
        return

    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{key}[{index}] must be a string; got {json_type_name(item)}.")


def validate_planner_output(data: dict[str, Any], task_schema: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[str] = []

    missing = sorted(TOP_LEVEL_KEYS - set(data))
    for key in missing:
        errors.append(f"{key} is required.")

    extra = sorted(set(data) - TOP_LEVEL_KEYS)
    for key in extra:
        errors.append(f"{key} is not allowed at the planner output top level.")

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary must be a non-empty string.")

    validate_string_array(data, "observations", errors)
    validate_string_array(data, "risks", errors)
    validate_string_array(data, "recommended_order", errors)

    tasks_value = data.get("tasks")
    tasks: list[dict[str, Any]] = []
    task_ids: list[str] = []
    if not isinstance(tasks_value, list):
        errors.append("tasks must be an array.")
    else:
        for index, task in enumerate(tasks_value):
            task_id = validate_task(task, index, task_schema, errors)
            if isinstance(task, dict):
                tasks.append(task)
            if task_id is not None:
                task_ids.append(task_id)

    seen: set[str] = set()
    for task_id in task_ids:
        if task_id in seen:
            errors.append(f"Duplicate task_id: {task_id}")
        seen.add(task_id)

    known_task_ids = set(task_ids)
    recommended = data.get("recommended_order")
    if isinstance(recommended, list):
        seen_recommended: set[str] = set()
        for index, task_id in enumerate(recommended):
            if not isinstance(task_id, str):
                continue
            validate_safe_task_id(task_id, f"recommended_order[{index}]", errors)
            if task_id in seen_recommended:
                errors.append(f"recommended_order[{index}] duplicates task_id {task_id!r}.")
            seen_recommended.add(task_id)
            if task_id not in known_task_ids:
                errors.append(f"recommended_order[{index}] references unknown task_id {task_id!r}.")

    if errors:
        raise ValidationFailure(errors)

    return tasks


def agent_path(*parts: str) -> Path:
    path = AGENT_DIR.joinpath(*parts)
    resolved_agent = AGENT_DIR.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_agent and not str(resolved_path).startswith(str(resolved_agent) + os.sep):
        raise ValidationFailure([f"Refusing to write outside .agent/: {path}"])
    return path


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR))


def add_markdown_list(lines: list[str], items: list[str]) -> None:
    if not items:
        lines.append("- None")
        return
    for item in items:
        lines.append(f"- {item}")


def plan_markdown(data: dict[str, Any], tasks: list[dict[str, Any]], generated_at: str) -> str:
    lines: list[str] = [
        f"# Planner Result {generated_at}",
        "",
        f"- Generated at: {generated_at}",
        "",
        "## Summary",
        "",
        data["summary"],
        "",
        "## Observations",
        "",
    ]
    add_markdown_list(lines, data["observations"])
    lines.extend(["", "## Risks", ""])
    add_markdown_list(lines, data["risks"])
    lines.extend(["", "## Recommended Order", ""])
    add_markdown_list(lines, data["recommended_order"])
    lines.extend(["", "## Tasks", ""])
    if not tasks:
        lines.append("- None")
    else:
        for task in tasks:
            lines.append(
                f"- `{task['task_id']}`: {task['title']} "
                f"(status: {task['status']}, risk: {task['risk']}, area: {task['area']})"
            )
    lines.append("")
    return "\n".join(lines)


def approval_markdown(task: dict[str, Any], task_path: Path, generated_at: str) -> str:
    lines: list[str] = [
        f"# Pending Approval: {task['task_id']}",
        "",
        f"- Title: {task['title']}",
        f"- Status: {task['status']}",
        f"- Risk: {task['risk']}",
        f"- Area: {task['area']}",
        f"- Approval required: {str(task['approval_required']).lower()}",
        f"- Approved by: {task['approved_by']}",
        f"- Created at: {task['created_at']}",
        f"- Materialized at: {generated_at}",
        f"- Task file: {relative(task_path)}",
        "",
        "## Objective",
        "",
        task["objective"],
        "",
        "## Context",
        "",
        task["context"] or "No additional context provided.",
        "",
        "## Implementation Plan",
        "",
    ]
    add_markdown_list(lines, task["implementation_plan"])
    lines.extend(["", "## Acceptance Criteria", ""])
    add_markdown_list(lines, task["acceptance_criteria"])
    lines.extend(["", "## Test Plan", ""])
    add_markdown_list(lines, task["test_plan"])
    lines.extend(["", "## Files Likely To Change", ""])
    add_markdown_list(lines, task["files_likely_to_change"])
    lines.extend(["", "## Forbidden Changes", ""])
    add_markdown_list(lines, task["forbidden_changes"])
    lines.extend(["", "## Done Definition", ""])
    add_markdown_list(lines, task["done_definition"])
    lines.extend(
        [
            "",
            "## Approval Command",
            "",
            "```bash",
            f"scripts/agent-approve.sh {task['task_id']}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def check_write_targets(plan_path: Path, tasks: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    if plan_path.exists():
        errors.append(f"Refusing to overwrite existing plan: {relative(plan_path)}")

    for task in tasks:
        task_path = agent_path("tasks", f"{task['task_id']}.json")
        if task_path.exists():
            errors.append(f"Refusing to overwrite existing task: {relative(task_path)}")
        if task["status"] == "proposed":
            approval_path = agent_path("approvals", "pending", f"{task['task_id']}.md")
            if approval_path.exists():
                errors.append(f"Refusing to overwrite existing pending approval: {relative(approval_path)}")

    if errors:
        raise ValidationFailure(errors)


def materialize(data: dict[str, Any], tasks: list[dict[str, Any]]) -> list[Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan_path = agent_path("plans", f"PLAN-{timestamp}.md")

    check_write_targets(plan_path, tasks)

    for directory in [
        agent_path("plans"),
        agent_path("tasks"),
        agent_path("approvals", "pending"),
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    plan_path.write_text(plan_markdown(data, tasks, generated_at), encoding="utf-8")
    created.append(plan_path)

    for task in tasks:
        task_path = agent_path("tasks", f"{task['task_id']}.json")
        task_path.write_text(json.dumps(task, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        created.append(task_path)

        if task["status"] == "proposed":
            approval_path = agent_path("approvals", "pending", f"{task['task_id']}.md")
            approval_path.write_text(approval_markdown(task, task_path, generated_at), encoding="utf-8")
            created.append(approval_path)

    return created


def main(argv: list[str]) -> int:
    try:
        raw = read_input(argv)
        data = load_json_document(raw)
        task_schema = load_task_schema()
        tasks = validate_planner_output(data, task_schema)
        created = materialize(data, tasks)
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValidationFailure as exc:
        print("ERROR: Planner output validation failed:", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Planner output materialized.")
    for path in created:
        print(f"Created {relative(path)}")
    print(
        "Summary: "
        f"1 plan, {len(tasks)} task file(s), "
        f"{sum(1 for task in tasks if task['status'] == 'proposed')} pending approval file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
