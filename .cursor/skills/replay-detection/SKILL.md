---
name: Replay Detection
description: Re-run native detectors and lifecycle over stored readings without writing events
when_to_use: After changing detector thresholds, scoring, lifecycle, or features, before
  committing calibration-sensitive changes
---

# Replay Detection Skill

This skill replays the current native detector registry and lifecycle manager over recent
stored readings. It writes lifecycle output only to an in-memory SQLite database, so it does not
modify `data/watchagent.db`.

Run:

```bash
python3 .cursor/skills/replay-detection/replay.py --limit 100
python3 .cursor/skills/replay-detection/replay.py --city Toronto --limit 500
```

Output includes:

- `candidate_count`: raw `EventCandidate` firings from pure detectors
- `incident_count`: lifecycle-collapsed incidents from stable dedupe keys
- `candidates_sample`: sample candidate evidence and score inputs
- `replayed_incidents`: in-memory incidents with status, score, severity, onset, peak, and
  resolved timestamps
- `stored_event_sample`: current DB events for comparison

Use this for local smoke checks. For calibrated multi-year numbers and before/after tables, use
the authoritative evaluation script:

```bash
python3 scripts/evaluate.py --source archive --start-date 2023-01-01 --end-date 2025-12-31
```

Notes:

- Forecast-bust only fires during replay when the local DB contains stored forecast rows and
  enough obs/forecast comparison pairs.
- The script uses the committed climatology artifact through the normal feature layer; it does
  not fetch archive data.
