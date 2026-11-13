"""Metric registry: keys, labels, units, direction, thresholds and tolerances (config/metrics.yaml).

Results are keyed by these stable keys ("pmi", "ae", ...). Display labels are looked up here."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from gms import config


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    short: str
    unit: str
    direction: str            # "higher" | "lower" is better
    good: float
    moderate: float
    tolerance_rel: float
    tolerance_abs: float
    yield_dependent: bool
    legacy_label: Optional[str]
    reference: str
    help: str

    @property
    def higher_is_better(self) -> bool:
        return self.direction == "higher"


def metrics() -> dict[str, Metric]:
    raw = config.load("metrics")["metrics"]
    return {k: Metric(key=k, **v) for k, v in raw.items()}


def display_order() -> list[str]:
    return list(config.load("metrics")["display_order"])


def get(key: str) -> Metric:
    return metrics()[key]


def _usable(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def rating(key: str, value) -> str:
    """'good' | 'moderate' | 'poor' | 'n/a' from the thresholds in config/metrics.yaml."""
    if not _usable(value):
        return "n/a"
    m = get(key)
    if m.higher_is_better:
        return "good" if value >= m.good else "moderate" if value >= m.moderate else "poor"
    return "good" if value <= m.good else "moderate" if value <= m.moderate else "poor"


def change(key: str, original, candidate) -> Optional[float]:
    """Signed improvement of `candidate` over `original` in the metric's own unit:
    positive = better, negative = worse, None if either value is missing."""
    if not _usable(original) or not _usable(candidate):
        return None
    if math.isinf(original) and math.isinf(candidate):
        return 0.0
    d = candidate - original
    return d if get(key).higher_is_better else -d


def tolerance(key: str, original) -> float:
    m = get(key)
    rel = m.tolerance_rel * abs(original) if _usable(original) and math.isfinite(original) else 0.0
    return max(rel, m.tolerance_abs)


def is_worse(key: str, original, candidate) -> Optional[bool]:
    """True if `candidate` is worse than `original` beyond the metric's tolerance.
    None if the comparison is not possible (a value is missing)."""
    c = change(key, original, candidate)
    if c is None:
        return None
    return c < -tolerance(key, original)
