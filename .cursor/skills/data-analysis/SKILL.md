---
name: Data Analysis
description: Answer natural-language questions about stored readings and events
when_to_use: When the user asks about trends, per-city comparisons, time-window summaries,
  or event distributions over the WatchAgent database
---

# Data Analysis Skill

Takes a natural-language question about the data in `data/watchagent.db` and returns a structured
answer with evidence and a tool-call trace. It is implemented as a **Tool-Use** agent using the
Anthropic Messages API plus deterministic Python tools over the WatchAgent database.

## Agentic design patterns

This skill implements three named patterns from the Agentic Design Patterns reference:

1. **Tool Use** — the core ReAct loop delegates data retrieval to deterministic Python/SQL tools
   (`query_readings`, `query_events`, `compute_statistics`, `compare_cities`,
   `count_events_by_type`, `list_event_types`). The LLM reasons about which tool to call next;
   the tools themselves are pure DB queries with no LLM involvement.

2. **Planning** — the system prompt provides schema context so the model can plan a multi-step
   investigation (e.g. "first list event types, then count by type, then query the top one").

3. **Reflection** — after the ReAct loop produces a candidate answer, a separate verification
   call checks every numeric claim against the tool-result trace. The reflected output includes
   a `corrections` list so the trace shows what the agent caught (or confirmed). See §Reflection
   below.

## Step bound

The total call budget is **`MAX_STEPS` (6) ReAct iterations + 1 Reflection call = 7 LLM calls
maximum**. This keeps latency and cost predictable.

## How to run

### Interactive analysis

```bash
export ANTHROPIC_API_KEY=sk-...
python .cursor/skills/data-analysis/analyze.py "Which city had the most events this week?"
```

Output is a JSON object on stdout:

```json
{
  "answer": "Toronto had the most events in the requested window.",
  "evidence": [],
  "tool_calls": [],
  "confidence": "high",
  "corrections": []
}
```

### Natural-language digest

Generate a grounded briefing of recent events:

```bash
python .cursor/skills/data-analysis/digest.py              # last 24 hours
python .cursor/skills/data-analysis/digest.py --hours 48   # custom window
```

Output includes both the LLM-rendered prose and the raw facts for verification:

```json
{
  "digest": "Over the past 24 hours, WatchAgent detected 5 events across ...",
  "facts": {
    "total_events": 5,
    "events_by_city": {"Toronto": 3, "Ottawa": 1, "Vancouver": 1},
    "events_by_type": {"rapid_change": 3, "wmo_transition": 2},
    ...
  }
}
```

### Eval suite

Run the deterministic eval against a fixed seed database:

```bash
export ANTHROPIC_API_KEY=sk-...
python .cursor/skills/data-analysis/evals/run_evals.py
```

**Manual only — never in CI.** Requires an API key. The seed DB is built in-memory and never
touches the real database.

## Environment

Requires `ANTHROPIC_API_KEY` for the LLM loop. The running WatchAgent service does **not**
require this key — it is used only by this skill. Install local dev dependencies with
`pip install -e ".[dev]"`.

## Reflection

After the main ReAct loop produces a candidate answer, a dedicated reflection call verifies it:

- The model receives the original question, the full tool-call/result trace, and the candidate
  answer.
- It must check each numeric claim against the tool results and return corrected JSON.
- The output includes a `corrections` list (empty if everything checked out).
- This adds exactly one LLM call to the pipeline, bounded by the step limit above.

This implements the **Reflection** pattern: the agent audits its own output before finalizing,
catching arithmetic mistakes and mis-readings of tool results.

## Eval set

The eval suite (`evals/`) tests the skill against a **fixed seed dataset** so expected answers
are exact and reproducible:

- `seed_data.py` — builds a deterministic SQLite database with known readings and events.
- `questions.yaml` — 8 questions, each with a grader spec (`expect_contains`,
  `expect_numeric` with tolerance, `expect_tools`).
- `run_evals.py` — builds the seed DB in-memory, runs `analyze()` per question, grades
  results with structural and value checks (no LLM grading), and prints a pass/fail table.

Because the seed data is fixed, answers like "Toronto had the most rapid_change events" and
"max temperature was 12.8" are ground truth — not LLM-judged opinions.

## Grounded NL digest

The digest (`digest.py`) uses **grounded generation** to produce a natural-language briefing:

1. `gather_facts(hours)` — pure, deterministic DB queries that collect counts by city/type,
   severity breakdown, notable events with exact numbers, and latest readings. No LLM involved.
2. `render_digest(facts)` — the LLM receives **only** the gathered facts and is prompted to
   never invent numbers. It renders them into 4–6 sentence prose.

The output includes both `digest` (prose) and `facts` (raw data) so every claim in the briefing
is verifiable against the structured facts. This avoids hallucination by construction: the LLM
has no access to data beyond what `gather_facts` provides.

## Design notes

- **No coupling to the running service** — all files live under `.cursor/skills/data-analysis/`.
  The `app/` package never imports `anthropic`.
- Tools are deterministic Python functions over SQLAlchemy — same query, same result.
- `ANTHROPIC_API_KEY` is read from the environment only; no key is ever committed.
- The eval seed DB is ephemeral (temp file, deleted after run) and never touches production data.
