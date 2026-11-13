"""Greener alternatives for a reaction.

Candidates come from
  * the curated substitution table (gms/data/substitutions.yaml: solvents, workup and
    chromatography solvents, reagents, catalysts, bases, purification method), filtered by
    reaction class, and
  * two built-in rules: cut an excess of a reactant or reagent, and run more concentrated.

Each candidate is an edited copy of the reaction. It is scored by the same calculator at the
ORIGINAL yield (this is not a claim that the yields are equal), tested by the no-regression
check, and given break-even yields. A candidate that fails the check is kept in the result with
its reasons, but it is not a suggestion: the UI lists it separately.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from gms import breakeven, config, edits, precedents, regression, scoring
from gms.chem import hazard, solvents
from gms.chem.structure import canon
from gms.metrics import registry
from gms.model import STOICHIOMETRIC_ROLES, Reaction

KIND_ROLES = {
    "reaction_solvent": ("reaction_solvent",),
    "workup_solvent": ("workup",),
    "purification_solvent": ("purification",),
    "reagent": ("reagent", "reactant", "additive"),
    "catalyst": ("catalyst",),
    "base": ("base",),
    "additive": ("additive", "reagent", "catalyst"),
}
KIND_LABEL = {
    "reaction_solvent": "Reaction solvent", "workup_solvent": "Workup solvent",
    "purification_solvent": "Chromatography solvent", "reagent": "Reagent", "catalyst": "Catalyst",
    "base": "Base", "additive": "Additive", "purification_method": "Purification method",
    "stoichiometry": "Stoichiometry", "concentration": "Concentration",
}
EVIDENCE_LABEL = {
    "class_specific_study": "a published study tested this change on this reaction class",
    "general_guide": "a general selection guide recommends it; it was not tested on this reaction class",
    "single_report": "a single report; narrow scope",
    "built_in": "a general principle; no specific study",
}
PRECEDENT_GROUP = {"reaction_solvent": "solvent", "reagent": "agent", "catalyst": "agent", "base": "agent",
                   "additive": "agent"}
_ROLE_FIX = {"ligand": "catalyst", "solvent": "reaction_solvent"}
_RATIO_RE = re.compile(r"\((\d+(?:\.\d+)?):(\d+(?:\.\d+)?)\s*v/v\)")


@dataclass
class Candidate:
    id: str
    kind: str
    title: str
    target: str
    replacement: str
    edit_list: list
    problem: str = ""
    scope: str = ""
    caveats: str = ""
    rule_id: str = ""
    evidence_level: str = "built_in"
    sources: list = field(default_factory=list)
    precedent: dict = field(default_factory=dict)
    confidence: str = "low"
    confidence_reason: str = ""
    status: str = "passed"            # "passed" | "rejected" | "not_applicable"
    reason: str = ""                  # why it was rejected or could not be built
    reaction: Optional[Reaction] = None
    score: Optional[object] = None
    check: Optional[dict] = None
    breakeven: Optional[dict] = None
    changes: dict = field(default_factory=dict)       # metric key -> improvement at the original yield
    greenness_gain: Optional[float] = None
    assumptions: list = field(default_factory=list)

    def summary(self) -> dict:
        """Serializable summary (reports, feedback store)."""
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "target": self.target,
            "replacement": self.replacement, "rule_id": self.rule_id, "evidence_level": self.evidence_level,
            "sources": self.sources, "precedent": self.precedent, "confidence": self.confidence,
            "confidence_reason": self.confidence_reason, "status": self.status, "reason": self.reason,
            "problem": self.problem, "scope": self.scope, "caveats": self.caveats,
            "changes_at_original_yield": self.changes, "greenness_gain": self.greenness_gain,
            "breakeven": self.breakeven, "assumptions": self.assumptions,
            "check": self.check,
            "metrics": self.score.metrics if self.score else None,
            "verdict": self.score.greenness["label"] if self.score else None,
        }


def rules() -> list[dict]:
    return config.data("substitutions.yaml")["rules"]


def _cid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _mixture_parts(rep: dict) -> Optional[tuple[list[dict], str]]:
    """A replacement whose SMILES is a mixture of known solvents -> (parts with volume fractions, note)."""
    frags = [canon(f) for f in (rep.get("smiles") or "").split(".")]
    if len(frags) < 2 or not all(f and solvents.lookup(f) for f in frags):
        return None
    m = _RATIO_RE.search(rep["name"])
    if m and len(frags) == 2:
        a, b = float(m.group(1)), float(m.group(2))
        fr, note = [a / (a + b), b / (a + b)], f"{m.group(1)}:{m.group(2)} by volume"
    else:
        fr, note = [1.0 / len(frags)] * len(frags), "equal volumes assumed; the real ratio must be found by experiment"
    return [{"name": solvents.lookup(f).name, "smiles": f, "fraction": x} for f, x in zip(frags, fr)], note


def _confidence(evidence: str, sources: list, prec: dict) -> tuple[str, str]:
    cfg = config.load("suggestions")["confidence"]
    verified = any(s.get("verified") for s in sources)
    n = prec.get("n_sole", prec.get("n")) if prec.get("available") else None
    if evidence == "class_specific_study" and verified and n is not None and n >= cfg["precedents_for_high"]:
        return "high", f"a study on this reaction class and {n} precedents in the dataset"
    if evidence == "class_specific_study" and verified:
        return "moderate", "a study on this reaction class" + (
            (f"; only {n} precedents in the dataset" if n else "; no precedent in the dataset") if n is not None else "; no precedent data for this kind of change")
    if evidence != "single_report" and n is not None and n >= cfg["precedents_for_moderate"]:
        return "moderate", f"{n} precedents in the dataset; no study of this change on this reaction class"
    why = {"general_guide": "a general guide only", "single_report": "a single report",
           "built_in": "a general principle"}.get(evidence, "the reference could not be verified")
    if evidence == "class_specific_study" and not verified:
        why = "the reference could not be verified"
    return "low", why + ("" if n is None else f"; {n} precedents in the dataset")


def _evaluate(c: Candidate, reaction: Reaction, base, store) -> Candidate:
    try:
        c.reaction = edits.apply(reaction, c.edit_list)
    except edits.EditError as e:
        c.status, c.reason = "not_applicable", f"The change cannot be computed: {e}."
        return c
    c.score = scoring.score(c.reaction, store)
    c.check = regression.check(reaction, base, c.reaction, c.score)
    c.breakeven = breakeven.breakevens(base, c.reaction, c.score, store)
    for k in registry.display_order():
        c.changes[k] = registry.change(k, base.metrics.get(k), c.score.metrics.get(k))
    g0, g1 = base.greenness["score"], c.score.greenness["score"]
    c.greenness_gain = (g1 - g0) if g0 is not None and g1 is not None else None
    c.assumptions = [f"{x.name}: {x.note}" for x in c.reaction.components
                     if x.estimated and x.note and reaction.find(x.name) is None]
    if not c.check["passed"]:
        c.status, c.reason = "rejected", "; ".join(c.check["failures"])
    elif not any(r["result"] == regression.BETTER for r in c.check["rows"]):
        c.status, c.reason = "rejected", "Nothing gets better: no metric or hazard score improves."
    return c


def _table_candidates(reaction: Reaction) -> list[Candidate]:
    out = []
    cls = reaction.reaction_class
    for rule in rules():
        if "any" not in rule["classes"] and cls not in rule["classes"]:
            continue
        kind = rule["kind"]
        if kind == "purification_method":
            step = rule["target"]["step"]
            if step not in reaction.workup_steps:
                continue
            for rep in rule["replacements"]:
                cr = config.load("suggestions")["crystallisation"]
                pm = reaction.product.mass if reaction.product else 0.0
                ed = [{"op": "replace_step", "old": step, "new": rep["step"]},
                      {"op": "remove_stage", "stage": "purification"},
                      {"op": "add", "component": {
                          "name": f"{cr['solvent']} (crystallisation solvent, assumed)", "role": "purification",
                          "smiles": cr["smiles"], "volume_ml": cr["ml_per_g_product"] * pm,
                          "note": f"assumed {cr['ml_per_g_product']:g} mL per g of product (config/suggestions.yaml)"}}]
                out.append(Candidate(
                    id=_cid(rule["id"], rep["step"]), kind=kind, title=f"{rep['step']} in place of {step.lower()}",
                    target=step, replacement=rep["step"], edit_list=ed, problem=rule.get("problem", ""),
                    scope=rep.get("scope", ""), caveats=rep.get("caveats", ""), rule_id=rule["id"],
                    evidence_level=rep["evidence"], sources=rep.get("sources", []),
                    precedent={"available": False, "reason": "the dataset has no workup or purification data"}))
            continue
        tsmi = canon(rule["target"].get("smiles"))
        for comp in reaction.by_role(*KIND_ROLES[kind]):
            if not comp.smiles or canon(comp.smiles) != tsmi:
                continue
            for rep in rule["replacements"]:
                w = {"name": rep["name"], "smiles": rep["smiles"]}
                mix = _mixture_parts(rep) if kind.endswith("solvent") else None
                if mix:
                    w["parts"], w["parts_note"] = mix
                elif canon(rep["smiles"]) == "O":
                    w["role"] = "water"
                ed = [{"op": "replace", "name": comp.name, "with": w, "amount": rep.get("amount", "same_volume")}]
                for extra in rep.get("adds") or []:
                    role = _ROLE_FIX.get(extra.get("role"), extra.get("role") or "additive")
                    if any(canon(x.smiles) == canon(extra.get("smiles")) for x in reaction.inputs if x.smiles):
                        continue                      # already in the reaction
                    ed.append({"op": "add", "component": {**extra, "role": role}})
                for gone in rep.get("removes") or []:
                    hit = next((x for x in reaction.inputs if x.smiles and canon(x.smiles) == canon(gone.get("smiles"))), None)
                    if hit:
                        ed.append({"op": "remove", "name": hit.name})
                out.append(Candidate(
                    id=_cid(rule["id"], comp.name, rep["name"]), kind=kind,
                    title=f"{rep['name']} in place of {comp.name}", target=comp.name, replacement=rep["name"],
                    edit_list=ed, problem=rule.get("problem", ""), scope=rep.get("scope", ""),
                    caveats=rep.get("caveats", ""), rule_id=rule["id"], evidence_level=rep["evidence"],
                    sources=rep.get("sources", []),
                    precedent=(
                        {"available": False, "reason": "the dataset cannot identify these conditions"}
                        if rep.get("precedent_search") is False else
                        precedents.support(cls, rep["smiles"], PRECEDENT_GROUP[kind]) if kind in PRECEDENT_GROUP else
                        {"available": False, "reason": "the dataset has no workup or purification data"})))
    return out


def _built_in_candidates(reaction: Reaction) -> list[Candidate]:
    out = []
    cfg = config.load("suggestions")
    cls = reaction.reaction_class
    st = cfg["stoichiometry"]
    for comp in reaction.by_role(*STOICHIOMETRIC_ROLES):
        coef = comp.coefficient or 1.0
        if comp.limiting or not comp.equiv or comp.equiv / coef < st["min_equiv"]:
            continue
        target, basis = st["target_equiv"], f"default target of {st['target_equiv']:g} equivalents (config/suggestions.yaml)"
        prec = {"available": False, "reason": "no precedent data for the amount of this material in this reaction class"}
        typ = None
        if comp.role == "reactant":
            t = precedents.typical(cls, "equiv:reactant")
            if t:
                typ = {"n": t["n"], "q1": t["q1"], "median": t["median"], "q3": t["q3"],
                       "what": "the coupling partner in excess"}
        else:
            sup = precedents.support(cls, comp.smiles, "agent")
            if sup.get("available") and sup.get("equiv_n"):
                typ = {"n": sup["equiv_n"], "q1": sup["equiv_q1"], "median": sup["equiv_median"],
                       "q3": sup["equiv_q3"], "what": comp.name}
        if typ and typ["n"] >= st["min_precedents"]:
            prec = {"available": True, "n": typ["n"], "n_class": typ["n"], "examples": [],
                    "note": f"{typ['n']:,} precedents of this reaction class use {typ['what']} at a median of "
                            f"{typ['median']:.2g} equivalents (middle half {typ['q1']:.2g} to {typ['q3']:.2g})"}
            if typ["median"] < comp.equiv / coef:
                target = max(1.0, typ["median"])
                basis = f"median of {typ['n']:,} precedents of this reaction class"
        target *= coef
        if target >= comp.equiv:
            continue
        out.append(Candidate(
            id=_cid("stoich", comp.name), kind="stoichiometry",
            title=f"{target:.3g} equivalents of {comp.name} in place of {comp.equiv:.3g}",
            target=comp.name, replacement=f"{target:.3g} equiv", rule_id="built-in: excess reagent",
            edit_list=[{"op": "set_equiv", "name": comp.name, "equiv": target}],
            problem=f"{comp.name} is used at {comp.equiv:.3g} equivalents. The excess becomes waste.",
            scope=f"Target amount: {basis}.",
            caveats="An excess is often used on purpose: to drive a slow reaction to completion, or because the "
                    "reagent decomposes (for example protodeboronation of boronic acids, hydrolysis of activated "
                    "esters). A smaller excess can lower the conversion. Test it.",
            precedent=prec))
    co = cfg["concentration"]
    lim = next((x for x in reaction.by_role(*STOICHIOMETRIC_ROLES) if x.limiting), None)
    solv = reaction.by_role("reaction_solvent") + [x for x in reaction.by_role("water") if x.stage == "reaction"]
    vol = sum(x.volume_ml or 0.0 for x in solv)
    if lim and lim.moles and vol:
        per = vol / (lim.moles * 1000)
        typ = precedents.typical(cls, "ml_per_mmol")
        target, basis = co["target_ml_per_mmol"], f"default target (config/suggestions.yaml)"
        prec = {"available": False, "reason": "no precedent data for the concentration in this reaction class"}
        if typ and typ["n"] >= st["min_precedents"]:
            prec = {"available": True, "n": typ["n"], "n_class": typ["n"], "examples": [],
                    "note": f"{typ['n']:,} precedents of this reaction class use a median of {typ['median']:.2g} mL "
                            f"of solvent per mmol (middle half {typ['q1']:.2g} to {typ['q3']:.2g})"}
            target, basis = max(typ["median"], 1.0), f"median of {typ['n']} precedents of this reaction class"
        if per > co["max_ml_per_mmol"] and target < per:
            out.append(Candidate(
                id=_cid("conc"), kind="concentration",
                title=f"Run at {target:.3g} mL of solvent per mmol in place of {per:.3g}",
                target=", ".join(x.name for x in solv), replacement=f"{target:.3g} mL/mmol ({1 / target:.2g} M)",
                rule_id="built-in: dilute reaction",
                edit_list=[{"op": "scale_mass", "name": x.name, "factor": target / per} for x in solv],
                problem=f"The reaction uses {per:.3g} mL of solvent per mmol of {lim.name} ({1 / per:.2g} M).",
                scope=f"Target concentration: {basis}.",
                caveats="Check solubility and stirring at the higher concentration. Exothermic reactions and "
                        "reactions with a competing bimolecular side reaction can behave differently.",
                precedent=prec))
    return out


def suggest(reaction: Reaction, base=None, store: Optional[hazard.HazardStore] = None) -> list[Candidate]:
    """All candidates for a completed reaction: passed ones first (largest gain first), then the
    rejected ones, then those that could not be computed."""
    store = store or hazard.store()
    base = base or scoring.score(reaction, store)
    cands = _table_candidates(reaction) + _built_in_candidates(reaction)
    for c in cands:
        c.confidence, c.confidence_reason = _confidence(c.evidence_level, c.sources, c.precedent)
        _evaluate(c, reaction, base, store)
    order = {"passed": 0, "rejected": 1, "not_applicable": 2}
    cands.sort(key=lambda c: (order[c.status], -(c.greenness_gain or 0.0), c.title))
    return cands
