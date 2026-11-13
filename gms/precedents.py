"""Precedents: how often a solvent, reagent, catalyst or base appears in reactions of the same
class in the packaged dataset (gms/data/precedents.json, built by scripts/build_precedents.py
from the Open Reaction Database, which includes the USPTO patent reactions).

A precedent count says that the material has been used in this reaction class. It does not say
that it will work for the user's substrates. Yields of precedents are reported yields of other
substrates and are shown as a range, never as a prediction.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Optional

from gms import config
from gms.chem.structure import canon

FILE = config.DATA_DIR / "precedents.json"


@lru_cache(maxsize=1)
def dataset() -> dict:
    if not FILE.exists():
        return {}
    return json.loads(FILE.read_text(encoding="utf-8"))


def available(reaction_class: Optional[str]) -> bool:
    return bool(reaction_class) and reaction_class in dataset().get("classes", {})


def meta() -> dict:
    return dataset().get("meta", {})


def support(reaction_class: Optional[str], smiles: Optional[str], group: str) -> dict:
    """Use of a material in the class. group: 'solvent' (reaction solvent) or 'agent'
    (reagent, catalyst, base, additive). For a mixture (dot-separated SMILES) every part must
    be present in the same reaction.

    Returns {'available': False, 'reason': ...} when the dataset cannot answer, else
    {'available': True, 'n': uses, 'n_class': reactions in the class, 'yield_q1'/'yield_median'/
     'yield_q3' (reported yields, may be None), 'examples': [ids]}."""
    if not available(reaction_class):
        return {"available": False, "reason": "no precedent data for this reaction class"}
    cls = dataset()["classes"][reaction_class]
    key = ".".join(sorted(filter(None, (canon(p) for p in (smiles or "").split("."))))) if smiles else None
    if not key:
        return {"available": False, "reason": "no structure for the material"}
    rec = cls.get(group, {}).get(key)
    base = {"available": True, "n_class": cls["n"], "dataset": meta().get("name", "")}
    if rec is None:
        return {**base, "n": 0, "yield_q1": None, "yield_median": None, "yield_q3": None, "examples": [],
                **({"n_sole": 0} if group == "solvent" else {})}
    return {**base, **rec}


def typical(reaction_class: Optional[str], quantity: str) -> Optional[dict]:
    """Quartiles of a quantity in the class, e.g. 'ml_per_mmol', 'yield', 'equiv:reagent'.
    {'n', 'q1', 'median', 'q3'} or None."""
    if not available(reaction_class):
        return None
    return dataset()["classes"][reaction_class].get("typical", {}).get(quantity)


def percentile(reaction_class: Optional[str], quantity: str, value: Optional[float]) -> Optional[dict]:
    """Where a value lies among the class precedents: {'percentile', 'n'} from the stored deciles."""
    t = typical(reaction_class, quantity)
    if not t or value is None or not t.get("deciles"):
        return None
    d = t["deciles"]                                  # 0%, 10%, ..., 100%
    below = sum(1 for x in d if x <= value)
    return {"percentile": max(0, min(100, (below - 1) * 10 + 5 if 0 < below < len(d) else (0 if below == 0 else 100))),
            "n": t["n"]}
