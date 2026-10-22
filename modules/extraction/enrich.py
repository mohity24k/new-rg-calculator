"""
Reaction record -> structures, masses, moles and validation flags (CPU / network, no GPU).

Run after extraction (`build_dataset.py enrich`), so the GPU never waits on PubChem.
Every check attaches a flag to the record; nothing is dropped.

Checks (thresholds in config/extraction.yaml, section `validation`):
    no_reactant / no_product
    unresolved_structure        reactant, reagent or product without a structure
    moles_mass_mismatch         printed mmol differs from mass / MW
    missing_amounts             reaction components without a mass
    workup_amounts_missing      workup / purification materials without a mass (PMI is a lower bound)
    yield_mismatch              reported yield vs product moles / limiting moles (1:1 assumed)
    mass_balance                product mass above the theoretical maximum
    equiv_out_of_range, catalyst_amount
    temperature_out_of_range, time_out_of_range
    solvent_concentration       reaction solvent mL per mmol of limiting reagent out of range
    purification_amounts_missing  chromatography mentioned but no eluent / silica amounts
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from modules import pdf_extraction as px

_CACHE: dict[str, Optional[str]] = {}


def load_cache(path: Optional[str]):
    if path and Path(path).exists():
        _CACHE.update(json.loads(Path(path).read_text(encoding="utf-8")))


def save_cache(path: Optional[str]):
    if path:
        Path(path).write_text(json.dumps(_CACHE, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def resolve(name: str, online: bool = True) -> Optional[str]:
    """Name -> canonical SMILES via the alias table, OPSIN, then (online) PubChem."""
    name = str(name or "").strip()
    if not name:
        return None
    if name in _CACHE and (_CACHE[name] is not None or online):
        return _CACHE[name]
    for cand in px._variants(name):
        if px._norm(cand) in px.ALIASES:
            s = px._canon(px.ALIASES[px._norm(cand)])
            _CACHE[name] = s
            return s
    transient = False
    for cand in px._variants(name):
        s = px._opsin(cand)
        if not s and online:
            try:
                s = px._pubchem(cand)
            except px._LookupFailed:
                transient = True
        s = px._canon(s) if s else None
        if s:
            _CACHE[name] = s
            return s
    if online and not transient:
        _CACHE[name] = None              # definitive miss: do not ask PubChem again
    return None


def _flag(rec: dict, code: str, detail: str):
    rec["flags"].append({"code": code, "detail": detail})


def _amounts(c: dict, online: bool):
    c.update(smiles=None, mw=None, carbons=None, mass_g=None, volume_ml=None, moles=None)
    if c["role"] == "ignore":
        return
    if c["role"] != "water" and not c.get("generic"):
        c["smiles"] = resolve(c["name"], online)
    c["mw"], c["carbons"] = px._mol_props(c["smiles"])
    q, u = c.get("quantity"), (c.get("unit") or "")
    if q is not None:
        if u in px.MASS_UNITS:
            c["mass_g"] = q * px.MASS_UNITS[u]
        elif u in px.VOL_UNITS:
            c["volume_ml"] = q * px.VOL_UNITS[u]
            d = px._density(c["name"], "water" if c["role"] == "water" else c["role"], c["smiles"])
            if d:
                c["mass_g"] = c["volume_ml"] * d
                c["flags"].append("mass = volume x density")
            else:
                c["flags"].append("volume given, density unknown")
        elif u in px.MOL_UNITS:
            c["moles"] = q * px.MOL_UNITS[u]
    if c["moles"] is None and c.get("mmol") is not None and u not in px.MOL_UNITS:
        if c["mass_g"] is None or not c["mw"]:
            c["moles"] = c["mmol"] / 1000
    if c["mass_g"] is not None and c["moles"] is None and c["mw"]:
        c["moles"] = c["mass_g"] / c["mw"]
    elif c["moles"] is not None and c["mass_g"] is None and c["mw"]:
        c["mass_g"] = c["moles"] * c["mw"]


def enrich_reaction(rec: dict, cfg: dict, online: bool = True) -> dict:
    """New record with smiles / mw / carbons / mass_g / volume_ml / moles per component, and
    validation flags. The input record is not changed."""
    v = cfg["validation"]
    r = copy.deepcopy(rec)
    r["flags"] = [f for f in r.get("flags", []) if f.get("code") in
                  ("inherited_from_general_procedure", "general_procedure_not_found", "yield_out_of_range")]
    comps = r["components"]
    for c in comps:
        _amounts(c, online)

    # equivalents -> moles (reference: moles per 1 equiv of a reactant, else the smallest reactant)
    refs = [c["moles"] / c["equiv"] for c in comps
            if c["role"] == "reactant" and c["moles"] and c.get("equiv") and c["equiv"] > 0]
    lim = [c["moles"] for c in comps if c["role"] == "reactant" and c["moles"]]
    ref = min(refs) if refs else (min(lim) if lim else None)
    for c in comps:
        if c.get("equiv") is not None and c["moles"] is None and ref \
                and c["role"] in ("reactant", "reagent", "catalyst", "base", "additive"):
            c["moles"] = c["equiv"] * ref
            c["flags"].append("derived from equivalents")
            if c["mw"]:
                c["mass_g"] = c["moles"] * c["mw"]

    if r.get("record_type") == "general_procedure":
        r["enriched"] = True
        return r

    # --- validation -------------------------------------------------------------------------
    reactants = [c for c in comps if c["role"] == "reactant"]
    products = [c for c in comps if c["role"] == "product"]
    if not reactants:
        _flag(r, "no_reactant", "no reactant found")
    if not products:
        _flag(r, "no_product", "no product found")
    elif len(products) > 1:
        _flag(r, "several_products", f"{len(products)} products: keep one")
    unresolved = [c["name"] for c in comps if c["role"] in ("reactant", "reagent", "product") and not c["smiles"]]
    if unresolved:
        _flag(r, "unresolved_structure", "no structure for: " + ", ".join(unresolved))
    for c in comps:
        if c.get("mmol") and c["mass_g"] and c["mw"] and (c.get("unit") or "") not in px.MOL_UNITS:
            calc = c["mass_g"] / c["mw"] * 1000
            if abs(calc - c["mmol"]) > v["moles_mass_tolerance"] * max(calc, c["mmol"]):
                c["flags"].append(f"printed {c['mmol']:g} mmol but mass/MW = {calc:.3g} mmol")
                _flag(r, "moles_mass_mismatch", f"{c['name']}: printed {c['mmol']:g} mmol, mass/MW {calc:.3g} mmol")
    missing = [c for c in comps if c["role"] != "ignore" and c["mass_g"] is None]
    in_rxn = [c["name"] for c in missing if c["stage"] == "reaction"]
    after = [c["name"] for c in missing if c["stage"] != "reaction"]
    if in_rxn:
        _flag(r, "missing_amounts", "no mass for: " + ", ".join(in_rxn))
    if after:
        _flag(r, "workup_amounts_missing", "no mass for: " + ", ".join(after))

    lim_mol = min((c["moles"] for c in reactants if c["moles"]), default=None)
    p = products[0] if products else None
    if p and lim_mol and p["mw"]:
        theo = lim_mol * p["mw"]
        r["theoretical_yield_g"] = theo
        if p["mass_g"] is not None and p["mass_g"] > theo * (1 + v["mass_balance_tolerance"]):
            _flag(r, "mass_balance", f"product {p['mass_g']:.4g} g > theoretical {theo:.4g} g (1:1 assumed)")
        if p["moles"]:
            y = 100 * p["moles"] / lim_mol
            r["computed_yield_percent"] = round(y, 1)
            if r.get("yield_percent") is not None and abs(y - r["yield_percent"]) > v["yield_tolerance_pct"]:
                _flag(r, "yield_mismatch",
                      f"reported {r['yield_percent']:g}% vs {y:.1f}% from amounts (1:1 stoichiometry assumed)")
    for c in comps:
        e = c.get("equiv")
        if e is None:
            continue
        if e <= 0 or e > v["max_equiv"]:
            _flag(r, "equiv_out_of_range", f"{c['name']}: {e:g} equiv")
        if c["role"] == "catalyst" and e > v["catalyst_max_equiv"]:
            _flag(r, "catalyst_amount", f"{c['name']}: {e:g} equiv for a catalyst (reagent?)")
    cond = r.get("conditions") or {}
    lo, hi = v["temperature_c"]
    if cond.get("temperature_c") is not None and not lo <= cond["temperature_c"] <= hi:
        _flag(r, "temperature_out_of_range", f"{cond['temperature_c']:g} °C")
    lo, hi = v["time_h"]
    if cond.get("time_h") is not None and not lo < cond["time_h"] <= hi:
        _flag(r, "time_out_of_range", f"{cond['time_h']:g} h")
    vol = sum(c["volume_ml"] or 0 for c in comps
              if c["role"] == "reaction_solvent" or (c["role"] == "water" and c["stage"] == "reaction"))
    if vol and lim_mol:
        conc = vol / (lim_mol * 1000)
        r["solvent_ml_per_mmol"] = round(conc, 3)
        lo, hi = v["solvent_ml_per_mmol"]
        if not lo <= conc <= hi:
            _flag(r, "solvent_concentration", f"{conc:.3g} mL reaction solvent per mmol")
    if r.get("purification") and re.search(r"chromatograph|column|flash", r["purification"], re.I) \
            and not any(c["role"] == "purification" and c["mass_g"] for c in comps):
        _flag(r, "purification_amounts_missing",
              "chromatography reported without eluent / silica amounts: PMI is a lower bound")
    counted = [c for c in comps if c["role"] != "ignore"]
    r["completeness"] = round(sum(c["mass_g"] is not None for c in counted) / len(counted), 3) if counted else 0.0
    r["enriched"] = True
    return r

# ---------------------------------------------------------------------------
# Adapter to the interactive app (one reaction -> existing review table)
# ---------------------------------------------------------------------------


APP_ROLE = {"reactant": "reactant", "reagent": "reactant", "catalyst": "catalyst", "base": "base",
            "additive": "additive", "reaction_solvent": "solvent", "workup": "workup",
            "purification": "workup", "water": "water", "product": "product", "ignore": "ignore"}


def to_review_table(rec: dict) -> pd.DataFrame:
    """Enriched reaction -> the review table of the Upload PDF page (pdf_extraction.COLUMNS).
    Reagents count as reactants and purification as workup, as in the single-reaction mode."""
    rows = []
    pages = ", ".join(str(p) for p in (rec.get("source") or {}).get("pages", []))
    for c in rec["components"]:
        role = APP_ROLE.get(c["role"], "additive")
        flags = list(c.get("flags", []))
        if role != "ignore" and c.get("quantity") is None and c.get("equiv") is None:
            role = "ignore"
            flags = ["no amount stated (set Role back to include)"]
        if role in ("reactant", "product") and not c.get("mw"):
            flags.append("MW unknown: enter MW or SMILES")
        if role != "ignore" and c.get("mass_g") is None:
            flags.append("mass missing")
        if role == "reactant" and c.get("moles") is None:
            flags.append("moles missing")
        if c["role"] == "reagent":
            flags.append("stoichiometric reagent")
        rows.append({"Name": c["name"], "Role": role, "Mass (g)": px._sig(c.get("mass_g")),
                     "Moles (mol)": px._sig(c.get("moles")), "MW (g/mol)": c.get("mw"),
                     "Carbons": c.get("carbons"), "SMILES": c.get("smiles"), "Page": pages,
                     "Flag": "; ".join(dict.fromkeys(flags))})
    df = px._typed(pd.DataFrame(rows, columns=px.COLUMNS))
    if df.empty:
        return df
    df["_o"] = df["Role"].map(px.ROLES.index)
    return df.sort_values(["_o", "Name"], key=lambda c: c.str.lower() if c.dtype == object else c) \
             .drop(columns="_o").reset_index(drop=True)


def review_meta(rec: dict) -> dict:
    """Conditions in the shape pdf_extraction.suggest_eco_inputs expects."""
    cond = rec.get("conditions") or {}
    workup = [w.lower() for w in rec.get("workup") or []]
    if rec.get("purification"):
        workup.append(rec["purification"].lower())
    return {"yield_percent": rec.get("yield_percent"), "temperature_c": cond.get("temperature_c"),
            "time_h": cond.get("time_h"), "atmosphere": cond.get("atmosphere"),
            "activation": cond.get("activation"), "workup": workup, "heated": bool(cond.get("reflux"))}


def summary(rec: dict) -> str:
    prod = next((c["name"] for c in rec["components"] if c["role"] == "product"), "no product")
    label = rec.get("label") or rec.get("procedure_label") or ""
    y = rec.get("yield_percent")
    pages = ",".join(str(p) for p in (rec.get("source") or {}).get("pages", []))
    n_flags = len([f for f in rec.get("flags", []) if f["code"] != "inherited_from_general_procedure"])
    gp = "General procedure " if rec.get("record_type") == "general_procedure" else ""
    return (f"{gp}{label + ': ' if label else ''}{prod}"
            f"{f' ({y:g}%)' if y is not None else ''} · p. {pages} · {n_flags} flag(s)")
