#!/usr/bin/env python3
"""Run the data-analysis skill eval suite against a deterministic seed database.

Usage
-----
    export ANTHROPIC_API_KEY=sk-...
    python .cursor/skills/data-analysis/evals/run_evals.py

Requires an API key — **manual only, never in CI**.
The seed DB is built in-memory; it never touches the real database.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SKILL_DIR))

import yaml  # noqa: E402

from app.config import get_settings  # noqa: E402


def _load_questions() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("questions.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def _extract_numbers(text: str) -> list[float]:
    """Extract all numeric literals from a string."""
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _grade(answer_text: str, tool_calls: list[dict], spec: dict) -> tuple[bool, str]:
    """Grade a single eval answer against its spec. Returns (passed, reason)."""
    failures: list[str] = []

    if "expect_contains" in spec:
        expected = spec["expect_contains"].lower()
        if expected not in answer_text.lower():
            failures.append(
                f"expected answer to contain '{spec['expect_contains']}'"
            )

    if "expect_numeric" in spec:
        target = float(spec["expect_numeric"])
        tol = float(spec.get("tolerance", 0.1))
        nums = _extract_numbers(answer_text)
        if not any(abs(n - target) <= tol for n in nums):
            failures.append(
                f"expected numeric {target} (±{tol}), "
                f"found {nums}"
            )

    if "expect_tools" in spec:
        used = {tc.get("tool", "") for tc in tool_calls}
        missing = set(spec["expect_tools"]) - used
        if missing:
            failures.append(f"expected tools {missing} not called")

    if failures:
        return False, "; ".join(failures)
    return True, "ok"


def main() -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY must be set to run evals.")
        sys.exit(1)

    questions = _load_questions()
    print(f"Loaded {len(questions)} eval questions.\n")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        db_url = f"sqlite:///{tmp_path}"
        os.environ["DATABASE_URL"] = db_url
        get_settings.cache_clear()

        from evals.seed_data import build_seed_db  # noqa: E402

        build_seed_db(db_url)
        print(f"Seed database created at {tmp_path}\n")

        from analyze import analyze  # noqa: E402

        results: list[tuple[str, bool, str]] = []
        for i, spec in enumerate(questions, 1):
            q = spec["q"]
            print(f"[{i}/{len(questions)}] {q}")
            result = analyze(q)
            passed, reason = _grade(
                result.answer, result.tool_calls, spec,
            )
            status = "PASS" if passed else "FAIL"
            print(f"  → {status}: {reason}")
            if result.corrections:
                print(f"  corrections: {result.corrections}")
            results.append((q, passed, reason))
            print()

        passed_count = sum(1 for _, p, _ in results if p)
        total = len(results)
        print("=" * 60)
        print(f"Results: {passed_count}/{total} passed")
        print("=" * 60)
        for q, passed, reason in results:
            mark = "✓" if passed else "✗"
            print(f"  {mark} {q[:60]}")
            if not passed:
                print(f"    {reason}")

    finally:
        os.unlink(tmp_path)
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
