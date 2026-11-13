"""Example reactions packaged with the app (gms/data/examples/*.yaml)."""

from __future__ import annotations

from functools import lru_cache

import yaml

from gms import config
from gms.model import Reaction


@lru_cache(maxsize=1)
def _all() -> dict:
    out = {}
    for p in sorted((config.DATA_DIR / "examples").glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        out[d["id"]] = d
    return out


def titles() -> dict[str, str]:
    return {k: v["title"] for k, v in _all().items()}


def description(example_id: str) -> str:
    return " ".join(_all()[example_id].get("description", "").split())


def load(example_id: str) -> Reaction:
    return Reaction.from_dict(_all()[example_id]["reaction"])
