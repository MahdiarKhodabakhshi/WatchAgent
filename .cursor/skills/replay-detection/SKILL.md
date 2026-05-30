---
name: Replay Detection
description: Re-run current detection rules over stored readings to see what would fire
when_to_use: After modifying detection rules or thresholds, before committing changes
---

# Replay Detection Skill

Re-runs the current `detect()` function over the last N stored readings without modifying the
database. It reports which events would fire under the current rules and compares them with events
that were actually stored when those readings were first processed.

Use cases:

- Threshold tuning: inspect what a new threshold would flag.
- Regression check: confirm a rule change did not silently drop expected events.
- Review prep: generate examples for README event-detection rationale.

Run:

```bash
python .cursor/skills/replay-detection/replay.py --limit 100
```
