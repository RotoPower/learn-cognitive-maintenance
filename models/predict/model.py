"""Logistic-regression failure predictor (predict skill).

train()  -> artefact dict: feature names, scaler stats, coefficients, metrics, run id
score()  -> per-asset probability of failure within the horizon as of a timestamp
upload() -> POST the artefact to /admin/model_artifacts

Split is time based. With cutoff C and horizon H:
    train rows: timestamp <= C - H   (their labels are fully known at C)
    test rows:  timestamp >  C       (the replay window)
Rows in (C - H, C] are dropped from both sides (embargo) so no test outcome
leaks into training. Fitting is deterministic (Newton's method with L2), the
seed is recorded for provenance and tie-breaking.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from models.predict.features import ID_COLS, feature_columns

ARTIFACT_DIR = Path("models/artifacts")
REPORTS_DIR = Path("reports")

# tag -> (failure mode, expected sign of drift), from the maintenance-domain skill
SYMPTOMS: dict[str, tuple[str, int]] = {
    "VIB_DE": ("bearing_wear", +1),
    "BRG_TEMP_DE": ("bearing_wear", +1),
    "VIB_NDE": ("bearing_wear", +1),
    "CDP": ("compressor_fouling", -1),
    "EXH_TEMP": ("compressor_fouling", +1),
    "FUEL_FLOW": ("compressor_fouling", +1),
    "LOAD_MW": ("compressor_fouling", -1),
    "GBX_OIL_TEMP": ("gearbox_wear", +1),
    "VIB": ("gearbox_wear", +1),
}


# --------------------------------------------------------------------- fitting


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logreg(X: np.ndarray, y: np.ndarray, l2: float = 1.0, max_iter: int = 50, tol: float = 1e-8) -> tuple[np.ndarray, float]:
    """L2-regularised logistic regression by Newton's method. X standardised."""
    n, p = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    w = np.zeros(p + 1)
    reg = np.full(p + 1, l2)
    reg[-1] = 0.0  # no penalty on the intercept
    for _ in range(max_iter):
        pr = _sigmoid(Xb @ w)
        grad = Xb.T @ (pr - y) + reg * w
        s = pr * (1 - pr)
        hess = (Xb * s[:, None]).T @ Xb + np.diag(reg)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < tol:
            break
    return w[:-1], float(w[-1])


class Scaler:
    def __init__(self, mean: np.ndarray, std: np.ndarray):
        self.mean, self.std = np.asarray(mean, float), np.asarray(std, float)

    @classmethod
    def fit(cls, X: np.ndarray) -> "Scaler":
        mean = np.nanmean(X, axis=0)
        std = np.nanstd(X, axis=0)
        mean = np.where(np.isnan(mean), 0.0, mean)
        std = np.where(np.isnan(std) | (std < 1e-12), 1.0, std)
        return cls(mean, std)

    def transform(self, X: np.ndarray) -> np.ndarray:
        Z = (X - self.mean) / self.std
        return np.where(np.isnan(Z), 0.0, Z)  # missing tag or window -> neutral


