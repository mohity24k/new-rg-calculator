"""Combined greenness score and verdict (green / borderline / not green), with its reasons.

The score is a weighted mean of desirabilities (0-1), each computed from one deterministic
metric with the limits in config/scoring.yaml. No model is involved. The limits and weights
are provisional defaults; `sensitivity` shows how much the verdict depends on the weights.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from gms import config
from gms.metrics import registry

VERDICT_LABEL = {"green": "Green", "borderline": "Borderline", "not_green": "Not green",
                 "unknown": "Not enough data"}

_NAMES = {"hazard": "Worst hazard score", "solvent": "Solvent class (CHEM21, by mass)"}


def desirability(value, worst: float, best: float, scale: str = "linear") -> Optional[float]:
    """0 at `worst` or beyond, 1 at `best` or beyond, linear (or log-linear) in between."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if math.isinf(value):
        return 0.0 if (value > 0) == (worst > best) else 1.0
    if scale == "log":
        if value <= 0:
            return 1.0 if best < worst else 0.0
        value, worst, best = math.log(value), math.log(worst), math.log(best)
    d = (value - worst) / (best - worst)
    return max(0.0, min(1.0, d))


def _inputs(score) -> dict:
    """The values the verdict is built from."""
    v = {k: score.metrics.get(k) for k in ("ae", "rme", "pmi", "ecoscale")}
    v["hazard"] = score.hazard_summary["worst_overall"]
    v["solvent"] = score.hazard_summary["solvent_class"]
    return v


def _label(key: str) -> str:
    return _NAMES.get(key) or registry.get(key).label


def _fmt(key: str, value) -> str:
    if key in _NAMES:
        return f"{value:.2g}"
    m = registry.get(key)
    text = f"{value:,.0f}" if abs(value) >= 1000 and not math.isinf(value) else f"{value:.3g}"
    return f"{text} {m.unit}".strip()


def _verdict(score_value: Optional[float], capped: bool) -> str:
    if score_value is None:
        return "unknown"
    v = config.load("scoring")["verdict"]
    if score_value >= v["green_min"]:
        return "borderline" if capped else "green"
    return "borderline" if score_value >= v["borderline_min"] else "not_green"


def _weighted(parts: dict, weights: dict) -> Optional[float]:
    total = sum(weights[k] for k in parts)
    return sum(parts[k] * weights[k] for k in parts) / total if total > 0 else None


def assess(score) -> dict:
    cfg = config.load("scoring")
    values = _inputs(score)
    parts, missing = {}, []
    for key, d in cfg["desirability"].items():
        x = desirability(values.get(key), d["worst"], d["best"], d.get("scale", "linear"))
        if x is None:
            missing.append(_label(key))
        else:
            parts[key] = x
    weights = {k: float(cfg["desirability"][k]["weight"]) for k in parts}
    # the mass metrics and the EcoScale are the minimum needed for a verdict
    enough = "pmi" in parts and "ecoscale" in parts
    value = _weighted(parts, weights) if enough else None

    caps = []
    c = cfg["caps"]
    hs = score.hazard_summary
    if hs["worst_overall"] is not None and hs["worst_overall"] >= c["hazard_score_at_least"]:
        caps.append(f"{hs['worst_material']} has a hazard score of {hs['worst_overall']} "
                    "(for example carcinogenic, mutagenic, toxic to reproduction, or acutely very toxic).")
    if hs["worst_solvent_class"] is not None and hs["worst_solvent_class"] >= c["solvent_class_at_least"]:
        caps.append(f"{hs['worst_solvent']} is ranked 'highly hazardous' in the CHEM21 solvent guide.")
    verdict = _verdict(value, bool(caps))

    reasons, strengths = [], []
    for key in sorted(parts, key=lambda k: -(1 - parts[k]) * weights[k]):
        d, lim = parts[key], cfg["desirability"][key]
        text = f"{_label(key)}: {_fmt(key, values[key])} (target {lim['best']:g}, poor at {lim['worst']:g})"
        if d < 0.5:
            reasons.append(text)
        elif d >= 0.8:
            strengths.append(text)
    notes = []
    if missing:
        notes.append("Not included because the value is missing: " + ", ".join(missing) + ".")
    if hs["unknown"]:
        notes.append("Hazard unknown for: " + ", ".join(hs["unknown"]) + ". The hazard part of the verdict "
                     "covers only the materials with data.")
    if hs["solvents_not_in_guide"]:
        notes.append("Not in the CHEM21 solvent guide (not in the solvent class): "
                     + ", ".join(dict.fromkeys(hs["solvents_not_in_guide"])) + ".")
    if score.estimated_metrics:
        notes.append("The verdict uses estimated amounts (see the list of estimates).")
    return {"score": value, "verdict": verdict, "label": VERDICT_LABEL[verdict],
            "desirabilities": parts, "weights": weights, "values": values,
            "reasons": reasons, "strengths": strengths, "caps": caps, "notes": notes,
            "provisional": True}


def sensitivity(greenness: dict) -> Optional[dict]:
    """How often the verdict stays the same when the weights are varied at random
    (fixed seed: the result is reproducible)."""
    parts = greenness["desirabilities"]
    if greenness["score"] is None:
        return None
    cfg = config.load("scoring")["sensitivity"]
    rng = random.Random(cfg["seed"])
    capped = bool(greenness["caps"])
    same, values = 0, []
    for _ in range(cfg["samples"]):
        w = {k: greenness["weights"][k] * rng.uniform(1 - cfg["spread"], 1 + cfg["spread"]) for k in parts}
        v = _weighted(parts, w)
        values.append(v)
        same += _verdict(v, capped) == greenness["verdict"]
    values.sort()
    n = len(values)
    return {"same_verdict_share": same / n, "score_low": values[int(0.05 * n)],
            "score_high": values[min(n - 1, int(0.95 * n))], "samples": n, "spread": cfg["spread"]}
