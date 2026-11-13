"""The no-regression check: "greener without affecting other factors" as a test, not a claim.

A candidate passes only if
    1. it makes the same product,
    2. hazard does not get worse,
    3. no metric gets worse beyond its tolerance (config/metrics.yaml),
    4. cost does not get worse, where the cost is known (config/prices.yaml).
The result lists every comparison that was made, so the user can see what was checked.
"""

from __future__ import annotations

import math
from typing import Optional

from gms import config
from gms.chem import hazard
from gms.chem.structure import canon
from gms.metrics import registry
from gms.model import Reaction

PASS, FAIL, BETTER, TOLERATED, SAME, INFO, NOT_COMPARED = "pass", "fail", "better", "tolerated", "same", "trade-off", "not compared"


def _row(group, item, original, candidate, result, detail="") -> dict:
    return {"group": group, "item": item, "original": original, "candidate": candidate,
            "result": result, "detail": detail}


def _price_table() -> tuple[dict, dict]:
    cfg = config.load("prices")
    by_smiles, by_name = {}, {}
    for p in cfg.get("prices") or []:
        if p.get("smiles") and canon(p["smiles"]):
            by_smiles[canon(p["smiles"])] = float(p["per_kg"])
        if p.get("name"):
            by_name[p["name"].strip().lower()] = float(p["per_kg"])
    return by_smiles, by_name


def material_cost(reaction: Reaction) -> tuple[Optional[float], list[str]]:
    """(cost of all inputs, names without a price). Cost is None if any material has no price."""
    by_smiles, by_name = _price_table()
    total, missing = 0.0, []
    for c in reaction.inputs:
        if c.role == "water" or c.mass <= 0:
            continue
        price = by_smiles.get(canon(c.smiles)) if c.smiles else None
        if price is None:
            price = by_name.get(c.name.strip().lower())
        if price is None:
            missing.append(c.name)
        else:
            total += price * c.mass / 1000.0
    return (None if missing else total), missing


def check(original: Reaction, o_score, candidate: Reaction, c_score) -> dict:
    rows = []

    # 1. same product
    po, pc = original.product, candidate.product
    same = po is not None and pc is not None and (
        (canon(po.smiles) == canon(pc.smiles)) if po.smiles and pc.smiles else po.name == pc.name)
    rows.append(_row("Product", "Same product", po.name if po else "none", pc.name if pc else "none",
                     PASS if same else FAIL,
                     "identical structure" if same and po.smiles else "identical name" if same else "the product differs"))

    # 2. hazard
    o_names = {c.name for c in original.inputs}
    c_names = {c.name for c in candidate.inputs}
    added = [r for r in c_score.hazards if r["name"] not in o_names]
    removed = [r for r in o_score.hazards if r["name"] not in c_names]
    unknown = [r["name"] for r in added if r["worst"] is None]
    if added:
        rows.append(_row("Hazard", "Hazard data for the new materials", "", ", ".join(r["name"] for r in added),
                         FAIL if unknown else PASS,
                         ("no hazard data for " + ", ".join(unknown) + ": the check cannot pass") if unknown
                         else "GHS data found for every new material"))
    if added and removed and not unknown:
        a, b = max(r["worst"] for r in added), max((r["worst"] for r in removed if r["worst"] is not None), default=None)
        if b is not None:
            rows.append(_row("Hazard", "Worst hazard score: new materials vs the materials they replace", b, a,
                             FAIL if a > b else BETTER if a < b else SAME,
                             f"{', '.join(r['name'] for r in removed)} -> {', '.join(r['name'] for r in added)}"))
    ow = max((v for v in o_score.hazard_summary["worst"].values() if v is not None), default=None)
    cw = max((v for v in c_score.hazard_summary["worst"].values() if v is not None), default=None)
    if ow is not None and cw is not None:
        rows.append(_row("Hazard", "Worst hazard score of any material in the reaction", ow, cw,
                         FAIL if cw > ow else BETTER if cw < ow else SAME, "scale 1-10, config/hazard.yaml"))
    for d in hazard.DIMENSIONS:
        o, c = o_score.hazard_summary["worst"][d], c_score.hazard_summary["worst"][d]
        if o is None or c is None:
            continue
        rows.append(_row("Hazard", f"Worst {d} score", o, c, INFO if c > o else BETTER if c < o else SAME,
                         "this dimension gets worse; the overall worst score decides the check" if c > o else ""))

    osc, csc = o_score.hazard_summary["solvent_class"], c_score.hazard_summary["solvent_class"]
    if osc is not None and csc is not None:
        rows.append(_row("Hazard", "Solvent class (CHEM21, mass-weighted; 1 recommended - 4 highly hazardous)",
                         round(osc, 2), round(csc, 2),
                         FAIL if csc > osc + 0.01 else BETTER if csc < osc - 0.01 else SAME,
                         "the solvents used are ranked worse on average" if csc > osc + 0.01 else ""))

    # 3. metrics
    for key in config.load("suggestions")["checked_metrics"]:
        m = registry.get(key)
        o, c = o_score.metrics.get(key), c_score.metrics.get(key)
        ch = registry.change(key, o, c)
        if ch is None:
            rows.append(_row("Metrics", m.label, o, c, NOT_COMPARED, "value missing"))
            continue
        tol = registry.tolerance(key, o)
        if ch < -(tol + 1e-9):
            res, detail = FAIL, f"worse by {abs(ch):.3g} {m.unit} (tolerance {tol:.3g})"
        elif ch < -1e-9:
            res, detail = TOLERATED, f"worse by {abs(ch):.3g} {m.unit}, inside the tolerance of {tol:.3g}"
        elif ch > 1e-9 and not math.isinf(ch):
            res, detail = BETTER, f"better by {ch:.3g} {m.unit}"
        else:
            res, detail = SAME, ""
        rows.append(_row("Metrics", m.label, o, c, res, detail))

    # 4. cost
    oc, o_missing = material_cost(original)
    cc, c_missing = material_cost(candidate)
    if oc is None or cc is None:
        missing = sorted(set(o_missing + c_missing))
        rows.append(_row("Cost", "Material cost", None, None, NOT_COMPARED,
                         "no price for: " + ", ".join(missing[:6]) + ("..." if len(missing) > 6 else "")
                         + " (add prices in config/prices.yaml)"))
    else:
        tol = config.load("suggestions")["cost_tolerance_rel"] * oc
        rows.append(_row("Cost", "Material cost", round(oc, 4), round(cc, 4),
                         FAIL if cc > oc + tol else BETTER if cc < oc else SAME,
                         f"currency: {config.load('prices').get('currency') or 'as entered'}"))

    failed = [r for r in rows if r["result"] == FAIL]
    return {"passed": not failed, "rows": rows,
            "failures": [f"{r['item']}: {r['detail']}" for r in failed],
            "trade_offs": [f"{r['item']}: {r['detail'] or 'worse'}" for r in rows if r["result"] in (TOLERATED, INFO)]}
