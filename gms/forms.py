"""Manual entry: table rows <-> reaction record, with validation of what the user typed.

This is the edge of the system for manual input, so the checks live here. The functions do not
depend on Streamlit and are tested directly.
"""

from __future__ import annotations

import math
from typing import Optional

from gms.chem import units
from gms.metrics import ecoscale as es
from gms.model import ROLES, Component, Reaction

UNITS = ["g", "mg", "kg", "mL", "µL", "L", "mmol", "mol", "equiv", "mol%"]
ROLE_CHOICES = [r.replace("_", " ") for r in ROLES]
COLUMNS = ["Name", "Role", "Amount", "Unit", "Limiting", "SMILES", "MW", "Coefficient", "Benign", "Estimated"]
ADVANCED = ["SMILES", "MW", "Coefficient", "Benign", "Estimated"]
MAX_ROWS = 60
MAX_NAME = 200
EMPTY_ROW = {"Name": "", "Role": "reactant", "Amount": None, "Unit": "g", "Limiting": False, "SMILES": "",
             "MW": None, "Coefficient": 1.0, "Benign": False, "Estimated": False}
ATMOSPHERES = ["air", "nitrogen", "argon", "glove box"]
ACTIVATIONS = ["thermal", "microwave", "ultrasound", "photochemical", "electrochemical"]
EXTRA_SETUPS = ["Instruments for controlled addition of chemicals", "Pressure equipment (> 1 atm)",
                "Additional special glassware"]
PRICE_NOT_ASSESSED = "Not assessed"


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


def _number(v) -> Optional[float]:
    if _missing(v):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def rows_from_reaction(reaction: Reaction) -> list[dict]:
    """Table rows that show each component with the amount it was given in."""
    rows = []
    for c in reaction.components:
        if c.mass_g is not None and not (c.volume_ml is not None and "volume x density" in c.note):
            amount, unit = c.mass_g, "g"
            if 0 < c.mass_g < 1:
                amount, unit = c.mass_g * 1000, "mg"
        elif c.volume_ml is not None:
            amount, unit = c.volume_ml, "mL"
        elif c.equiv is not None:
            amount, unit = c.equiv, "equiv"
        elif c.moles is not None:
            amount, unit = c.moles * 1000, "mmol"
        else:
            amount, unit = None, "g"
        rows.append({"Name": c.name, "Role": c.role.replace("_", " "),
                     "Amount": round(amount, 6) if amount is not None else None, "Unit": unit,
                     "Limiting": bool(c.limiting), "SMILES": c.smiles or "", "MW": None,
                     "Coefficient": float(c.coefficient), "Benign": bool(c.benign), "Estimated": bool(c.estimated)})
    return rows


def meta_from_reaction(reaction: Reaction) -> dict:
    return {"name": reaction.name, "reaction_class": reaction.reaction_class or "auto",
            "temperature_c": _number(reaction.temperature_c), "time_h": _number(reaction.time_h),
            "reflux": reaction.reflux,
            "atmosphere": reaction.atmosphere or "air", "activation": reaction.activation or "thermal",
            "extra_setups": list(reaction.extra_setups), "workup_steps": list(reaction.workup_steps),
            "reported_yield_percent": _number(reaction.reported_yield_percent),
            "price_category": reaction.price_category or PRICE_NOT_ASSESSED, "source": dict(reaction.source)}


def reaction_from_rows(rows: list[dict], meta: dict) -> tuple[Optional[Reaction], list[str]]:
    """(reaction, problems). The reaction is None if a problem makes scoring impossible."""
    problems, comps = [], []
    rows = [r for r in rows if not (_missing(r.get("Name")) and _number(r.get("Amount")) is None)]
    if len(rows) > MAX_ROWS:
        return None, [f"At most {MAX_ROWS} materials are supported; the table has {len(rows)}."]
    for i, r in enumerate(rows, 1):
        name = str(r.get("Name") or "").strip()
        if not name:
            problems.append(f"Row {i}: the name is missing.")
            continue
        if len(name) > MAX_NAME:
            problems.append(f"Row {i}: the name is longer than {MAX_NAME} characters.")
            continue
        role = str(r.get("Role") or "").strip().replace(" ", "_")
        if role not in ROLES:
            problems.append(f"'{name}': choose a role.")
            continue
        amount, unit = _number(r.get("Amount")), str(r.get("Unit") or "").strip()
        if amount is not None and amount < 0:
            problems.append(f"'{name}': the amount must not be negative.")
            continue
        if amount is not None and unit not in UNITS:
            problems.append(f"'{name}': choose a unit.")
            continue
        c = Component(name=name, role=role, limiting=bool(r.get("Limiting")) and role in ("reactant", "reagent"),
                      smiles=(str(r.get("SMILES")).strip() or None) if not _missing(r.get("SMILES")) else None,
                      mw=_number(r.get("MW")) or None, coefficient=_number(r.get("Coefficient")) or 1.0,
                      benign=bool(r.get("Benign")), estimated=bool(r.get("Estimated")))
        if c.estimated:
            c.note = "marked as an estimate by the user"
        if amount is not None and amount > 0:
            u = units.unit(unit)
            if u in units.MASS_UNITS:
                c.mass_g = amount * units.MASS_UNITS[u]
            elif u in units.VOL_UNITS:
                c.volume_ml = amount * units.VOL_UNITS[u]
            elif u in units.MOL_UNITS:
                c.moles = amount * units.MOL_UNITS[u]
            elif unit == "equiv":
                c.equiv = amount
            elif unit == "mol%":
                c.equiv = amount / 100.0
        comps.append(c)
    names = [c.name for c in comps]
    for n in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"'{n}' is in the table more than once. Give each row its own name "
                        "(for example 'ethyl acetate (eluent)').")
    n_prod = sum(c.role == "product" for c in comps)
    if n_prod != 1:
        problems.append("The table needs exactly one product row." if n_prod == 0 else
                        "The table has more than one product. Keep the main product; a by-product is not an input.")
    if not any(c.role == "reactant" for c in comps):
        problems.append("The table needs at least one reactant.")
    if sum(c.limiting for c in comps) > 1:
        problems.append("Tick only one limiting reactant.")
    if problems:
        return None, problems
    comps.sort(key=lambda c: c.role == "product")              # the product last
    steps = [s for s in meta.get("workup_steps") or [] if s in es.WORKUP_PENALTIES]
    price = meta.get("price_category")
    cls = meta.get("reaction_class")
    return Reaction(
        name=str(meta.get("name") or "").strip()[:MAX_NAME], components=comps,
        reported_yield_percent=_number(meta.get("reported_yield_percent")),
        temperature_c=_number(meta.get("temperature_c")), time_h=_number(meta.get("time_h")),
        reflux=bool(meta.get("reflux")),
        atmosphere=meta.get("atmosphere") if meta.get("atmosphere") in ATMOSPHERES else None,
        activation=meta.get("activation") if meta.get("activation") in ACTIVATIONS else None,
        extra_setups=[s for s in meta.get("extra_setups") or [] if s in EXTRA_SETUPS],
        workup_steps=steps, price_category=price if price in es.PRICE_PENALTIES else None,
        reaction_class=cls if cls not in (None, "", "auto", "other") else None,
        source=dict(meta.get("source") or {"entry": "manual"})), []
