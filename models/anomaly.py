"""Fleet anomaly detection with rolling z-scores (anomaly skill).

Method
------
For every non-dead tag on a target asset, compute a 30-day rolling mean/std
using ONLY samples strictly before each timestamp (the window is closed on the
left so the current point never sits in its own baseline). z = (value -
mean) / std. A point is "hot" when |z| > threshold; a run of >= min_run
consecutive hot hours is a flag. NaN values (and points inside an asset's
outage windows) are dropped from the baseline and break any run.

Usage
-----
    uv run python -m models.anomaly score --as-of 2024-09-20T00:00:00 \
        [--window-days 7] [--input path.parquet] [--seed 42] [--round 1]

Never reads ground truth; only writes to reports/ and models/artifacts/.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_ASSETS = ["GT1", "BFP1", "BFP2", "CTF1"]
DERIVED_DIR = Path("data/derived")
REPORTS_DIR = Path("reports")
ARTIFACT_DIR = Path("models/artifacts")

# Failure-mode symptom map from .claude/skills/maintenance-domain/SKILL.md.
# key: (asset family, TAG) -> list of (mode, expected_sign, note)
FAILURE_MODES: dict[tuple[str, str], list[tuple[str, int, str]]] = {
    ("BFP", "VIB_DE"): [("bearing_wear", +1, "DE vibration rising: primary bearing_wear symptom")],
    ("BFP", "BRG_TEMP_DE"): [("bearing_wear", +1, "DE bearing temperature rising: bearing_wear symptom")],
    ("BFP", "VIB_NDE"): [("bearing_wear", +1, "NDE vibration rising: weak bearing_wear symptom (gain 0.8)")],
    ("BFP", "MOTOR_CURR"): [("bearing_wear", +1, "motor current rising: secondary bearing_wear symptom")],
    ("GT1", "CDP"): [("compressor_fouling", -1, "CDP falling: compressor_fouling symptom")],
    ("GT1", "EXH_TEMP"): [("compressor_fouling", +1, "exhaust temperature rising: compressor_fouling symptom")],
    ("GT1", "FUEL_FLOW"): [("compressor_fouling", +1, "fuel flow rising at load: compressor_fouling symptom")],
    ("GT1", "LOAD_MW"): [("compressor_fouling", -1, "MW output falling: compressor_fouling symptom")],
    ("CTF1", "GBX_OIL_TEMP"): [("gearbox_wear", +1, "gearbox oil temperature rising: leading gearbox_wear symptom")],
    ("CTF1", "VIB"): [("gearbox_wear", +1, "fan vibration rising: late gearbox_wear symptom")],
    ("CTF1", "MOTOR_CURR"): [("gearbox_wear", +1, "motor current rising: secondary gearbox_wear symptom")],
}
MODE_TAGS = {
    "bearing_wear": ["VIB_DE", "BRG_TEMP_DE", "VIB_NDE", "MOTOR_CURR"],
    "compressor_fouling": ["CDP", "EXH_TEMP", "FUEL_FLOW", "LOAD_MW"],
    "gearbox_wear": ["GBX_OIL_TEMP", "VIB", "MOTOR_CURR"],
}


@dataclass
class Config:
    baseline_days: int = 30
    min_periods_hours: int = 24 * 20
    z_threshold: float = 3.0
    min_run_hours: int = 6
    window_days: int = 7
    target_assets: list[str] = field(default_factory=lambda: list(TARGET_ASSETS))


# --------------------------------------------------------------------------- core


def rolling_baseline(s: pd.Series, cfg: Config) -> tuple[pd.Series, pd.Series]:
    """Rolling mean/std over a time window of `baseline_days` STRICTLY before
    each timestamp. `s` must have a sorted DatetimeIndex; NaNs are ignored by
    the window and never contribute (they also count against min_periods)."""
    win = pd.Timedelta(days=cfg.baseline_days)
    # closed="left" excludes the current row from its own window.
    roll = s.rolling(win, min_periods=cfg.min_periods_hours, closed="left")
    return roll.mean(), roll.std(ddof=1)


def zscores(s: pd.Series, cfg: Config) -> pd.DataFrame:
    mean, std = rolling_baseline(s, cfg)
    std = std.where(std > 0)
    z = (s - mean) / std
    return pd.DataFrame({"value": s, "mean": mean, "std": std, "z": z})


def find_runs(z: pd.Series, cfg: Config) -> list[dict]:
    """Return consecutive-hour runs where |z| > threshold. A NaN z or a gap in
    the hourly index breaks the run."""
    hot = (z.abs() > cfg.z_threshold).fillna(False).to_numpy()
    idx = z.index
    runs: list[tuple[int, int]] = []
    start = None
    for i in range(len(hot)):
        contiguous = i > 0 and (idx[i] - idx[i - 1]) == pd.Timedelta(hours=1)
        if hot[i] and start is not None and contiguous:
            continue
        if start is not None:
            runs.append((start, i - 1))
            start = None
        if hot[i]:
            start = i
    if start is not None:
        runs.append((start, len(hot) - 1))
    out = []
    for a, b in runs:
        n = b - a + 1
        if n < cfg.min_run_hours:
            continue
        zz = z.iloc[a : b + 1]
        k = int(zz.abs().to_numpy().argmax())
        out.append(
            {
                "first_flag_ts": idx[a],
                "last_flag_ts": idx[b],
                "hours_flagged": int(n),
                "severity": float(zz.abs().max()),
                "z_peak_signed": float(zz.iloc[k]),
                "z_at_end": float(zz.iloc[-1]),
            }
        )
    return out


def apply_exclusions(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Drop dead tags; set values inside an asset's outage windows to NaN."""
    df = df.copy()
    dead = set(meta.get("dead_tags", []))
    if "dead" in df.columns:
        dead |= set(df.loc[df["dead"].astype(bool), "tag"].unique())
    df = df[~df["tag"].isin(dead)]
    for w in meta.get("outage_windows", []):
        m = (
            (df["asset"] == w["asset"])
            & (df["timestamp"] >= pd.Timestamp(w["from"]))
            & (df["timestamp"] <= pd.Timestamp(w["to"]))
        )
        df.loc[m, "value"] = np.nan
    return df


