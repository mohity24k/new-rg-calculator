"""Load config/extraction.yaml, falling back to the built-in defaults for missing keys."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "extraction.yaml"

DEFAULTS = {
    "page_filter": {"min_text_chars": 200, "min_procedure_score": 3,
                    "spectrum_max_words": 120, "reference_min_hits": 8},
    "chunking": {"max_chars": 6000},
    "images": {"max_side_px": 1280},
    "server": {"concurrency": 32, "max_tokens": 3072, "timeout_s": 300, "retries": 2},
    "validation": {"yield_tolerance_pct": 5, "mass_balance_tolerance": 0.02,
                   "moles_mass_tolerance": 0.05, "max_equiv": 20, "catalyst_max_equiv": 0.5,
                   "temperature_c": [-100, 300], "time_h": [0, 336],
                   "solvent_ml_per_mmol": [0.05, 100]},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Optional[str] = None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        import yaml
    except ImportError:  # PyYAML missing: defaults still work
        return copy.deepcopy(DEFAULTS)
    with open(p, encoding="utf-8") as f:
        return _merge(DEFAULTS, yaml.safe_load(f) or {})
