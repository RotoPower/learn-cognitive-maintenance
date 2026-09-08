"""Export one or more months of simulated historian data to parquet.

    uv run python scripts/export.py --month 2024-08
    uv run python scripts/export.py --month 2024-07 --month 2024-08 --out-dir data/raw

Writes ``<out-dir>/readings_<YYYY-MM>.parquet`` (wide: timestamp + one column
per tag) taken from the simulator with its realism quirks intact (dead tag,
missing hours, duplicated timestamp, outage), exactly as a historian export
would look. Nothing from ground truth is included.

Note: ``data/raw`` is read-only for Claude Code (CLAUDE.md + guard hook), so
run this from your own terminal when the target is ``data/raw``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from plant.sim import Plant


def export_month(plant: Plant, month: str, out_dir: Path, frame: pd.DataFrame | None = None) -> Path:
    df = frame if frame is not None else plant.generate(quirks=True)
    start = pd.Timestamp(month + "-01")
    end = start + pd.offsets.MonthBegin(1)
    part = df[(df["timestamp"] >= start) & (df["timestamp"] < end)].reset_index(drop=True)
    if part.empty:
        raise SystemExit(f"no data in {month}: simulator covers {df['timestamp'].min()} .. {df['timestamp'].max()}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"readings_{month}.parquet"
    part.to_parquet(path, index=False)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", action="append", required=True, help="YYYY-MM; repeatable")
    ap.add_argument("--out-dir", default="data/raw")
    ap.add_argument("--config", default=None, help="faults.yaml (default: plant/faults.yaml)")
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)

    plant = Plant.from_yaml(a.config, seed=a.seed) if a.config else Plant.from_yaml(seed=a.seed)
    frame = plant.generate(quirks=True)
    for m in a.month:
        p = export_month(plant, m, Path(a.out_dir), frame)
        n = len(pd.read_parquet(p))
        print(f"wrote {p.as_posix()} ({n} rows)")


if __name__ == "__main__":
    main()
