"""Deterministic plant simulator.

Every sensor value is a pure function of ``(seed, asset, tag, sim_time)`` given
the scripted scenario in ``plant/faults.yaml``:

    value = baseline + symptom(1 - health) + load_gain * (load - REF_LOAD) + noise

Randomness is hash-based (blake2b over the tuple), so there is no RNG state and
values can be evaluated at any time in any order.  Ground truth (``health`` and
``failures``) lives here too, but per CLAUDE.md it is only for the validator and
must never be fed to a model.

Run ``uv run python -m plant.sim --out data/sim`` to write a hourly CSV plus a
``ground_truth.json`` sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml

DEFAULT_CONFIG = Path(__file__).with_name("faults.yaml")
REF_LOAD = 0.75
PLANT_LOAD_TAG = "PLANT.LOAD"

# --------------------------------------------------------------------------- #
# Plant description (mirror of docs/plant.md)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TagSpec:
    baseline: float
    load_gain: float
    noise: float
    dead: bool = False  # transmitter failed: reads ``baseline`` forever


_PUMP_TAGS = {
    "FLOW": TagSpec(260.0, 200.0, 2.5),
    "DISCH_PRESS": TagSpec(165.0, 18.0, 0.7),
    "VIB_DE": TagSpec(1.8, 0.6, 0.10),
    "VIB_NDE": TagSpec(1.5, 0.5, 0.10),
    "BRG_TEMP_DE": TagSpec(62.0, 7.0, 0.5),
    "MOTOR_CURR": TagSpec(310.0, 180.0, 2.0),
}

TAGS: dict[str, dict[str, TagSpec]] = {
    "GT1": {
        "LOAD_MW": TagSpec(90.0, 120.0, 0.8),
        "EXH_TEMP": TagSpec(540.0, 60.0, 2.0),
        "CDP": TagSpec(15.5, 5.0, 0.08),
        "FUEL_FLOW": TagSpec(6.2, 5.5, 0.05),
        "VIB_1": TagSpec(2.1, 0.4, 0.12),
        "BRG_TEMP_1": TagSpec(78.0, 6.0, 0.6),
        "BRG_TEMP_2": TagSpec(81.4, 0.0, 0.0, dead=True),
    },
    "BFP1": dict(_PUMP_TAGS),
    "BFP2": dict(_PUMP_TAGS),
    "CTF1": {
        "SPEED": TagSpec(118.0, 0.0, 0.3),
        "VIB": TagSpec(2.4, 0.3, 0.15),
        "GBX_OIL_TEMP": TagSpec(58.0, 9.0, 0.6),
        "MOTOR_CURR": TagSpec(95.0, 25.0, 1.0),
    },
}
ASSETS: tuple[str, ...] = tuple(TAGS)

# mode -> tag -> (gain, shape); symptom = gain * (1 - health) ** shape
FAULT_MODES: dict[str, dict[str, tuple[float, float]]] = {
    "bearing_wear": {
        "VIB_DE": (4.5, 2.0),
        "BRG_TEMP_DE": (22.0, 1.5),
        "VIB_NDE": (0.8, 2.0),
        "MOTOR_CURR": (6.0, 1.0),
    },
    "compressor_fouling": {
        "CDP": (-1.4, 1.0),
        "EXH_TEMP": (28.0, 1.0),
        "FUEL_FLOW": (0.45, 1.0),
        "LOAD_MW": (-5.0, 1.0),
    },
    "gearbox_wear": {
        "GBX_OIL_TEMP": (20.0, 1.5),
        "VIB": (3.5, 2.0),
        "MOTOR_CURR": (7.0, 1.0),
    },
}

HEALTH_SHAPE = 2.5  # health = 1 - x ** HEALTH_SHAPE, accelerating degradation


def canonical_asset(name: str) -> str:
    """``GT-1`` / ``gt1`` -> ``GT1``.  Raises for unknown assets."""
    key = name.replace("-", "").replace("_", "").upper()
    if key not in TAGS:
        raise KeyError(f"unknown asset {name!r}; known: {ASSETS}")
    return key


def split_tag(full: str) -> tuple[str, str]:
    """``BFP2.VIB_DE`` -> ``("BFP2", "VIB_DE")``."""
    asset, _, tag = full.partition(".")
    if not tag:
        raise ValueError(f"tag must look like ASSET.TAG, got {full!r}")
    return asset, tag


# --------------------------------------------------------------------------- #
# Hash-based, stateless randomness
# --------------------------------------------------------------------------- #


def _u01(*parts: Any) -> float:
    """Uniform in [0, 1) that depends only on ``parts``."""
    key = "|".join(repr(p) for p in parts).encode()
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return (int.from_bytes(digest, "little") >> 11) / float(1 << 53)


def _gauss(*parts: Any) -> float:
    """Standard normal via Box-Muller, pure in ``parts``."""
    u1 = _u01(*parts, "g1")
    u2 = _u01(*parts, "g2")
    u1 = max(u1, 1e-300)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


# --------------------------------------------------------------------------- #
# Scenario script
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Scenario:
    asset: str
    mode: str
    onset_h: float
    duration_h: float

    @property
    def failure_h(self) -> float:
        return self.onset_h + self.duration_h


@dataclass(frozen=True)
class Outage:
    asset: str
    from_h: float
    to_h: float


@dataclass(frozen=True)
class Quirks:
    dead_tags: tuple[str, ...] = ()
    missing_hours: tuple[tuple[float, int], ...] = ()  # (from_h, n_hours)
    duplicated_timestamps: tuple[float, ...] = ()  # hours


def _as_datetime(d: Any) -> datetime:
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day)
    return pd.Timestamp(d).to_pydatetime()


class Plant:
    """A scripted plant.  All public methods are pure in their arguments."""

    def __init__(
        self,
        seed: int,
        start: datetime,
        horizon_days: float,
        scenarios: Iterable[Scenario] = (),
        outages: Iterable[Outage] = (),
        quirks: Quirks | None = None,
    ) -> None:
        self.seed = int(seed)
        self.start = _as_datetime(start)
        self.horizon_h = float(horizon_days) * 24.0
        self.scenarios = tuple(sorted(scenarios, key=lambda s: (s.asset, s.onset_h)))
        self.outages = tuple(outages)
        self.quirks = quirks or Quirks()
        for s in self.scenarios:
            if s.mode not in FAULT_MODES:
                raise ValueError(f"unknown fault mode {s.mode!r}")
        self._by_asset: dict[str, tuple[Scenario, ...]] = {
            a: tuple(s for s in self.scenarios if s.asset == a) for a in ASSETS
        }
        # scenarios on one asset must not overlap (health would be ambiguous)
        for a, ss in self._by_asset.items():
            for prev, nxt in zip(ss, ss[1:]):
                if nxt.onset_h < prev.failure_h:
                    raise ValueError(f"overlapping scenarios on {a}")

    # ----- construction --------------------------------------------------- #

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_CONFIG, seed: int | None = None) -> "Plant":
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_config(cfg, seed=seed)

    @classmethod
    def from_config(cls, cfg: dict[str, Any], seed: int | None = None) -> "Plant":
        scenarios, outages = [], []
        for item in cfg.get("scenarios", []):
            asset = canonical_asset(item["asset"])
            if "mode" in item:
                scenarios.append(
                    Scenario(asset, item["mode"], item["onset_day"] * 24.0, item["duration_days"] * 24.0)
                )
            elif item.get("event") == "sensor_outage":
                outages.append(Outage(asset, item["from_day"] * 24.0, item["to_day"] * 24.0))
            else:
                raise ValueError(f"cannot interpret scenario entry {item!r}")
        q = cfg.get("quirks", {}) or {}
        quirks = Quirks(
            dead_tags=tuple(q.get("dead_tags", [])),
            missing_hours=tuple((m["from_day"] * 24.0, int(m["hours"])) for m in q.get("missing_hours", [])),
            duplicated_timestamps=tuple(d["day"] * 24.0 + d["hour"] for d in q.get("duplicated_timestamps", [])),
        )
        return cls(
            seed=cfg["seed"] if seed is None else seed,
            start=cfg["start"],
            horizon_days=cfg["horizon_days"],
            scenarios=scenarios,
            outages=outages,
            quirks=quirks,
        )

    # ----- time ------------------------------------------------------------ #

    def to_hours(self, t: Any) -> float:
        """datetime / Timestamp / date / number -> hours since ``start``."""
        if isinstance(t, (int, float, np.integer, np.floating)):
            return float(t)
        return (_as_datetime(t) - self.start).total_seconds() / 3600.0

    def to_timestamp(self, hours: float) -> datetime:
        return self.start + timedelta(hours=float(hours))

    def timestamps(self, freq_h: float = 1.0) -> list[datetime]:
        n = int(math.floor(self.horizon_h / freq_h))
        return [self.to_timestamp(i * freq_h) for i in range(n)]

    def tags(self) -> list[str]:
        out = [PLANT_LOAD_TAG]
        for asset, spec in TAGS.items():
            out.extend(f"{asset}.{tag}" for tag in spec)
        return out

    # ----- hidden state ---------------------------------------------------- #

    def load(self, t: Any) -> float:
        """Plant load factor in [0.45, 1.0]: daily + weekly cycle + slow drift."""
        h = self.to_hours(t)
        day = h / 24.0
        hour = h % 24.0
        d0 = math.floor(day)
        frac = day - d0
        daily = 0.5 * (1.0 - math.cos(2.0 * math.pi * (hour - 4.0) / 24.0))  # low 04:00, high 16:00
        dow = (self.start.weekday() + d0) % 7
        weekend = -0.10 if dow >= 5 else 0.0
        j0 = _u01(self.seed, "PLANT", "LOAD", d0)
        j1 = _u01(self.seed, "PLANT", "LOAD", d0 + 1)
        slow = 0.08 * (2.0 * ((1.0 - frac) * j0 + frac * j1) - 1.0)
        return float(min(1.0, max(0.45, 0.55 + 0.30 * daily + weekend + slow)))

    def active_scenario(self, asset: str, t: Any) -> Scenario | None:
        """Scenario degrading ``asset`` at ``t`` (onset <= t <= failure), if any."""
        asset = canonical_asset(asset)
        h = self.to_hours(t)
        for s in self._by_asset[asset]:
            if s.onset_h <= h <= s.failure_h:
                return s
        return None

    def health(self, asset: str, t: Any) -> float:
        """GROUND TRUTH. Hidden health in [0, 1]; 1 = as new, 0 = failed."""
        s = self.active_scenario(asset, t)
        if s is None:
            return 1.0
        x = (self.to_hours(t) - s.onset_h) / s.duration_h
        return float(max(0.0, 1.0 - x**HEALTH_SHAPE))

    def in_outage(self, asset: str, t: Any) -> bool:
        asset = canonical_asset(asset)
        h = self.to_hours(t)
        return any(o.asset == asset and o.from_h <= h < o.to_h for o in self.outages)

    # ----- sensors --------------------------------------------------------- #

    def value(self, asset: str, tag: str, t: Any) -> float:
        """Sensor reading. Pure in (seed, asset, tag, t). NaN during an outage."""
        if asset.upper() == "PLANT" and tag == "LOAD":
            return self.load(t)
        asset = canonical_asset(asset)
        try:
            spec = TAGS[asset][tag]
        except KeyError:
            raise KeyError(f"unknown tag {asset}.{tag}") from None
        if spec.dead or f"{asset}.{tag}" in self.quirks.dead_tags:
            return spec.baseline
        if self.in_outage(asset, t):
            return math.nan

        h = self.to_hours(t)
        load_term = spec.load_gain * (self.load(h) - REF_LOAD)

        symptom = 0.0
        s = self.active_scenario(asset, h)
        if s is not None:
            gs = FAULT_MODES[s.mode].get(tag)
            if gs is not None:
                gain, shape = gs
                symptom = gain * (1.0 - self.health(asset, h)) ** shape

        noise = spec.noise * _gauss(self.seed, asset, tag, h)
        return spec.baseline + symptom + load_term + noise

    def value_by_tag(self, full_tag: str, t: Any) -> float:
        return self.value(*split_tag(full_tag), t)

    # ----- ground truth ---------------------------------------------------- #

    def failures(self) -> list[dict[str, Any]]:
        """GROUND TRUTH. One record per scripted failure. Validator use only."""
        return [
            {
                "asset": s.asset,
                "mode": s.mode,
                "onset": self.to_timestamp(s.onset_h),
                "failure": self.to_timestamp(s.failure_h),
                "repair": self.to_timestamp(s.failure_h),
            }
            for s in sorted(self.scenarios, key=lambda s: s.onset_h)
        ]

    def events(self) -> list[dict[str, Any]]:
        """Non-failure events (sensor outages)."""
        return [
            {"asset": o.asset, "event": "sensor_outage", "from": self.to_timestamp(o.from_h), "to": self.to_timestamp(o.to_h)}
            for o in self.outages
        ]

    def ground_truth(self) -> dict[str, Any]:
        return {"seed": self.seed, "failures": self.failures(), "events": self.events()}

    # ----- bulk generation ------------------------------------------------ #

    def generate(self, freq_h: float = 1.0, quirks: bool = True) -> pd.DataFrame:
        """Hourly wide table: ``timestamp`` column + one column per tag.

        With ``quirks=True`` the table also carries the realism defects from the
        config: a block of missing hours and a duplicated timestamp row (the dead
        tag is already constant from ``value``).
        """
        ts = self.timestamps(freq_h)
        hours = [self.to_hours(t) for t in ts]
        cols: dict[str, list[float]] = {"timestamp": ts}  # type: ignore[dict-item]
        for full in self.tags():
            asset, tag = split_tag(full)
            cols[full] = [self.value(asset, tag, h) for h in hours]
        df = pd.DataFrame(cols)

        if not quirks:
            return df

        h = np.asarray(hours)
        keep = np.ones(len(df), dtype=bool)
        for from_h, n in self.quirks.missing_hours:
            keep &= ~((h >= from_h) & (h < from_h + n * freq_h))
        df = df[keep]

        dup_idx: list[int] = []
        for dh in self.quirks.duplicated_timestamps:
            hit = np.flatnonzero(np.isclose(h[keep], dh))
            if hit.size:
                dup_idx.append(int(hit[0]))
        if dup_idx:
            parts = []
            last = 0
            for i in sorted(dup_idx):
                parts.append(df.iloc[last : i + 1])
                parts.append(df.iloc[i : i + 1])  # the duplicate, identical values
                last = i + 1
            parts.append(df.iloc[last:])
            df = pd.concat(parts)
        return df.reset_index(drop=True)

    def write(self, out_dir: str | Path, freq_h: float = 1.0) -> dict[str, Path]:
        """Write ``sensors.csv`` and validator-only ``ground_truth.json``."""
        out = Path(out_dir)
        if "raw" in out.parts:
            raise PermissionError("data/raw/ is read-only (CLAUDE.md); write elsewhere")
        out.mkdir(parents=True, exist_ok=True)
        sensors = out / "sensors.csv"
        truth = out / "ground_truth.json"
        self.generate(freq_h).to_csv(sensors, index=False, date_format="%Y-%m-%dT%H:%M:%S")
        truth.write_text(json.dumps(self.ground_truth(), default=str, indent=2), encoding="utf-8")
        return {"sensors": sensors, "ground_truth": truth}


# --------------------------------------------------------------------------- #
# Module-level convenience: the literal (seed, asset, tag, sim_time) function
# --------------------------------------------------------------------------- #

_PLANTS: dict[tuple[int, str], Plant] = {}


def plant(seed: int | None = None, config: str | Path = DEFAULT_CONFIG) -> Plant:
    key_seed = -1 if seed is None else int(seed)
    key = (key_seed, str(Path(config).resolve()))
    if key not in _PLANTS:
        _PLANTS[key] = Plant.from_yaml(config, seed=seed)
    return _PLANTS[key]


def value(seed: int, asset: str, tag: str, sim_time: Any, config: str | Path = DEFAULT_CONFIG) -> float:
    """Sensor value as a pure function of (seed, asset, tag, sim_time)."""
    return plant(seed, config).value(asset, tag, sim_time)


def failures(seed: int | None = None, config: str | Path = DEFAULT_CONFIG) -> list[dict[str, Any]]:
    """GROUND TRUTH failure table. Validator use only."""
    return plant(seed, config).failures()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate simulated plant data.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--seed", type=int, default=None, help="override seed from config")
    ap.add_argument("--out", default="data/sim", help="output directory (never data/raw)")
    ap.add_argument("--freq-hours", type=float, default=1.0)
    args = ap.parse_args(argv)
    p = Plant.from_yaml(args.config, seed=args.seed)
    paths = p.write(args.out, freq_h=args.freq_hours)
    for k, v in paths.items():
        print(f"{k}: {v.as_posix()}")


if __name__ == "__main__":
    main()
