---
name: Explain Event
description: Inspect one stored lifecycle incident with score, evidence, and supporting readings
when_to_use: When reviewing why a WatchAgent event is in the feed or validating an incident
  after detector/lifecycle changes
---

# Explain Event Skill

Explains one stored ORM `Event` row. This is read-only and project-specific to the current
lifecycle/scoring schema.

Run:

```bash
python3 .cursor/skills/explain-event/explain.py --event-id 1
```

The JSON output includes:

- event identity and lifecycle status
- `priority_score`, derived severity, confidence, detector name, and dedupe key
- onset, peak, and resolved timestamps
- signal values and evidence
- supporting readings referenced by `supporting_reading_ids`

Use this when checking whether the feed ranking and explanation are defensible for a specific
incident.
