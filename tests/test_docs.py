"""docs/plant.md is the plant's single source of truth for people and agents; keep it equal to plant/sim.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plant.sim import FAULT_MODES, MODE_ASSETS, TAGS

DOC = Path("docs/plant.md").read_text(encoding="utf-8")
FAMILIES = {"GTx": ["GT1", "GT2"], "HRSGx": ["HRSG1", "HRSG2"], "BFPx": ["BFP1", "BFP2", "BFP3"],
            "CWPx": ["CWP1", "CWP2"], "CTFx": ["CTF1", "CTF2"]}


def doc_tags() -> dict[str, tuple[float, float, float]]:
    """`| `ASSET.TAG` | unit | baseline | load gain | noise |` rows, family rows expanded."""
    out: dict[str, tuple[float, float, float]] = {}
    for m in re.finditer(r"^\| `([A-Za-z0-9]+)\.([A-Z0-9_]+)`\s*\|[^|]*\|\s*([-\d.]+)\s*\|\s*([-\d.]+)\s*\|\s*([-\d.]+)\s*\|", DOC, re.M):
        prefix, tag, *nums = m.groups()
        for asset in FAMILIES.get(prefix, [prefix]):
            key = f"{asset}.{tag}"
            if key not in out:  # a specific row (GT1.BRG_TEMP_2) wins over the family row
                out[key] = tuple(float(x) for x in nums)
    return out


def test_every_tag_documented_with_the_simulator_numbers() -> None:
    docs = doc_tags()
    sim = {f"{a}.{t}": (s.baseline, s.load_gain, s.noise) for a, tags in TAGS.items() for t, s in tags.items()}
    assert set(docs) == set(sim), sorted(set(docs) ^ set(sim))
    for key, nums in sim.items():
        assert docs[key] == pytest.approx(nums), key


def test_every_asset_and_mode_documented() -> None:
    for asset in TAGS:
        assert re.search(rf"^\| `{asset}`\s*\|", DOC, re.M), asset
    for mode, symptoms in FAULT_MODES.items():
        row = re.search(rf"^\| `{mode}`\s*\|([^|]*)\|(.*)$", DOC, re.M)
        assert row, mode
        assert {a.strip() for a in row.group(1).split(",")} == set(MODE_ASSETS[mode]), mode
        for tag, (gain, shape) in symptoms.items():
            m = re.search(rf"`{tag}` \(([-+\d.]+), ([\d.]+)\)", row.group(2))
            assert m and float(m.group(1)) == pytest.approx(gain) and float(m.group(2)) == pytest.approx(shape), (mode, tag)
