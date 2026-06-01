#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import desc, func, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.detection.base import EVENT_TYPES  # noqa: E402
from app.detection.statistics import mean, percentile, population_std  # noqa: E402
from app.models import Event, Reading  # noqa: E402

CityName = Literal["Ottawa", "Toronto", "Vancouver"]
MetricName = Literal[
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "wind_speed_10m",
    "surface_pressure",
    "pressure_msl",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_gusts_10m",
    "cloud_cover",
    "snowfall",
    "snow_depth",
]

MAX_STEPS = 6
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


class QueryReadingsInput(BaseModel):
    city: CityName | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(100, ge=1, le=1000)


class QueryEventsInput(BaseModel):
    city: CityName | None = None
    event_type: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(100, ge=1, le=1000)


class ComputeStatisticsInput(BaseModel):
    metric: MetricName
    city: CityName | None = None
    window_hours: int = Field(24, ge=1, le=720)


class CompareCitiesInput(BaseModel):
    metric: MetricName
    window_hours: int = Field(24, ge=1, le=720)


class CountEventsInput(BaseModel):
    window_hours: int = Field(168, ge=1, le=720)


class AnalysisResult(BaseModel):
    answer: str
    evidence: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    confidence: Literal["high", "medium", "low"]
    corrections: list[str] = []


def make_session_factory() -> sessionmaker[Session]:
    engine = build_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def query_readings(**kwargs: Any) -> dict[str, Any]:
    args = QueryReadingsInput(**kwargs)
    SessionLocal = make_session_factory()
    with SessionLocal() as session:
        query = select(Reading)
        if args.city:
            query = query.where(Reading.city == args.city)
        if args.start:
            query = query.where(Reading.observation_ts >= args.start)
        if args.end:
            query = query.where(Reading.observation_ts <= args.end)
        rows = session.scalars(
            query.order_by(Reading.observation_ts.desc()).limit(args.limit)
        ).all()
        return {"readings": [_reading_to_dict(row) for row in rows], "count": len(rows)}


def query_events(**kwargs: Any) -> dict[str, Any]:
    args = QueryEventsInput(**kwargs)
    SessionLocal = make_session_factory()
    with SessionLocal() as session:
        query = select(Event)
        if args.city:
            query = query.where(Event.city == args.city)
        if args.event_type:
            query = query.where(Event.event_type == args.event_type)
        if args.start:
            query = query.where(Event.event_ts >= args.start)
        if args.end:
            query = query.where(Event.event_ts <= args.end)
        rows = session.scalars(
            query.order_by(
                desc(Event.priority_score).nulls_last(),
                Event.event_ts.desc(),
            ).limit(args.limit),
        ).all()
        return {"events": [_event_to_dict(row) for row in rows], "count": len(rows)}


def compute_statistics(**kwargs: Any) -> dict[str, Any]:
    args = ComputeStatisticsInput(**kwargs)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
    SessionLocal = make_session_factory()
    with SessionLocal() as session:
        query = select(Reading).where(Reading.observation_ts >= cutoff)
        if args.city:
            query = query.where(Reading.city == args.city)
        readings = session.scalars(query).all()
        values = [
            float(value)
            for row in readings
            if (value := getattr(row, args.metric)) is not None
        ]
        return _summary_stats(values) | {
            "metric": args.metric,
            "city": args.city,
            "window_hours": args.window_hours,
        }


def compare_cities(**kwargs: Any) -> dict[str, Any]:
    args = CompareCitiesInput(**kwargs)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
    output: dict[str, Any] = {}
    SessionLocal = make_session_factory()
    with SessionLocal() as session:
        for city in ("Ottawa", "Toronto", "Vancouver"):
            rows = session.scalars(
                select(Reading)
                .where(Reading.city == city)
                .where(Reading.observation_ts >= cutoff)
                .order_by(Reading.observation_ts.desc())
            ).all()
            values = [
                float(value)
                for row in rows
                if (value := getattr(row, args.metric)) is not None
            ]
            stats = _summary_stats(values)
            stats["latest_value"] = values[0] if values else None
            output[city] = stats
    return {"metric": args.metric, "window_hours": args.window_hours, "cities": output}


