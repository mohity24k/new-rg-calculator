"""Yield scenarios and break-even yields for a candidate change.

The yield of an untested change is not known, so no single yield is predicted. Instead:
  * the candidate is scored at the original yield (not a claim that the yields are equal),
  * the candidate can be scored at any yield (`scenario`),
  * the break-even yield is the lowest yield at which the candidate is still at least as good
    as the original on a given metric.

All functions take yields as fractions (0.32 = 32%).
"""

from __future__ import annotations

from typing import Optional

from gms import scoring
from gms.model import STOICHIOMETRIC_ROLES, Reaction


def pmi_breakeven(candidate_input_mass: float, max_product_mass: float, original_pmi: float) -> float:
    """Lowest yield at which the candidate's PMI is not above the original PMI.
    PMI(y) = inputs / (y x max product)  =>  y >= inputs / (max product x original PMI).
    Check: inputs 40 g, maximum product 8 g, original PMI 15.625 -> 0.32."""
    return candidate_input_mass / (max_product_mass * original_pmi)


def rme_breakeven(candidate_reactant_mass: float, max_product_mass: float, original_rme_percent: float) -> float:
    """Lowest yield at which the candidate's RME is not below the original RME.
    RME(y) = 100 x y x max product / reactant mass  =>  y >= RME_orig / 100 x reactant mass / max product."""
    return original_rme_percent / 100.0 * candidate_reactant_mass / max_product_mass


def ecoscale_breakeven(candidate_other_penalties: float, original_ecoscale: float) -> float:
    """Lowest yield at which the candidate's EcoScale is not below the original EcoScale.
    EcoScale(y) = 100 - (100 - 100 y) / 2 - other penalties
      =>  y >= 1 - (100 - other penalties - original EcoScale) / 50."""
    return 1.0 - (100.0 - candidate_other_penalties - original_ecoscale) / 50.0


def describe(y: Optional[float]) -> dict:
    """Break-even yield as a value in 0..1 with a status:
    'always' (greener at any yield), 'never' (not reachable below 100%), 'at' (a yield in between)."""
    if y is None:
        return {"status": "unknown", "yield": None}
    if y <= 0:
        return {"status": "always", "yield": 0.0}
    if y > 1.0 + 1e-9:
        return {"status": "never", "yield": y}
    return {"status": "at", "yield": min(y, 1.0)}


def greenness_breakeven(candidate: Reaction, original_score_value: float, store=None,
                        tol: float = 1e-3) -> Optional[float]:
    """Lowest yield at which the candidate's combined greenness score reaches the original
    score (bisection; the score does not decrease when the yield rises)."""
    def g(y: float) -> Optional[float]:
        return scoring.score(scoring.with_yield(candidate, y), store).greenness["score"]

    top = g(1.0)
    if top is None:
        return None
    if top < original_score_value - 1e-12:
        return float("inf")
    lo, hi = 0.0, 1.0
    if (g(1e-6) or 0.0) >= original_score_value:
        return 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if (g(mid) or 0.0) >= original_score_value:
            hi = mid
        else:
            lo = mid
    return hi


def breakevens(original_score, candidate: Reaction, candidate_score, store=None) -> dict:
    """Break-even yields of a candidate against the original, per metric."""
    theo = scoring.theoretical_product_mass(candidate)
    out = {"pmi": describe(None), "rme": describe(None), "ecoscale": describe(None), "greenness": describe(None)}
    if not theo:
        return out
    om = original_score.metrics
    inputs = sum(c.mass for c in candidate.inputs)
    reactant_mass = sum(c.mass for c in candidate.by_role(*STOICHIOMETRIC_ROLES))
    if om.get("pmi") and om["pmi"] != float("inf"):
        out["pmi"] = describe(pmi_breakeven(inputs, theo, om["pmi"]))
    if om.get("rme"):
        out["rme"] = describe(rme_breakeven(reactant_mass, theo, om["rme"]))
    if candidate_score.ecoscale and om.get("ecoscale") is not None:
        other = candidate_score.ecoscale["total_penalty"] - candidate_score.ecoscale["penalties"]["yield"]
        out["ecoscale"] = describe(ecoscale_breakeven(other, om["ecoscale"]))
    if original_score.greenness["score"] is not None:
        out["greenness"] = describe(greenness_breakeven(candidate, original_score.greenness["score"], store))
    return out


def scenario(candidate: Reaction, yield_fraction: float, store=None):
    """The candidate scored at the given yield."""
    return scoring.score(scoring.with_yield(candidate, yield_fraction), store)
