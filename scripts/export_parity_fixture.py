"""Write a parity fixture from the Python simulator for the TypeScript port.

apps/plant-api/test/fixtures/parity.json holds sample values of load(), health(),
value() and the failures() table for the default plant/faults.yaml (seed 42) and
one other seed, so the Worker's sim.ts can be checked bit-for-bit (well, to 1e-9).

    uv run python scripts/export_parity_fixture.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from plant.sim import TAGS, Plant

OUT = Path("apps/plant-api/test/fixtures/parity.json")
HOURS = [0.0, 1.0, 17.0, 100.0, 1234.5, 3200.0, 4799.0, 4800.0, 5999.0, 6200.0, 6479.0, 6480.0, 6481.0, 7300.0, 7799.0, 7800.0, 8000.0, 8759.0]


def sample(plant: Plant) -> dict:
    values = []
    for asset, tags in TAGS.items():
        for tag in tags:
            for h in HOURS:
                v = plant.value(asset, tag, h)
                values.append({"asset": asset, "tag": tag, "h": h, "v": None if math.isnan(v) else v})
    return {
        "seed": plant.seed,
        "start": plant.start.isoformat(),
        "horizon_h": plant.horizon_h,
        "load": [{"h": h, "v": plant.load(h)} for h in HOURS],
        "health": [{"asset": a, "h": h, "v": plant.health(a, h)} for a in TAGS for h in HOURS],
        "values": values,
        "failures": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()} for f in plant.failures()],
        "events": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.items()} for e in plant.events()],
    }


def main() -> None:
    fixture = {"plants": [sample(Plant.from_yaml(seed=42)), sample(Plant.from_yaml(seed=7))]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=1), encoding="utf-8")
    n = sum(len(p["values"]) for p in fixture["plants"])
    print(f"wrote {OUT.as_posix()} ({n} sensor samples)")


if __name__ == "__main__":
    main()
