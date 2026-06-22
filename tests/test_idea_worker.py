"""Unit tests for scripts/idea-worker.py pure logic (no Claude, no network)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "idea-worker.py"


def _load():
    spec = importlib.util.spec_from_file_location("idea_worker", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load()
NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


# -- decide_action / is_waiting --------------------------------------------- #
def test_decide_action():
    assert w.decide_action({"state": "new"}) == "plan"
    assert w.decide_action({"state": "approved"}) == "implement"
    assert w.decide_action({"state": "planned"}) is None
    assert w.decide_action({"state": "done"}) is None


def test_is_waiting_unpaused():
    assert w.is_waiting({"state": "new"}, NOW, auto_continue=False) is False


def test_is_waiting_paused_blocks():
    idea = {"paused": True, "resume_after": w.iso(NOW + timedelta(hours=3))}
    assert w.is_waiting(idea, NOW, auto_continue=False) is True


def test_is_waiting_auto_continue_past_due():
    idea = {"paused": True, "resume_after": w.iso(NOW - timedelta(minutes=1))}
    assert w.is_waiting(idea, NOW, auto_continue=True) is False
    # still waiting when not yet due
    idea2 = {"paused": True, "resume_after": w.iso(NOW + timedelta(hours=1))}
    assert w.is_waiting(idea2, NOW, auto_continue=True) is True


# -- parse_resume_after ----------------------------------------------------- #
def test_parse_fallback_window():
    dt, source = w.parse_resume_after("nothing useful here", NOW, window_hours=5)
    assert source == "fallback"
    assert dt == NOW + timedelta(hours=5)


def test_parse_relative():
    dt, source = w.parse_resume_after("please try again in 2 hours", NOW)
    assert source == "relative"
    assert abs((dt - (NOW + timedelta(hours=2))).total_seconds()) < 2


def test_parse_iso():
    dt, source = w.parse_resume_after("limit resets at 2030-01-01T00:00:00Z", NOW)
    assert source == "iso"
    assert dt.year == 2030


def test_parse_epoch():
    dt, source = w.parse_resume_after("retry after 1893456000 seconds", NOW)
    assert source == "epoch"
    assert dt.tzinfo is not None


def test_parse_clock_future():
    dt, source = w.parse_resume_after("usage limit reached, resets at 11pm", NOW)
    assert source == "clock"
    assert dt > NOW


def test_human_delta():
    assert w.human_delta(NOW, NOW + timedelta(hours=2, minutes=5)) == "2h 5m"
    assert w.human_delta(NOW, NOW + timedelta(minutes=45)) == "45m"
    assert w.human_delta(NOW, NOW - timedelta(minutes=5)) == "now"


# -- load/save -------------------------------------------------------------- #
@pytest.fixture
def tmp_ideas(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "IDEAS_DIR", tmp_path / ".agent" / "ideas")
    return tmp_path


def test_load_save_roundtrip(tmp_ideas):
    w.save_idea({"id": "IDEA-2", "state": "new", "text": "two"})
    w.save_idea({"id": "IDEA-10", "state": "approved", "text": "ten"})
    ideas = w.load_ideas()
    assert [i["id"] for i in ideas] == ["IDEA-2", "IDEA-10"]  # numeric sort, not lexical
    assert all("updated_at" in i for i in ideas)


def test_load_skips_garbage(tmp_ideas):
    (tmp_ideas / ".agent" / "ideas").mkdir(parents=True)
    (tmp_ideas / ".agent" / "ideas" / "IDEA-1.json").write_text("not json")
    assert w.load_ideas() == []
