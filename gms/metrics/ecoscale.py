"""
eco_scale.py
------------
Reaction Eco-Scale scoring system.

Reference:
Van Aken, K., Strekowski, L., Patiny, L. (2006). "EcoScale, a semi-quantitative
tool to select an organic preparation based on economical and ecological
parameters." Beilstein J. Org. Chem., 2, 1-7. doi:10.1186/1860-5397-2-3
(Open Access), as summarized in Tamargo et al. (2024), Table 10.3.

Scoring logic: start at 100 points ("Compound A + Compound B = Compound C,
100% yield, minimal operator risk, minimal environmental impact") and
subtract penalty points for every deviation from that ideal.
"""

from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Penalty tables (Table 10.3)
# ---------------------------------------------------------------------------

# Van Aken et al. (2006): price of the reaction components needed to obtain 10 mmol of product
PRICE_PENALTIES = {
    "Inexpensive (< $10 for 10 mmol product)": 0,
    "Expensive ($10 - $50 for 10 mmol product)": 3,
    "Very expensive (> $50 for 10 mmol product)": 5,
}

# Hazard-warning-symbol based safety penalties. Counted PER COMPOUND (Table 10.4: nitrobenzene
# (T, N) = 10 and MeOH (T, F) = 10 are both charged). The legacy EU symbols map to GHS roughly as:
# N = GHS09; T = GHS06 (acute tox. cat. 3) or GHS08 (CMR); T+ = GHS06 (cat. 1-2);
# F = GHS02 (flammable cat. 2-3); F+ = GHS02 (cat. 1, e.g. Et2O, pentane); E = GHS01.
SAFETY_PENALTIES = {
    "N - Dangerous to environment": 5,
    "T - Toxic": 5,
    "F - Highly flammable": 5,
    "E - Explosive": 10,
    "F+ - Extremely flammable": 10,
    "T+ - Extremely toxic": 10,
}

TECHNICAL_SETUP_PENALTIES = {
    "Common setup": 0,
    "Instruments for controlled addition of chemicals": 1,
    "Unconventional activation (microwave / ultrasound / photochemical)": 2,
    "Pressure equipment (> 1 atm)": 3,
    "Additional special glassware": 1,
    "Inert gas atmosphere": 1,
    "Glove box": 3,
}

TEMPERATURE_TIME_PENALTIES = {
    "Room temperature, < 1 h": 0,
    "Room temperature, < 24 h": 1,
    "Heating, < 1 h": 2,
    "Heating, > 1 h": 3,
    "Cooling to 0 degC": 4,
    "Cooling, < 0 degC": 5,
}

WORKUP_PENALTIES = {
    "None": 0,
    "Cooling to room temperature": 0,
    "Adding solvent": 0,
    "Simple filtration": 0,
    "Removal of solvent (bp < 150 degC)": 0,
    "Crystallization and filtration": 1,
    "Removal of solvent (bp > 150 degC)": 2,
    "Solid-phase extraction": 2,
    "Distillation": 3,
    "Sublimation": 3,
    "Liquid-liquid extraction (incl. drying/filtration of desiccant)": 3,
    "Classical chromatography": 10,
}


SAFETY_SYMBOLS = {"N": "N - Dangerous to environment", "T": "T - Toxic", "F": "F - Highly flammable",
                  "E": "E - Explosive", "F+": "F+ - Extremely flammable", "T+": "T+ - Extremely toxic"}


@dataclass
class EcoScaleInputs:
    route_name: str
    yield_pct: float
    price_category: str
    # One entry per hazard symbol PER COMPOUND (duplicates are intended: two toxic compounds = 2 x "T").
    safety_hazards: List[str] = field(default_factory=list)
    technical_setups: List[str] = field(default_factory=list)
    temp_time_category: str = "Room temperature, < 1 h"
    workup_category: str = "None"
    # Every workup/purification step performed (penalties add up, Table 10.4). Overrides workup_category.
    workup_steps: Optional[List[str]] = None


def compound_hazards(symbols: List[str]) -> List[str]:
    """Hazard labels for ONE compound, without double counting: F+ replaces F, T+ replaces T."""
    s = {x.strip() for x in symbols if x}
    s = {SAFETY_SYMBOLS.get(x, x) for x in s}
    if SAFETY_SYMBOLS["F+"] in s:
        s.discard(SAFETY_SYMBOLS["F"])
    if SAFETY_SYMBOLS["T+"] in s:
        s.discard(SAFETY_SYMBOLS["T"])
    return sorted(x for x in s if x in SAFETY_PENALTIES)


def yield_penalty(yield_pct: float) -> float:
    """Penalty = (100 - % yield) / 2  (Eq. per Table 10.3, category 1)."""
    yield_pct = max(0.0, min(100.0, yield_pct))
    return (100.0 - yield_pct) / 2.0


def compute_eco_scale(inputs: EcoScaleInputs) -> dict:
    """
    Compute the full Eco-Scale breakdown for a single reaction/route.
    Eco-Scale = 100 - sum(individual penalties)   [Eq. 10.34]
    """
    p_yield = yield_penalty(inputs.yield_pct)
    p_price = PRICE_PENALTIES.get(inputs.price_category, 0)
    p_safety = sum(SAFETY_PENALTIES.get(h, 0) for h in inputs.safety_hazards)
    p_setup = sum(TECHNICAL_SETUP_PENALTIES.get(s, 0) for s in dict.fromkeys(inputs.technical_setups))
    p_temp = TEMPERATURE_TIME_PENALTIES.get(inputs.temp_time_category, 0)
    steps = inputs.workup_steps if inputs.workup_steps is not None else [inputs.workup_category]
    p_workup = sum(WORKUP_PENALTIES.get(w, 0) for w in steps)

    total_penalty = p_yield + p_price + p_safety + p_setup + p_temp + p_workup
    score = max(0.0, 100.0 - total_penalty)

    return {
        "Route": inputs.route_name,
        "Yield Penalty": round(p_yield, 2),
        "Price Penalty": p_price,
        "Safety Penalty": p_safety,
        "Technical Setup Penalty": p_setup,
        "Temperature/Time Penalty": p_temp,
        "Workup Penalty": p_workup,
        "Total Penalty": round(total_penalty, 2),
        "Eco-Scale Score": round(score, 2),
    }


def eco_scale_rating(score: float) -> str:
    """
    Van Aken et al. (2006): > 75 excellent, > 50 acceptable, < 50 inadequate.
    (Figure 10.8 of the chapter shows a finer 0-19 ... 90-100 colour scale.)
    """
    if score >= 75:
        return "Excellent / Ideally Green"
    elif score >= 50:
        return "Acceptable"
    else:
        return "Poor / Needs Redesign"
