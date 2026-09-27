"""Grading helpers of scripts/eval_assistant.py (no assistant, no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_spec = importlib.util.spec_from_file_location("eval_assistant", Path(__file__).resolve().parents[1] / "scripts" / "eval_assistant.py")
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)


def test_numbers_skip_dates_times_markers_and_counts() -> None:
    text = "1. BFP2.VIB_DE 3.1 mm/s at 2024-09-20 13:00 (baseline 1.8), up 72%; 3 alerts in 7 days; p_fail 0.991"
    assert E.numbers(text) == [3.1, 1.8, 72.0, 0.991]


def test_facts_include_ratios_percentages_and_text_numbers() -> None:
    known = E.facts({"reading": {"value": 3.1, "unit": "mm/s", "baseline": 1.8}, "risk": {"p_fail": 0.991}, "s": "stop above 4.5 mm/s"})
    for v in (3.1, 1.8, 99.1, 4.5, 3.1 / 1.8, 1.3):
        assert E.matches(v, known), v
    assert not E.matches(7.7, known)
    assert E.matches(1.72, known) and E.matches(99.0, known)  # rounded as shown


def test_grade_flags_invented_numbers_missing_facts_drafts_and_playbook() -> None:
    known = E.facts({"value": 3.1, "baseline": 1.8})
    pb = {"bearing_wear": {"actions": "- Take a vibration spectrum at the drive-end bearing\n- Re-grease the bearing per the lubrication chart"}}
    q = {"q": "What should we do?", "expect": [["bearing"]], "playbook": "bearing_wear"}
    good = E.grade(q, "Per the playbook for bearing wear: take a vibration spectrum at the drive end; re-grease per the lubrication chart. VIB_DE 3.1 mm/s.", [], known, pb)
    assert good["pass"] and good["playbook_hits"] == 2
    bad = E.grade(q, "Bearing: VIB_DE is 4.2 mm/s, replace it.", [], known, pb)
    assert not bad["pass"] and bad["invented"] == [4.2] and bad["playbook"] is False
    wo = {"q": "Create a work order", "expect": [["confirm"]], "forbid": ["work order was created"], "draft": True}
    assert E.grade(wo, "Drafted; press Confirm.", [{"draft_id": "x"}], known, pb)["pass"]
    assert not E.grade(wo, "The work order was created. Confirm later.", [{"draft_id": "x"}], known, pb)["pass"]
    assert not E.grade(wo, "Press confirm.", [], known, pb)["pass"]


def test_golden_set_shape() -> None:
    g = yaml.safe_load(Path("tests/golden/assistant.yaml").read_text(encoding="utf-8"))
    assert len(g["scenarios"]) == 4 and all(len(s["questions"]) == 5 for s in g["scenarios"])
    assert {s.get("mode") for s in g["scenarios"]} == {"bearing_wear", "compressor_fouling", "gearbox_wear", None}


def test_numbers_skip_date_ranges_and_written_dates() -> None:
    text = "data ends around 2024-07-18/19; outage 19-22 July; seen Sep 27; VIB_DE 1.91 mm/s"
    assert E.numbers(text) == [1.91]
