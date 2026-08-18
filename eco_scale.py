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
from typing import List

# ---------------------------------------------------------------------------
# Penalty tables (Table 10.3)
# ---------------------------------------------------------------------------

PRICE_PENALTIES = {
    "Inexpensive (< $10 per mmol product)": 0,
    "Expensive ($10 - $50 per mmol product)": 3,
    "Very expensive (> $50 per mmol product)": 5,
}

# GHS / hazard-warning-symbol based safety penalties
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


@dataclass
class EcoScaleInputs:
    route_name: str
    yield_pct: float
    price_category: str
    safety_hazards: List[str] = field(default_factory=list)
    technical_setups: List[str] = field(default_factory=list)
    temp_time_category: str = "Room temperature, < 1 h"
    workup_category: str = "None"


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
    p_setup = sum(TECHNICAL_SETUP_PENALTIES.get(s, 0) for s in inputs.technical_setups)
    p_temp = TEMPERATURE_TIME_PENALTIES.get(inputs.temp_time_category, 0)
    p_workup = WORKUP_PENALTIES.get(inputs.workup_category, 0)

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
    Qualitative color-scale bin, following the 0-19...90-100 bands in
    Figure 10.8 of the source chapter (collapsed to a 3-tier rating here).
    """
    if score >= 75:
        return "Excellent / Ideally Green"
    elif score >= 50:
        return "Acceptable"
    else:
        return "Poor / Needs Redesign"
