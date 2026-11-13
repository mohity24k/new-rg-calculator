"""Diagnosis: why is this reaction not green, and how much would fixing each cause gain?

Every finding names the material or step that causes it and carries the gain of an idealised
fix. The gain is computed, not estimated: the fix is applied to a copy of the reaction
(gms.edits) and the calculator (gms.scoring) is run again. The fixes are "what if" targets
that show where the leverage is. Whether a real alternative exists is the job of gms.suggest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from gms import config, edits, scoring
from gms.chem import hazard
from gms.metrics import registry
from gms.model import STOICHIOMETRIC_ROLES, Reaction

GAIN_KEYS = ("pmi", "e_factor", "rme", "ae", "ecoscale", "hazmat")
CHROMATOGRAPHY = "Classical chromatography"


@dataclass
class Finding:
    code: str
    title: str
    detail: str
    targets: list                     # names of the materials or steps concerned
    what_if: str = ""                 # the idealised fix that the gain refers to
    gains: dict = field(default_factory=dict)       # metric key -> improvement (positive = better)
    after: dict = field(default_factory=dict)       # metric key -> value after the fix
    greenness_gain: Optional[float] = None
    verdict_after: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class _Benign(hazard.HazardStore):
    """Hazard store in which the named materials have no hazard (for the hazard what-if)."""

    def __init__(self, base, benign: set):
        self._base, self._benign = base, benign

    def lookup(self, name, smiles=None, online=None):
        if name in self._benign:
            return hazard.HazardProfile(name=name, status="not_classified", source="what-if: no hazard")
        return self._base.lookup(name, smiles, online)


def _gain(base, finding: Finding, reaction: Reaction, store) -> Finding:
    after = scoring.score(reaction, store)
    for k in GAIN_KEYS:
        finding.gains[k] = registry.change(k, base.metrics.get(k), after.metrics.get(k))
        finding.after[k] = after.metrics.get(k)
    g0, g1 = base.greenness["score"], after.greenness["score"]
    finding.greenness_gain = (g1 - g0) if g0 is not None and g1 is not None else None
    finding.verdict_after = after.greenness["label"]
    return finding


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def diagnose(reaction: Reaction, base=None, store: Optional[hazard.HazardStore] = None) -> list[Finding]:
    """Findings for a completed reaction, ranked by the gain in the combined score."""
    cfg = config.load("diagnosis")
    store = store or hazard.store()
    base = base or scoring.score(reaction, store)
    out: list[Finding] = []
    total = base.totals["total_input_mass_g"]
    pm = base.totals["product_mass_g"]
    if total <= 0 or pm <= 0:
        return out

    # 1. mass hotspots
    c = cfg["mass_hotspot"]
    hot = sorted((x for x in base.contributions if x["share_of_input"] >= c["min_share_of_input"]),
                 key=lambda x: -x["share_of_input"])[:c["max_findings"]]
    for x in hot:
        f = Finding("mass_hotspot", f"{x['name']} is {_pct(x['share_of_input'])} of the input mass",
                    f"{x['mass_g']:.3g} g of {x['name']} ({x['role'].replace('_', ' ')}) adds "
                    f"{x['pmi_contribution']:.3g} kg/kg to the PMI of {base.metrics['pmi']:.3g}.",
                    [x["name"]], what_if=f"Use {_pct(c['what_if_factor'])} of the amount of {x['name']}.")
        out.append(_gain(base, f, edits.apply(reaction, [
            {"op": "scale_mass", "name": x["name"], "factor": c["what_if_factor"]}]), store))

    # 2. share of solvents and of the workup / purification stage
    by_stage = {}
    for x in base.contributions:
        by_stage[x["stage"]] = by_stage.get(x["stage"], 0.0) + x["mass_g"]
    after_reaction = by_stage.get("workup", 0.0) + by_stage.get("purification", 0.0)
    if after_reaction / total >= cfg["solvent_share"]["min_share_of_input"]:
        r2 = edits.apply(reaction, [{"op": "scale_mass", "name": x["name"], "factor": 0.5}
                                    for x in base.contributions if x["stage"] in ("workup", "purification")])
        f = Finding("workup_share", f"Workup and purification are {_pct(after_reaction / total)} of the input mass",
                    f"{after_reaction:.3g} g of {total:.3g} g is used after the reaction "
                    "(extraction, washing, drying, chromatography).",
                    [x["name"] for x in base.contributions if x["stage"] in ("workup", "purification")],
                    what_if="Use half the amount of every workup and purification material.")
        out.append(_gain(base, f, r2, store))

    # 3. dilute reaction
    lim = next((x for x in reaction.by_role(*STOICHIOMETRIC_ROLES) if x.limiting), None)
    solv = reaction.by_role("reaction_solvent")
    vol = sum(x.volume_ml or 0.0 for x in solv)
    if lim and lim.moles and vol:
        per = vol / (lim.moles * 1000)
        d = cfg["dilute_reaction"]
        if per > d["max_ml_per_mmol"]:
            factor = d["target_ml_per_mmol"] / per
            f = Finding("dilute_reaction", f"The reaction is dilute ({per:.3g} mL of solvent per mmol)",
                        f"{vol:.3g} mL of reaction solvent for {lim.moles * 1000:.3g} mmol of {lim.name} "
                        f"({1 / per:.2g} M).", [x.name for x in solv],
                        what_if=f"Run at {d['target_ml_per_mmol']:g} mL per mmol ({1 / d['target_ml_per_mmol']:.2g} M).")
            out.append(_gain(base, f, edits.apply(reaction, [
                {"op": "scale_mass", "name": x.name, "factor": factor} for x in solv]), store))

    # 4. excess of a reactant or reagent
    e = cfg["excess_reagent"]
    for x in reaction.by_role(*STOICHIOMETRIC_ROLES):
        if x.limiting or not x.equiv or x.equiv / (x.coefficient or 1.0) < e["min_equiv"]:
            continue
        target = e["target_equiv"] * (x.coefficient or 1.0)
        f = Finding("excess_reagent", f"{x.name} is used in excess ({x.equiv:.3g} equivalents)",
                    f"{x.mass:.3g} g of {x.name} is charged; {x.mass * (x.coefficient or 1.0) / x.equiv:.3g} g "
                    "is the stoichiometric amount.", [x.name],
                    what_if=f"Use {target:.3g} equivalents of {x.name}.")
        out.append(_gain(base, f, edits.apply(reaction, [{"op": "set_equiv", "name": x.name, "equiv": target}]), store))

    # 5. low yield
    y = base.metrics.get("yield")
    ly = cfg["low_yield"]
    if y is not None and y < ly["below_percent"]:
        f = Finding("low_yield", f"The yield is {y:.0f}%",
                    "Every mass metric is divided by the product mass, so lost product raises PMI and E-factor "
                    "and lowers RME. The cause can be incomplete conversion, side reactions or losses in isolation.",
                    [reaction.product.name],
                    what_if=f"Same inputs at {ly['target_percent']:g}% yield.")
        out.append(_gain(base, f, scoring.with_yield(reaction, ly["target_percent"] / 100.0), store))

    # 6. wasteful stoichiometric reagent (low atom economy)
    ae = base.metrics.get("ae")
    reagents = [x for x in reaction.by_role("reagent") if x.mass > 0]
    if ae is not None and ae < cfg["wasteful_reagent"]["ae_below"] and reagents:
        worst = max(reagents, key=lambda x: (x.mw or 0.0) * x.coefficient)
        f = Finding("wasteful_reagent", f"Atom economy is {ae:.0f}%: {worst.name} is consumed and not incorporated",
                    f"{worst.name} (MW {worst.mw or 0:.0f}) is a stoichiometric reagent. None of its "
                    "mass ends up in the product, so it becomes waste by design.", [worst.name],
                    what_if=f"A catalytic method that needs no {worst.name}.")
        out.append(_gain(base, f, edits.apply(reaction, [{"op": "remove", "name": worst.name}]), store))

    # 7. hazardous materials
    for row in base.hazards:
        if not row["high_hazard"]:
            continue
        dims = [f"{d} {row[d]}" for d in hazard.DIMENSIONS if row[d] >= config.load("hazard")["high_hazard_min"][d]]
        f = Finding("hazard", f"{row['name']} is a high-hazard material ({', '.join(dims)})",
                    f"Hazard statements: {', '.join(row['h_codes']) or 'none'}."
                    + (f" CHEM21 solvent ranking: {row['chem21_ranking']}." if row["chem21_ranking"] else ""),
                    [row["name"]], what_if=f"Same mass of a material without hazard statements in place of {row['name']}.")
        out.append(_gain(base, f, reaction, _Benign(store, {row["name"]})))

    # 8. chromatography
    if CHROMATOGRAPHY in reaction.workup_steps:
        new = cfg["chromatography"]["replace_with"]
        pur = [x.name for x in reaction.by_role("purification")]
        f = Finding("chromatography", "Purification by column chromatography",
                    "Chromatography costs 10 EcoScale points" + (
                        f" and uses {sum(x.mass for x in reaction.by_role('purification')):.3g} g of eluent and silica."
                        if pur else ". No eluent or silica amounts are given, so the PMI is a lower bound."),
                    [CHROMATOGRAPHY] + pur,
                    what_if=f"{new} in place of chromatography, without the chromatography materials.")
        out.append(_gain(base, f, edits.apply(reaction, [
            {"op": "replace_step", "old": CHROMATOGRAPHY, "new": new},
            {"op": "remove_stage", "stage": "purification"}]), store))

    # 9. harsh conditions (energy, set-up)
    eco = base.ecoscale
    if eco:
        hc = cfg["harsh_conditions"]["min_penalty"]
        if eco["penalties"]["temp_time"] >= hc:
            f = Finding("harsh_conditions", f"Conditions: {eco['temp_time']} ({eco['penalties']['temp_time']} EcoScale points)",
                        "Long heating or cooling below 0 degC costs energy.", [eco["temp_time"]],
                        what_if="The same reaction at room temperature in less than 1 h.")
            out.append(_gain(base, f, edits.apply(reaction, [
                {"op": "set", "field": "temperature_c", "value": 22.0}, {"op": "set", "field": "reflux", "value": False},
                {"op": "set", "field": "time_h", "value": 0.5}]), store))
        if eco["penalties"]["setup"] >= hc:
            f = Finding("special_setup", f"Special set-up: {', '.join(eco['setups'])} ({eco['penalties']['setup']} EcoScale points)",
                        "Special equipment adds operator effort and risk.", list(eco["setups"]),
                        what_if="The same reaction in a common set-up under air.")
            out.append(_gain(base, f, edits.apply(reaction, [
                {"op": "set", "field": "atmosphere", "value": "air"}, {"op": "set", "field": "activation", "value": None},
                {"op": "set", "field": "extra_setups", "value": []}]), store))

    out.sort(key=lambda f: (-(f.greenness_gain or 0.0), -(f.gains.get("pmi") or 0.0)))
    return out


def stage_breakdown(score) -> dict:
    """Input mass and PMI contribution per stage and per role (for the hotspot charts)."""
    pm = score.totals["product_mass_g"]
    stages, roles = {}, {}
    for x in score.contributions:
        for key, d in ((x["stage"], stages), (x["role"], roles)):
            d.setdefault(key, {"mass_g": 0.0, "pmi": 0.0})
            d[key]["mass_g"] += x["mass_g"]
            d[key]["pmi"] += x["mass_g"] / pm if pm > 0 else 0.0
    return {"by_stage": stages, "by_role": roles}