def count_events_by_type(**kwargs: Any) -> dict[str, Any]:
    args = CountEventsInput(**kwargs)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
    SessionLocal = make_session_factory()
    with SessionLocal() as session:
        rows = session.execute(
            select(Event.event_type, func.count(Event.id))
            .where(Event.event_ts >= cutoff)
            .group_by(Event.event_type)
            .order_by(func.count(Event.id).desc())
        ).all()
        return {
            "window_hours": args.window_hours,
            "counts": [{"event_type": row[0], "count": int(row[1])} for row in rows],
        }


def list_event_types() -> dict[str, Any]:
    return {"event_types": sorted(EVENT_TYPES)}


TOOLS = {
    "query_readings": query_readings,
    "query_events": query_events,
    "compute_statistics": compute_statistics,
    "compare_cities": compare_cities,
    "count_events_by_type": count_events_by_type,
    "list_event_types": list_event_types,
}

TOOL_DEFINITIONS = [
    {
        "name": "query_readings",
        "description": "Query stored readings by city and optional time range.",
        "input_schema": QueryReadingsInput.model_json_schema(),
    },
    {
        "name": "query_events",
        "description": "Query stored events by city, event type, and optional time range.",
        "input_schema": QueryEventsInput.model_json_schema(),
    },
    {
        "name": "compute_statistics",
        "description": "Compute count, mean, std, min, max, p05, p50, and p95.",
        "input_schema": ComputeStatisticsInput.model_json_schema(),
    },
    {
        "name": "compare_cities",
        "description": "Compare a metric across Ottawa, Toronto, and Vancouver.",
        "input_schema": CompareCitiesInput.model_json_schema(),
    },
    {
        "name": "count_events_by_type",
        "description": "Count events grouped by event type over a recent window.",
        "input_schema": CountEventsInput.model_json_schema(),
    },
    {
        "name": "list_event_types",
        "description": "List supported WatchAgent event types.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

SYSTEM_PROMPT = """You answer questions about the WatchAgent SQLite database.

Schema reminder:
- readings(city, observation_ts, polled_at, temperature_2m, apparent_temperature,
  precipitation, wind_speed_10m, weather_code, surface_pressure, pressure_msl,
  relative_humidity_2m, dew_point_2m, wind_gusts_10m, cloud_cover, snowfall,
  snow_depth)
- events(city, event_ts, created_at, event_type, severity, metric, signal_values,
  reason, supporting_reading_ids, status, onset_ts, peak_ts, resolved_ts,
  priority_score, confidence, detector_name, dedupe_key, evidence)

WatchAgent stores lifecycle incidents. Severity is derived from priority_score, and
/events is sorted by priority_score first.

Use tools for evidence. Before finalizing, verify that the answer is supported by tool outputs.
Return only JSON with keys: answer, evidence, tool_calls, confidence.
"""


REFLECTION_PROMPT = (
    "Verify the candidate answer against the tool results. For each numeric claim, "
    "confirm it matches a tool result. Return JSON only: "
    '{"answer": str, "confidence": "high|medium|low", "corrections": [str]}'
)


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that LLMs sometimes add around JSON."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped.strip()


def reflect(
    question: str,
    tool_trace: list[dict[str, Any]],
    candidate_answer: str,
    client: Any,
) -> AnalysisResult:
    """Reflection pass: verify numeric claims in the candidate answer against tool results."""
    payload = json.dumps(
        {
            "question": question,
            "tool_trace": tool_trace,
            "candidate_answer": candidate_answer,
        },
        default=str,
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=REFLECTION_PROMPT,
        messages=[{"role": "user", "content": payload}],
    )
    text = "".join(
        getattr(block, "text", "")
        for block in response.content
        if getattr(block, "type", None) == "text"
    )
    try:
        parsed = json.loads(_strip_code_fences(text))
        return AnalysisResult(
            answer=parsed.get("answer", candidate_answer),
            evidence=[],
            tool_calls=tool_trace,
            confidence=parsed.get("confidence", "medium"),
            corrections=parsed.get("corrections", []),
        )
    except (json.JSONDecodeError, ValueError):
        return AnalysisResult(
            answer=candidate_answer,
            evidence=[],
            tool_calls=tool_trace,
            confidence="medium",
            corrections=[],
        )


def analyze(question: str) -> AnalysisResult:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return AnalysisResult(
            answer="ANTHROPIC_API_KEY is required for the data-analysis skill.",
            evidence=[],
            tool_calls=[],
            confidence="low",
        )

    try:
        import anthropic
    except ImportError:
        return AnalysisResult(
            answer='Install analysis dependencies with `python3 -m pip install -e ".[dev]"`.',
            evidence=[],
            tool_calls=[],
            confidence="low",
        )

    client = anthropic.Anthropic(api_key=api_key)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    trace: list[dict[str, Any]] = []

    for _step in range(MAX_STEPS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1400,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_blocks = [
            block for block in response.content if getattr(block, "type", None) == "tool_use"
        ]
        if not tool_blocks:
            text = "".join(
                getattr(block, "text", "") for block in response.content
                if getattr(block, "type", None) == "text"
            )
            candidate = _parse_result(text, trace)
            return reflect(question, trace, candidate.answer, client)

        results = []
        for block in tool_blocks:
            result = TOOLS[block.name](**block.input)
            trace.append({"tool": block.name, "input": block.input, "result": result})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": results})

    return AnalysisResult(
        answer="Analysis exceeded the maximum tool-use step limit.",
        evidence=[],
        tool_calls=trace,
        confidence="low",
    )


def _summary_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "p05": None,
            "p50": None,
            "p95": None,
        }
    return {
        "count": len(values),
        "mean": mean(values),
        "std": population_std(values),
        "min": min(values),
        "max": max(values),
        "p05": percentile(values, 5),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
    }


def _reading_to_dict(row: Reading) -> dict[str, Any]:
    return {
        "id": row.id,
        "city": row.city,
        "observation_ts": row.observation_ts.isoformat(),
        "temperature_2m": row.temperature_2m,
        "apparent_temperature": row.apparent_temperature,
        "precipitation": row.precipitation,
        "wind_speed_10m": row.wind_speed_10m,
        "weather_code": row.weather_code,
        "surface_pressure": row.surface_pressure,
        "pressure_msl": row.pressure_msl,
        "relative_humidity_2m": row.relative_humidity_2m,
        "dew_point_2m": row.dew_point_2m,
        "wind_gusts_10m": row.wind_gusts_10m,
        "cloud_cover": row.cloud_cover,
        "snowfall": row.snowfall,
        "snow_depth": row.snow_depth,
    }


def _event_to_dict(row: Event) -> dict[str, Any]:
    return {
        "id": row.id,
        "city": row.city,
        "event_ts": row.event_ts.isoformat(),
        "event_type": row.event_type,
        "severity": row.severity,
        "metric": row.metric,
        "signal_values": row.signal_values,
        "reason": row.reason,
        "supporting_reading_ids": row.supporting_reading_ids,
        "status": row.status,
        "onset_ts": row.onset_ts.isoformat() if row.onset_ts else None,
        "peak_ts": row.peak_ts.isoformat() if row.peak_ts else None,
        "resolved_ts": row.resolved_ts.isoformat() if row.resolved_ts else None,
        "priority_score": row.priority_score,
        "confidence": row.confidence,
        "detector_name": row.detector_name,
        "dedupe_key": row.dedupe_key,
        "evidence": row.evidence,
    }


def _parse_result(text: str, trace: list[dict[str, Any]]) -> AnalysisResult:
    try:
        parsed = json.loads(text)
        parsed.setdefault("tool_calls", trace)
        return AnalysisResult(**parsed)
    except (json.JSONDecodeError, ValueError):
        return AnalysisResult(
            answer=text.strip(),
            evidence=[],
            tool_calls=trace,
            confidence="medium",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze WatchAgent readings and events.")
    parser.add_argument("question")
    args = parser.parse_args()
    print(analyze(args.question).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