def score_table(df: pd.DataFrame, meta: dict, as_of: pd.Timestamp, cfg: Config):
    """Score a long-format table. Returns (flags, per_tag_stats)."""
    df = apply_exclusions(df, meta)
    df = df[df["asset"].isin(cfg.target_assets) & (df["timestamp"] <= as_of)]
    win_start = as_of - pd.Timedelta(days=cfg.window_days)
    flags, stats = [], []
    for (asset, tag), g in sorted(df.groupby(["asset", "tag"]), key=lambda kv: kv[0]):
        s = (
            g.sort_values("timestamp")
            .drop_duplicates("timestamp")
            .set_index("timestamp")["value"]
            .astype(float)
        )
        zs = zscores(s, cfg)
        scored = zs["z"].dropna()
        in_win = scored[scored.index >= win_start]
        stats.append(
            {
                "asset": asset,
                "tag": tag,
                "n_points": int(len(s)),
                "n_scored": int(len(scored)),
                "n_nan_values": int(s.isna().sum()),
                "max_abs_z": float(scored.abs().max()) if len(scored) else None,
                "max_abs_z_in_window": float(in_win.abs().max()) if len(in_win) else None,
            }
        )
        for r in find_runs(zs["z"], cfg):
            if r["last_flag_ts"] >= win_start and r["first_flag_ts"] <= as_of:
                r.update(asset=asset, tag=tag)
                r.update(interpretation=interpret(asset, tag, r["z_peak_signed"]))
                flags.append(r)
    return flags, stats


# ------------------------------------------------------------------ interpretation


def _family(asset: str) -> str:
    return "BFP" if asset.startswith("BFP") else asset


