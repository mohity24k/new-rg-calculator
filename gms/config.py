"""Configuration files (config/*.yaml) and packaged reference data (gms/data).

Thresholds, weights and tolerances live in these files, not in code."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(__file__).resolve().parent / "data"


def config_dir() -> Path:
    return Path(os.environ.get("GMS_CONFIG_DIR") or ROOT / "config")


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load(name: str) -> dict:
    """config/<name>.yaml as a dict (cached; treat the result as read-only)."""
    return _load(str(config_dir() / f"{name}.yaml"))


def data(name: str) -> dict:
    """gms/data/<name> (YAML or JSON) as a dict (cached; read-only)."""
    return _load(str(DATA_DIR / name))


def clear_cache() -> None:
    _load.cache_clear()
