"""Reaction conditions as free text -> EcoScale categories (Van Aken et al. 2006, Table 10.3)."""

from __future__ import annotations

import re
from typing import Optional

from gms.metrics import ecoscale as es

INERT_RE = re.compile(r"nitrogen|argon|inert|\bn2\b|\bar\b|glove", re.I)
ACTIVATION_RE = re.compile(r"\bleds?\b|light|photo|irradiat|\buv\b|hν|\bhv\b|microwave|\bmw\b|"
                           r"ultrasound|sonicat|electro", re.I)
GLOVE_BOX_RE = re.compile(r"glove\s?box", re.I)

WORKUP_RULES = [  # (regex, EcoScale category), ordered highest penalty first
    (r"chromatograph|\bcolumn\b", "Classical chromatography"),
    (r"distill", "Distillation"),
    (r"sublim", "Sublimation"),
    (r"(?<!solid-phase )(?<!solid phase )extract|separatory|\bwash",
     "Liquid-liquid extraction (incl. drying/filtration of desiccant)"),
    (r"solid[- ]phase extract|\bspe\b", "Solid-phase extraction"),
    (r"crystal", "Crystallization and filtration"),
    (r"filtrat|filter", "Simple filtration"),
]


def temp_time_category(temperature_c: Optional[float], time_h: Optional[float], heated: bool = False) -> str:
    """EcoScale temperature/time category. Unknown temperature counts as room temperature and
    unknown time as 'not short' (the more cautious reading)."""
    short = time_h is not None and time_h < 1
    t = temperature_c
    if t is not None and t < 0:
        return "Cooling, < 0 degC"
    if t is not None and t <= 5:
        return "Cooling to 0 degC"
    if (t is not None and t > 35) or heated:
        return "Heating, < 1 h" if short else "Heating, > 1 h"
    return "Room temperature, < 1 h" if short else "Room temperature, < 24 h"


def setups_from_conditions(atmosphere: Optional[str], activation: Optional[str]) -> list[str]:
    setups = []
    if INERT_RE.search(str(atmosphere or "")):
        setups.append("Glove box" if GLOVE_BOX_RE.search(str(atmosphere)) else "Inert gas atmosphere")
    if ACTIVATION_RE.search(str(activation or "")):
        setups.append("Unconventional activation (microwave / ultrasound / photochemical)")
    return setups


def workup_steps_from_text(items) -> list[str]:
    """Every workup / purification step mentioned in a list of free-text steps (penalties add up)."""
    steps = []
    for item in items or []:
        for part in re.split(r"[;,/]| and | then ", str(item).lower()):
            hit = next((v for k, v in WORKUP_RULES if re.search(k, part)), None)
            if hit and hit in es.WORKUP_PENALTIES:
                steps.append(hit)
    return steps
