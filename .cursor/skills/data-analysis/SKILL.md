---
name: Analyze Events
description: Answer read-only questions about stored readings and lifecycle incidents
when_to_use: When the user asks about event rates, top incidents, per-city comparisons,
  lifecycle status, score distribution, or recent WatchAgent weather summaries
---

# Analyze Events Skill

This skill answers natural-language questions about the local WatchAgent SQLite database.
It is project-specific to the current schema: enriched readings plus lifecycle incidents with
`status`, `priority_score`, `confidence`, `dedupe_key`, and `evidence`.

## Run

```bash
export ANTHROPIC_API_KEY=sk-...
python3 .cursor/skills/data-analysis/analyze.py "Which open incidents have the highest priority?"
```

The script prints JSON:

```json
{
  "answer": "Toronto has the highest-priority open incident.",
  "evidence": [],
  "tool_calls": [],
  "confidence": "high",
  "corrections": []
}
```

Generate a grounded deterministic-facts briefing:

```bash
python3 .cursor/skills/data-analysis/digest.py
python3 .cursor/skills/data-analysis/digest.py --hours 48
```

`digest.py` returns both rendered prose and raw facts so every count and event claim is
auditable.

## Data Contract

The skill reads:

- `readings`: city, UTC observation time, core weather fields, enriched pressure/humidity/dew
  point/gust/cloud/snow fields.
- `events`: stored lifecycle incidents. Severity is derived from `priority_score`; status is
  `open`, `ongoing`, or `resolved`; `dedupe_key` identifies the persistent incident.

The skill never writes to the database.

## Agentic Pattern

- **Tool use**: a bounded ReAct loop calls deterministic SQLAlchemy tools such as
  `query_readings`, `query_events`, `compute_statistics`, `compare_cities`,
  `count_events_by_type`, and `list_event_types`.
- **Reflection**: one verification call checks numeric claims against the tool trace.
- **Grounded generation**: `digest.py` gathers facts first, then asks the LLM to render only
  those facts.

Maximum LLM budget is 6 tool-use iterations plus 1 reflection call.

## Environment

Requires `ANTHROPIC_API_KEY` only for this optional Cursor skill. The WatchAgent service, Docker
startup, tests, and replay scripts do not require LLM credentials.

Install local dependencies with:

```bash
python3 -m pip install -e ".[dev]"
```

## Eval Suite

Manual deterministic eval:

```bash
export ANTHROPIC_API_KEY=sk-...
python3 .cursor/skills/data-analysis/evals/run_evals.py
```

The eval seed database is temporary and never touches `data/watchagent.db`. It is manual-only and
not part of CI.