def interpret(asset: str, tag: str, z_signed: float) -> str:
    short = tag.split(".", 1)[1]
    modes = FAILURE_MODES.get((_family(asset), short))
    direction = "up" if z_signed > 0 else "down"
    if not modes:
        return f"{short} {direction}: not a symptom tag of any documented failure mode"
    for mode, sign, note in modes:
        if np.sign(z_signed) == sign:
            return note
    return f"{short} {direction}: direction opposite to {modes[0][0]} symptom; no documented mode matches"


def asset_rollup(flags: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    by_asset: dict[str, list[dict]] = {}
    for f in flags:
        by_asset.setdefault(f["asset"], []).append(f)
    for asset, fs in sorted(by_asset.items()):
        fam = _family(asset)
        consistent: dict[str, list[str]] = {}
        unexplained = []
        for f in fs:
            short = f["tag"].split(".", 1)[1]
            modes = FAILURE_MODES.get((fam, short), [])
            hit = [m for m, sign, _ in modes if np.sign(f["z_peak_signed"]) == sign]
            if hit:
                for m in hit:
                    consistent.setdefault(m, []).append(short)
            else:
                unexplained.append(f"{short}({'+' if f['z_peak_signed'] > 0 else '-'})")
        best = max(consistent.items(), key=lambda kv: len(set(kv[1])))[0] if consistent else None
        out[asset] = {
            "n_flags": len(fs),
            "max_severity": max(f["severity"] for f in fs),
            "most_consistent_mode": best,
            "supporting_tags": sorted(set(consistent.get(best, []))) if best else [],
            "expected_symptom_tags": MODE_TAGS.get(best, []) if best else [],
            "unexplained_tags": sorted(set(unexplained)),
        }
    return out


# ------------------------------------------------------------------------- output


def _ts(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M")


def write_report(path: Path, flags, rollup, cfg: Config, info: dict) -> None:
    lines = [
        f"# Anomaly report - fleet - as of {info['as_of']}",
        "",
        f"Scoring window: {info['window_start']} to {info['as_of']} ({cfg.window_days} days). "
        f"Run id `{info['run_id']}`.",
        "",
        "## Flags",
        "",
    ]
    if flags:
        lines.append(
            "| asset | tag | first_flag_ts | last_flag_ts | hours_flagged | severity | z_at_end | interpretation |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for f in flags:
            sign = "+" if f["z_peak_signed"] > 0 else "-"
            lines.append(
                f"| {f['asset']} | {f['tag']} | {_ts(f['first_flag_ts'])} | {_ts(f['last_flag_ts'])} | "
                f"{f['hours_flagged']} | {f['severity']:.2f} ({sign}) | {f['z_at_end']:+.2f} | {f['interpretation']} |"
            )
    else:
        lines.append("No flags in the scoring window.")
    lines += ["", "## Per-asset summary", ""]
    for asset in cfg.target_assets:
        r = rollup.get(asset)
        if not r:
            lines.append(f"- **{asset}**: no flags.")
            continue
        mode = r["most_consistent_mode"] or "none"
        lines.append(
            f"- **{asset}**: {r['n_flags']} flag(s), max severity {r['max_severity']:.2f}. "
            f"Most consistent mode: **{mode}** (supported by {', '.join(r['supporting_tags']) or '-'}; "
            f"expected symptom tags {', '.join(r['expected_symptom_tags']) or '-'})."
            + (f" Unexplained: {', '.join(r['unexplained_tags'])}." if r["unexplained_tags"] else "")
        )
    lines += [
        "",
        "## Method and config",
        "",
        f"- Baseline: {cfg.baseline_days}-day rolling mean/std, window closed on the left "
        f"(current point excluded), min_periods = {cfg.min_periods_hours} h, std ddof=1.",
        f"- Flag rule: |z| > {cfg.z_threshold} for >= {cfg.min_run_hours} consecutive hours; "
        "a NaN or an hour gap breaks a run.",
        "- Severity: max |z| within the run; sign in parentheses is the direction of the peak.",
        f"- Exclusions: dead tags {info['dead_tags']}; outage windows {info['outage_windows']} "
        f"(set to NaN before baselining). Only target assets {cfg.target_assets} are scored; PLANT.LOAD is not scored.",
        "- Reported flags: runs intersecting the scoring window; first_flag_ts is the true run start "
        "even if before the window.",
        f"- Input: `{info['input']}` with sidecar `{info['sidecar']}`.",
        f"- Run id `{info['run_id']}`, seed {info['seed']}. No ground truth was read.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def run_score(
    as_of: str,
    window_days: int = 7,
    input_path: str | None = None,
    seed: int = 42,
    round_no: int = 1,
    report_dir: Path = REPORTS_DIR,
    artifact_dir: Path = ARTIFACT_DIR,
) -> dict:
    random.seed(seed)
    np.random.seed(seed)
    as_of_ts = pd.Timestamp(as_of)
    date = as_of_ts.strftime("%Y-%m-%d")
    inp = Path(input_path) if input_path else DERIVED_DIR / f"anomaly_fleet_{date}.parquet"
    sidecar = inp.with_suffix(".meta.json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    cfg = Config(window_days=window_days)
    df = pd.read_parquet(inp)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    flags, stats = score_table(df, meta, as_of_ts, cfg)
    rollup = asset_rollup(flags)
    run_id = f"anomaly_fleet_{date}_r{round_no}"
    info = {
        "run_id": run_id,
        "as_of": as_of_ts.isoformat(),
        "window_start": (as_of_ts - pd.Timedelta(days=window_days)).isoformat(),
        "seed": seed,
        "input": inp.as_posix(),
        "sidecar": sidecar.as_posix() if sidecar.exists() else None,
        "dead_tags": meta.get("dead_tags", []),
        "outage_windows": meta.get("outage_windows", []),
    }
    report_path = report_dir / f"anomaly_{date}.md"
    write_report(report_path, flags, rollup, cfg, info)
    artefact = {
        "run_id": run_id,
        "task": "anomaly",
        "target": "fleet",
        "as_of": info["as_of"],
        "window": {"start": info["window_start"], "end": info["as_of"], "days": window_days},
        "seed": seed,
        "config": asdict(cfg),
        "input": {
            "parquet": info["input"],
            "sidecar": info["sidecar"],
            "dead_tags": info["dead_tags"],
            "outage_windows": info["outage_windows"],
        },
        "flags": flags,
        "per_asset": rollup,
        "per_tag_stats": stats,
        "metrics": {
            "n_flags": len(flags),
            "n_assets_flagged": len({f["asset"] for f in flags}),
            "n_tags_flagged": len({f["tag"] for f in flags}),
            "n_tags_scored": len(stats),
            "assets_flagged": sorted({f["asset"] for f in flags}),
        },
        "report": report_path.as_posix(),
    }
    artifact_dir.mkdir(parents=True, exist_ok=True)
    art_path = artifact_dir / f"{run_id}.json"
    art_path.write_text(json.dumps(_jsonable(artefact), indent=2), encoding="utf-8")
    artefact["artifact_path"] = art_path.as_posix()
    return artefact


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="models.anomaly", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--as-of", required=True)
    s.add_argument("--window-days", type=int, default=7)
    s.add_argument("--input", default=None)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--round", type=int, default=1)
    a = p.parse_args(argv)
    art = run_score(a.as_of, a.window_days, a.input, a.seed, a.round)
    print(f"run_id={art['run_id']} report={art['report']} artifact={art['artifact_path']}")
    print(json.dumps(art["metrics"]))
    for f in art["flags"]:
        print(
            f"{f['asset']:5s} {f['tag']:18s} {_ts(f['first_flag_ts'])} -> {_ts(f['last_flag_ts'])} "
            f"n={f['hours_flagged']:4d} sev={f['severity']:.2f} zpeak={f['z_peak_signed']:+.2f} | {f['interpretation']}"
        )


if __name__ == "__main__":
    main()
