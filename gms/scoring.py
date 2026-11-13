"""score(reaction): the one place where a reaction record is turned into numbers.

Mass metrics, the EcoScale and the hazard summary are computed by the deterministic
calculators in gms.metrics from the literature formulas. No model is involved.
Results are keyed by the metric keys of config/metrics.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from gms import config
from gms.chem import amounts, conditions, hazard, solvents
from gms.metrics import ecoscale as es
from gms.metrics import mass as mm
from gms.metrics import registry
from gms.model import STOICHIOMETRIC_ROLES, Component, Reaction

SOLVENT_ROLES = ("reaction_solvent", "workup", "purification")
# metrics that change when the mass of an auxiliary (non-stoichiometric) material changes
AUX_METRICS = ("pmi", "mi", "e_factor", "mp", "emy", "hazmat")


@dataclass
class Score:
    metrics: dict                      # metric key -> value (None if it cannot be computed)
    totals: dict                       # masses in g, theoretical yield
    ecoscale: Optional[dict]           # penalties and details; None if the yield is unknown
    hazards: list                      # one row per input material
    hazard_summary: dict               # worst scores, high-hazard mass, unknowns, solvent class
    contributions: list                # mass share of every input material
    warnings: list = field(default_factory=list)
    estimates: list = field(default_factory=list)       # assumptions the numbers depend on
    estimated_metrics: list = field(default_factory=list)   # keys of metrics affected by them
    greenness: Optional[dict] = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _reactants(reaction: Reaction) -> list[mm.Reactant]:
    return [mm.Reactant(name=c.name, mw=c.mw or 0.0, moles=c.moles or 0.0, mass=c.mass,
                        carbons=c.carbons or 0, stoich_ratio=c.coefficient, is_limiting=c.limiting,
                        benign=c.benign)
            for c in reaction.by_role(*STOICHIOMETRIC_ROLES)]


def _auxiliaries(reaction: Reaction) -> list[mm.AuxiliaryMaterial]:
    return [mm.AuxiliaryMaterial(name=c.name, mass=c.mass, benign=c.benign, role=c.role)
            for c in reaction.inputs if c.role not in STOICHIOMETRIC_ROLES and c.role != "water"]


def theoretical_product_mass(reaction: Reaction) -> Optional[float]:
    """Product mass at 100% yield (Eq. 10.2), or None if it cannot be computed."""
    p = reaction.product
    lim = mm.limiting_reactant(_reactants(reaction))
    if p is None or not p.mw or lim is None or lim.moles <= 0:
        return None
    return mm.theoretical_yield_mass(lim.moles / (lim.stoich_ratio or 1.0), p.coefficient, p.mw)


def with_yield(reaction: Reaction, yield_fraction: float) -> Reaction:
    """Copy of the reaction whose product mass corresponds to the given yield (0-1)."""
    theo = theoretical_product_mass(reaction)
    if theo is None:
        raise ValueError("the theoretical yield cannot be computed (product MW or limiting reactant missing)")
    r = reaction.copy()
    p = r.product
    p.mass_g = max(0.0, yield_fraction) * theo
    p.moles = p.mass_g / p.mw
    return r


def mass_metrics(reaction: Reaction) -> tuple[dict, dict]:
    """(metrics by key, totals). Calls the unchanged literature formulas in gms.metrics.mass."""
    p = reaction.product
    reactants = _reactants(reaction)
    carbons_known = p is not None and p.carbons is not None and reactants and \
        all(c.carbons is not None for c in reaction.by_role(*STOICHIOMETRIC_ROLES))
    legacy = mm.compute_all_mass_metrics(
        reactants=reactants,
        product_mw=(p.mw or 0.0) if p else 0.0,
        product_actual_mass=p.mass if p else 0.0,
        product_moles=p.moles if p and p.moles else None,
        auxiliaries=_auxiliaries(reaction),
        water_mass=sum(c.mass for c in reaction.by_role("water")),
        product_coefficient=p.coefficient if p else 1.0,
        product_carbons=p.carbons if carbons_known else None)
    out = {}
    for key, m in registry.metrics().items():
        if m.legacy_label and key != "ecoscale":
            out[key] = legacy.get(m.legacy_label)
    totals = {
        "product_mass_g": p.mass if p else 0.0,
        "product_moles": legacy["Product Moles (mol)"],
        "reactant_mass_g": legacy["Total Reactant Mass (g)"],
        "auxiliary_mass_g": legacy["Total Auxiliary Mass (g)"],
        "water_mass_g": legacy["Water Mass (g)"],
        "total_input_mass_g": legacy["Total Mass Input incl. water (g)"],
        "theoretical_yield_g": legacy.get("Theoretical Yield (g)"),
    }
    return out, totals


def hazard_rows(reaction: Reaction, store: Optional[hazard.HazardStore] = None) -> list[dict]:
    store = store or hazard.store()
    rows = []
    for c in reaction.inputs:
        if c.role == "water":
            prof = hazard.HazardProfile(name=c.name, status="not_classified",
                                        source="water or aqueous solution: counted as non-hazardous")
        else:
            prof = store.lookup(c.name, c.smiles)
        sc = hazard.scores(prof)
        solvent_like = c.role == "reaction_solvent" or (c.role in SOLVENT_ROLES and c.volume_ml is not None)
        solv = solvents.lookup(c.smiles) if c.role in SOLVENT_ROLES else None
        rows.append({
            "name": c.name, "role": c.role, "mass_g": c.mass, "status": prof.status,
            "h_codes": list(prof.h_codes), "source": prof.source, "cid": prof.cid,
            "health": sc["health"] if sc else None, "safety": sc["safety"] if sc else None,
            "environment": sc["environment"] if sc else None,
            "worst": max(sc.values()) if sc else None,
            "high_hazard": hazard.is_high_hazard(prof),
            "ecoscale_symbols": es.compound_hazards(hazard.ecoscale_symbols(prof)),
            "chem21_ranking": solv.ranking if solv else None,
            "chem21_class": solv.class_number if solv else None,
            "solvent_like": solvent_like,
        })
    return rows


def hazard_summary(rows: list[dict], product_mass: float, total_input: float) -> dict:
    cfg = config.load("scoring")["caps"]
    counted = [r for r in rows if r["worst"] is not None]
    significant = [r for r in counted if total_input <= 0 or r["mass_g"] / total_input >= cfg["min_mass_fraction"]
                   or r["mass_g"] == 0]
    high_mass = sum(r["mass_g"] for r in rows if r["high_hazard"])
    solv = [r for r in rows if r["chem21_class"] is not None and r["mass_g"] > 0]
    solv_mass = sum(r["mass_g"] for r in solv)
    return {
        "worst": {d: max((r[d] for r in counted), default=None) for d in hazard.DIMENSIONS},
        "worst_overall": max((r["worst"] for r in significant), default=None),
        "worst_material": max(significant, key=lambda r: r["worst"])["name"] if significant else None,
        "high_hazard_mass_g": high_mass,
        "high_hazard_materials": [r["name"] for r in rows if r["high_hazard"]],
        "unknown": [r["name"] for r in rows if r["worst"] is None],
        "solvent_class": (sum(r["chem21_class"] * r["mass_g"] for r in solv) / solv_mass) if solv_mass else None,
        "worst_solvent_class": max((r["chem21_class"] for r in solv), default=None),
        "worst_solvent": max(solv, key=lambda r: r["chem21_class"])["name"] if solv else None,
        "solvents_not_in_guide": [r["name"] for r in rows if r["solvent_like"] and r["chem21_class"] is None],
    }


def ecoscale(reaction: Reaction, yield_percent: Optional[float], rows: list[dict]) -> Optional[dict]:
    """EcoScale breakdown (Van Aken et al. 2006). None if no yield is available."""
    if yield_percent is None or (isinstance(yield_percent, float) and math.isnan(yield_percent)):
        return None
    setups = list(dict.fromkeys(
        conditions.setups_from_conditions(reaction.atmosphere, reaction.activation) + list(reaction.extra_setups)))
    temp_time = conditions.temp_time_category(reaction.temperature_c, reaction.time_h, reaction.reflux)
    safety = [s for r in rows for s in r["ecoscale_symbols"]]
    price = reaction.price_category if reaction.price_category in es.PRICE_PENALTIES else next(iter(es.PRICE_PENALTIES))
    res = es.compute_eco_scale(es.EcoScaleInputs(
        route_name=reaction.name, yield_pct=yield_percent, price_category=price, safety_hazards=safety,
        technical_setups=setups or ["Common setup"], temp_time_category=temp_time,
        workup_steps=list(reaction.workup_steps)))
    return {
        "score": res["Eco-Scale Score"], "total_penalty": res["Total Penalty"],
        "penalties": {"yield": res["Yield Penalty"], "price": res["Price Penalty"],
                      "safety": res["Safety Penalty"], "setup": res["Technical Setup Penalty"],
                      "temp_time": res["Temperature/Time Penalty"], "workup": res["Workup Penalty"]},
        "yield_percent_used": min(100.0, max(0.0, yield_percent)),
        "price_assessed": reaction.price_category in es.PRICE_PENALTIES,
        "setups": setups or ["Common setup"], "temp_time": temp_time,
        "workup_steps": list(reaction.workup_steps),
        "safety_by_material": {r["name"]: r["ecoscale_symbols"] for r in rows if r["ecoscale_symbols"]},
        "rating": es.eco_scale_rating(res["Eco-Scale Score"]),
    }


def _warnings(reaction: Reaction, metrics: dict) -> list[str]:
    p = reaction.product
    w = mm.validate_inputs(_reactants(reaction), (p.mw or 0.0) if p else 0.0, p.mass if p else 0.0)
    if p is None:
        w.insert(0, "No product in the reaction: the yield-based metrics cannot be computed.")
    y = metrics.get("yield")
    if y is not None and y > 100.5:
        w.append(f"The computed yield is {y:.0f}%: more product than the limiting reactant allows. "
                 "Check the amounts, the limiting reactant and the coefficients.")
    if y is not None and reaction.reported_yield_percent is not None and abs(y - reaction.reported_yield_percent) > 5:
        w.append(f"The reported yield ({reaction.reported_yield_percent:g}%) differs from the yield computed "
                 f"from the amounts ({y:.1f}%).")
    for c in reaction.inputs:
        if c.mass_g is None:
            w.append(f"'{c.name}' has no mass and is not counted: PMI and E-factor are lower bounds.")
    return w


def score(reaction: Reaction, store: Optional[hazard.HazardStore] = None) -> Score:
    """All metrics, the EcoScale, the hazard summary and the greenness verdict of a reaction.
    The reaction should have been completed first (gms.chem.amounts.complete)."""
    from gms import greenness                       # imported here: greenness uses Score

    metrics, totals = mass_metrics(reaction)
    rows = hazard_rows(reaction, store)
    summary = hazard_summary(rows, totals["product_mass_g"], totals["total_input_mass_g"])
    pm = totals["product_mass_g"]
    metrics["hazmat"] = (summary["high_hazard_mass_g"] / pm) if pm > 0 else float("inf")
    y = metrics.get("yield")
    eco = ecoscale(reaction, y if y is not None else reaction.reported_yield_percent, rows)
    metrics["ecoscale"] = eco["score"] if eco else None
    for key in registry.metrics():
        metrics.setdefault(key, None)

    total = totals["total_input_mass_g"]
    contributions = [{
        "name": c.name, "role": c.role, "stage": c.stage, "mass_g": c.mass,
        "share_of_input": (c.mass / total) if total > 0 else 0.0,
        "pmi_contribution": (c.mass / pm) if pm > 0 else float("inf"),
        "estimated": c.estimated, "note": c.note,
    } for c in reaction.inputs]

    estimates, affected = [], set()
    for c in reaction.components:
        if c.estimated and c.mass > 0:
            estimates.append(f"{c.name} ({c.role.replace('_', ' ')}): {c.mass:.3g} g is an estimate"
                             + (f" ({c.note})" if c.note else ""))
            if c.role == "product":
                affected.update(k for k, m in registry.metrics().items() if m.yield_dependent)
            elif c.role in STOICHIOMETRIC_ROLES:
                affected.update(("rme", "ce", "erf", "yield", "ecoscale") + AUX_METRICS)
            else:
                affected.update(AUX_METRICS)
    if eco and not eco["price_assessed"]:
        estimates.append("EcoScale: the price of the reaction components was not assessed (0 penalty points assumed).")
    if eco and y is None:
        estimates.append("EcoScale: the yield is the reported yield; it could not be computed from the amounts.")
    if summary["unknown"]:
        estimates.append("No hazard data for: " + ", ".join(summary["unknown"])
                         + ". These materials add no safety penalty and are not in the hazard scores.")
        affected.update(("ecoscale", "hazmat"))

    s = Score(metrics=metrics, totals=totals, ecoscale=eco, hazards=rows, hazard_summary=summary,
              contributions=contributions, warnings=_warnings(reaction, metrics), estimates=estimates,
              estimated_metrics=[k for k in registry.display_order() if k in affected])
    s.greenness = greenness.assess(s)
    return s


def prepare_and_score(reaction: Reaction, store: Optional[hazard.HazardStore] = None,
                      online: Optional[bool] = None) -> tuple[Reaction, Score, list[str]]:
    """Convenience for the UI and scripts: complete a copy of the reaction, then score it."""
    r = reaction.copy()
    issues = amounts.complete(r, online)
    return r, score(r, store), issues


def measured_only(reaction: Reaction) -> Reaction:
    """Copy of the reaction without the materials whose mass is an estimate."""
    r = reaction.copy()
    r.components = [c for c in r.components if not (c.estimated and c.role != "product")]
    return r


__all__ = ["Score", "score", "prepare_and_score", "with_yield", "theoretical_product_mass",
           "mass_metrics", "hazard_rows", "measured_only", "Component"]