# --------------------------------------------------------------------- metrics


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Average precision (step-wise area under the PR curve)."""
    if y.sum() == 0:
        return float("nan")
    order = np.argsort(-p, kind="stable")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    precision = tp / np.arange(1, len(y) + 1)
    recall = tp / y.sum()
    prev_r = np.concatenate([[0.0], recall[:-1]])
    return float(np.sum((recall - prev_r) * precision))


def precision_at_k(y: np.ndarray, p: np.ndarray, k: int = 5) -> float:
    if len(y) == 0:
        return float("nan")
    order = np.argsort(-p, kind="stable")[:k]
    return float(y[order].mean())


def lead_times(frame: pd.DataFrame, threshold: float, step_hours: int = 24) -> list[dict]:
    """Per positive run (one per upcoming failure): first alert and lead time.

    The failure is estimated as one step after the last positive row, so the
    lead time is accurate to within one grid step and needs no ground truth.
    """
    out = []
    step = pd.Timedelta(hours=step_hours)
    for asset, g in frame.sort_values("timestamp").groupby("asset"):
        pos = g[g["label"] == 1]
        if pos.empty:
            continue
        ts = pos["timestamp"].to_numpy()
        breaks = np.flatnonzero(np.diff(ts) > np.timedelta64(int(step.total_seconds() * 1.5), "s")) + 1
        for run in np.split(np.arange(len(pos)), breaks):
            r = pos.iloc[run]
            fail_est = r["timestamp"].iloc[-1] + step
            alerts = r[r["p"] >= threshold]
            first = alerts["timestamp"].iloc[0] if not alerts.empty else None
            out.append(
                {
                    "asset": asset,
                    "failure_est": fail_est.isoformat(),
                    "first_alert": first.isoformat() if first is not None else None,
                    "lead_time_days": float((fail_est - first) / pd.Timedelta(days=1)) if first is not None else None,
                    "max_p": float(r["p"].max()),
                }
            )
    return out


def choose_threshold(y: np.ndarray, p: np.ndarray) -> float:
    """Threshold maximising F1 on the training set; 0.5 if degenerate."""
    if y.sum() == 0 or y.sum() == len(y):
        return 0.5
    best_t, best_f1 = 0.5, -1.0
    for t in np.unique(np.round(p, 3)):
        pred = p >= t
        tp = float((pred & (y == 1)).sum())
        if tp == 0:
            continue
        prec = tp / pred.sum()
        rec = tp / y.sum()
        f1 = 2 * prec * rec / (prec + rec)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def evaluate(frame: pd.DataFrame, threshold: float, step_hours: int = 24) -> dict:
    """frame: asset, timestamp, label (0/1), p."""
    y = frame["label"].to_numpy(float)
    p = frame["p"].to_numpy(float)
    lt = lead_times(frame, threshold, step_hours)
    leads = [d["lead_time_days"] for d in lt if d["lead_time_days"] is not None]
    false_alerts = int(((p >= threshold) & (y == 0)).sum())
    span_days = max((frame["timestamp"].max() - frame["timestamp"].min()) / pd.Timedelta(days=1), 1.0)
    asset_months = frame["asset"].nunique() * span_days / 30.0
    return {
        "n_rows": int(len(frame)),
        "positives": int(y.sum()),
        "positive_rate": float(y.mean()) if len(y) else float("nan"),
        "pr_auc": pr_auc(y, p),
        "precision_at_5": precision_at_k(y, p, 5),
        "threshold": threshold,
        "lead_time_days_mean": float(np.mean(leads)) if leads else None,
        "lead_times": lt,
        "failures_in_window": len(lt),
        "failures_alerted": len(leads),
        "false_alert_rows": false_alerts,
        "false_alerts_per_asset_month": float(false_alerts / asset_months) if asset_months else None,
    }


# ----------------------------------------------------------------------- train


def split(table: pd.DataFrame, cutoff: pd.Timestamp, horizon_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    ts = pd.to_datetime(table["timestamp"])
    known = table["label"].notna()
    train = table[known & (ts <= cutoff - pd.Timedelta(days=horizon_days))]
    test = table[known & (ts > cutoff)]
    return train, test


def default_cutoff(table: pd.DataFrame) -> pd.Timestamp:
    ts = pd.to_datetime(table["timestamp"]).drop_duplicates().sort_values()
    return ts.iloc[int(0.7 * (len(ts) - 1))]


def train(
    table: pd.DataFrame,
    horizon_days: int = 30,
    seed: int = 42,
    cutoff: str | pd.Timestamp | None = None,
    l2: float = 1.0,
    run_id: str | None = None,
    step_hours: int = 24,
) -> dict:
    """Fit on the labelled feature table (output of models.predict.labels)."""
    if "label" not in table.columns:
        raise ValueError("table has no 'label' column; run models.predict.labels first (validator)")
    table = table.copy()
    table["timestamp"] = pd.to_datetime(table["timestamp"])
    cutoff = pd.Timestamp(cutoff) if cutoff is not None else default_cutoff(table)
    rng = np.random.default_rng(seed)

    cols = feature_columns(table)
    tr, te = split(table, cutoff, horizon_days)
    if tr.empty or tr["label"].sum() == 0:
        pos = table.loc[table["label"] == 1, "timestamp"]
        first_pos = pos.min().date() if not pos.empty else None
        raise ValueError(
            f"training split has no positive labels: cutoff {cutoff.date()} minus the {horizon_days}-day embargo "
            f"ends training at {(cutoff - pd.Timedelta(days=horizon_days)).date()}, but the first positive label is "
            f"{first_pos}. Set cutoff to at least {first_pos + pd.Timedelta(days=horizon_days + 7) if first_pos else 'a later date'}."
        )

    scaler = Scaler.fit(tr[cols].to_numpy(float))
    Xtr = scaler.transform(tr[cols].to_numpy(float))
    ytr = tr["label"].to_numpy(float)
    # tiny seeded jitter on duplicate rows keeps the Hessian well conditioned and makes the seed matter
    Xtr = Xtr + rng.normal(0, 1e-9, Xtr.shape)
    coef, intercept = fit_logreg(Xtr, ytr, l2=l2)

    ptr = _sigmoid(Xtr @ coef + intercept)
    threshold = choose_threshold(ytr, ptr)
    tr_eval = evaluate(tr.assign(p=ptr), threshold, step_hours)

    if not te.empty:
        pte = _sigmoid(scaler.transform(te[cols].to_numpy(float)) @ coef + intercept)
        te_eval = evaluate(te.assign(p=pte), threshold, step_hours)
    else:
        te_eval = None

    date = cutoff.strftime("%Y-%m-%d")
    run_id = run_id or f"predict_fleet_h{horizon_days}_{date}_s{seed}"
    return {
        "run_id": run_id,
        "task": "predict",
        "target": "fleet",
        "created_at": datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "seed": seed,
        "horizon_days": horizon_days,
        "cutoff": cutoff.isoformat(),
        "split": {
            "train_rows": int(len(tr)),
            "train_end": tr["timestamp"].max().isoformat(),
            "embargo_days": horizon_days,
            "test_rows": int(len(te)),
            "test_start": te["timestamp"].min().isoformat() if not te.empty else None,
            "test_end": te["timestamp"].max().isoformat() if not te.empty else None,
        },
        "config": {"model": "logistic_regression_l2_newton", "l2": l2, "step_hours": step_hours},
        "feature_names": cols,
        "scaler": {"mean": scaler.mean.tolist(), "std": scaler.std.tolist()},
        "coefficients": coef.tolist(),
        "intercept": intercept,
        "threshold": threshold,
        "metrics": {"train": tr_eval, "test": te_eval},
    }


# ------------------------------------------------------------------- artefacts


def save_artifact(art: dict, artifact_dir: Path = ARTIFACT_DIR) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{art['run_id']}.json"
    path.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
    return path


def load_artifact(id_or_path: str | Path, artifact_dir: Path = ARTIFACT_DIR) -> dict:
    p = Path(id_or_path)
    if not p.exists():
        p = artifact_dir / f"{id_or_path}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def upload(art: dict | str | Path, base_url: str, admin_token: str, _post=None) -> dict:
    """POST the artefact to /admin/model_artifacts (ADMIN token)."""
    if not isinstance(art, dict):
        art = load_artifact(art)
    body = {
        "run_id": art["run_id"],
        "seed": art["seed"],
        "model": {k: art[k] for k in ("feature_names", "scaler", "coefficients", "intercept", "threshold", "config")},
        "metrics": art["metrics"],
        "meta": {k: art[k] for k in ("task", "target", "horizon_days", "cutoff", "split", "created_at")},
    }
    url = base_url.rstrip("/") + "/admin/model_artifacts"
    if _post is not None:
        return _post(url, body, admin_token)
    req = urllib.request.Request(
        url,
        data=json.dumps(body, default=str).encode(),
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
            "User-Agent": "plant-models/0.1 (+https://github.com/RotoPower/learn-cognitive-maintenance)",  # Cloudflare blocks Python-urllib
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


# ----------------------------------------------------------------------- score


def predict_proba(art: dict, features: pd.DataFrame) -> np.ndarray:
    cols = art["feature_names"]
    X = features.reindex(columns=cols).to_numpy(float)
    Z = Scaler(art["scaler"]["mean"], art["scaler"]["std"]).transform(X)
    return _sigmoid(Z @ np.asarray(art["coefficients"]) + art["intercept"])


def _drivers(art: dict, features_row: pd.Series, k: int = 3) -> list[tuple[str, float]]:
    cols = art["feature_names"]
    z = Scaler(art["scaler"]["mean"], art["scaler"]["std"]).transform(features_row.reindex(cols).to_numpy(float)[None, :])[0]
    contrib = z * np.asarray(art["coefficients"])
    order = np.argsort(-contrib)[:k]
    return [(cols[i], float(contrib[i])) for i in order if contrib[i] > 0]


def interpret(drivers: list[tuple[str, float]]) -> str:
    modes: dict[str, list[str]] = {}
    for name, _ in drivers:
        tag = name.split("__")[0]
        if tag in SYMPTOMS:
            modes.setdefault(SYMPTOMS[tag][0], []).append(tag)
    if not modes:
        return "no documented failure-mode symptom among top drivers"
    mode, tags = max(modes.items(), key=lambda kv: len(kv[1]))
    return f"consistent with {mode} ({', '.join(dict.fromkeys(tags))})"


def score(
    art: dict,
    features: pd.DataFrame,
    as_of: str | pd.Timestamp,
    report_dir: Path = REPORTS_DIR,
    write_report: bool = True,
) -> pd.DataFrame:
    """Latest feature row per asset at or before as_of -> ranked probabilities."""
    as_of = pd.Timestamp(as_of)
    f = features.copy()
    f["timestamp"] = pd.to_datetime(f["timestamp"])
    latest = f[f["timestamp"] <= as_of].sort_values("timestamp").groupby("asset").tail(1)
    if latest.empty:
        raise ValueError("no feature rows at or before as_of")
    latest = latest.copy()
    latest["p_fail"] = predict_proba(art, latest)
    latest["alert"] = latest["p_fail"] >= art["threshold"]
    rows = []
    for _, r in latest.iterrows():
        d = _drivers(art, r)
        text = interpret(d) if r["alert"] else f"below threshold; weak signal {interpret(d)}"
        rows.append({"drivers": "; ".join(f"{n} ({c:+.2f})" for n, c in d), "interpretation": text})
    latest = pd.concat([latest.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    ranked = latest.sort_values("p_fail", ascending=False)[["asset", "timestamp", "p_fail", "alert", "drivers", "interpretation"]].reset_index(drop=True)

    if write_report:
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"predict_{as_of.strftime('%Y-%m-%d')}.md"
        te = art["metrics"]["test"] or {}
        lines = [
            f"# Failure risk - fleet - as of {as_of.isoformat()}",
            "",
            f"Artefact `{art['run_id']}` (horizon {art['horizon_days']} d, cutoff {art['cutoff']}, seed {art['seed']}). "
            f"Alert threshold {art['threshold']:.3f}.",
            "",
            "| rank | asset | features at | P(fail within horizon) | alert | top drivers | interpretation |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, r in ranked.iterrows():
            lines.append(
                f"| {i + 1} | {r['asset']} | {r['timestamp'].strftime('%Y-%m-%dT%H:%M')} | {r['p_fail']:.3f} | "
                f"{'YES' if r['alert'] else 'no'} | {r['drivers'] or '-'} | {r['interpretation']} |"
            )
        lines += [
            "",
            "## Backtest (replay window at training time)",
            "",
            f"- PR-AUC {te.get('pr_auc', float('nan')):.3f}, precision@5 {te.get('precision_at_5', float('nan')):.2f}, "
            f"mean lead time {te.get('lead_time_days_mean')} d over {te.get('failures_alerted', 0)}/{te.get('failures_in_window', 0)} failures, "
            f"false alerts {te.get('false_alerts_per_asset_month')} per asset-month.",
            f"- Features: {len(art['feature_names'])} columns (current, 7d mean, 7d slope, 30d slope per tag; hours since repair; load).",
            "- Labels were built by models/predict/labels.py (validator). No ground truth was read for this scoring.",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
    return ranked
