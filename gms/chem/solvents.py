"""CHEM21 solvent selection guide (Prat et al., Green Chem. 2016, 18, 288; CC BY 3.0),
packaged as gms/data/chem21_solvents.csv."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from gms import config
from gms.chem.structure import canon

FILE = config.DATA_DIR / "chem21_solvents.csv"


@dataclass(frozen=True)
class Solvent:
    name: str
    synonyms: tuple
    smiles: str
    family: str
    bp_c: Optional[float]
    flash_point_c: Optional[float]
    safety_score: Optional[int]
    health_score: Optional[int]
    env_score: Optional[int]
    ranking: str                 # CHEM21 ranking after discussion
    density_g_ml: Optional[float]
    water_miscibility: str
    verified: str

    @property
    def class_number(self) -> Optional[int]:
        return config.load("hazard")["solvent_class"].get(self.ranking)


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def table() -> dict[str, Solvent]:
    """Canonical SMILES -> Solvent."""
    out = {}
    if not FILE.exists():
        return out
    with open(FILE, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            smi = canon(r.get("smiles"))
            if not smi:
                continue
            i = lambda k: int(v) if (v := _f(r.get(k))) is not None else None   # noqa: E731
            out[smi] = Solvent(
                name=r["name"].strip(), synonyms=tuple(s.strip() for s in r.get("synonyms", "").split(";") if s.strip()),
                smiles=smi, family=r.get("family", "").strip(), bp_c=_f(r.get("bp_c")),
                flash_point_c=_f(r.get("flash_point_c")), safety_score=i("safety_score"),
                health_score=i("health_score"), env_score=i("env_score"),
                ranking=(r.get("ranking_final") or r.get("ranking_default") or "").strip().lower(),
                density_g_ml=_f(r.get("density_g_ml")), water_miscibility=r.get("water_miscibility", "").strip(),
                verified=r.get("verified", "").strip().lower())
    return out


def lookup(smiles: Optional[str]) -> Optional[Solvent]:
    s = canon(smiles)
    return table().get(s) if s else None
