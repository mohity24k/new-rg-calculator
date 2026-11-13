"""Edits: small, explicit changes to a reaction record. The diagnosis ("what if this were
fixed?") and the suggestions (greener candidates) both build an edited copy of the reaction
and run the same calculator on it, so every reported gain is a computed number.

An edit is a dict with an "op" key:
    {"op": "scale_mass", "name": n, "factor": 0.5}
    {"op": "set_equiv", "name": n, "equiv": 1.1}
    {"op": "remove", "name": n}
    {"op": "remove_stage", "stage": "purification"}
    {"op": "add", "component": {...Component fields...}}
    {"op": "replace", "name": n, "with": {"name": ..., "smiles": ...}, "amount": "same_volume"
                                         | "same_moles" | "same_equiv" | {"equiv": x} | {"mol_percent": x}}
    {"op": "replace_step", "old": step, "new": step}       (EcoScale workup category)
    {"op": "set", "field": "temperature_c", "value": 25}   (a Reaction attribute)
"""

from __future__ import annotations

from typing import Optional

from gms.chem import amounts, solvents, units
from gms.chem.structure import canon, mol_props
from gms.model import Component, Reaction


class EditError(ValueError):
    """The edit cannot be applied to this reaction (for example an unknown density)."""


def _density(name: str, role: str, smiles: Optional[str]) -> Optional[float]:
    s = solvents.lookup(smiles)
    if s and s.density_g_ml:
        return s.density_g_ml
    return units.density(name, role, canon(smiles))


def _per_equiv(reaction: Reaction) -> Optional[float]:
    lim = amounts.limiting(reaction)
    return lim.moles / (lim.coefficient or 1.0) if lim and lim.moles else None


def _scale(c: Component, factor: float) -> None:
    for f in ("mass_g", "moles", "volume_ml", "equiv"):
        v = getattr(c, f)
        if v is not None:
            setattr(c, f, v * factor)


def _replace(r: Reaction, e: dict) -> None:
    old = r.find(e["name"])
    if old is None:
        raise EditError(f"'{e['name']}' is not in the reaction")
    w = e["with"]
    if w.get("parts"):                       # a solvent mixture: split the volume between the parts
        vol = old.volume_ml
        if vol is None:
            d_old = _density(old.name, old.role, old.smiles)
            if not d_old or old.mass_g is None:
                raise EditError(f"the volume of '{old.name}' is not known")
            vol = old.mass_g / d_old
        new_parts = []
        for part in w["parts"]:
            smi = canon(part["smiles"])
            d_new = _density(part["name"], old.role, smi)
            if not d_new:
                raise EditError(f"the density of '{part['name']}' is not known")
            mw, carbons = mol_props(smi)
            v = vol * part["fraction"]
            new_parts.append(Component(
                name=part["name"], role="water" if smi == "O" else old.role, smiles=smi, mw=mw, carbons=carbons,
                stage=old.stage, volume_ml=v, mass_g=v * d_new, moles=v * d_new / mw if mw else None,
                estimated=True, note=f"assumes the same total volume as {old.name}: {v:.3g} mL x {d_new:g} g/mL"
                                     + (f"; {w['parts_note']}" if w.get("parts_note") else "")))
        i = r.components.index(old)
        r.components[i:i + 1] = new_parts
        return
    smiles = canon(w.get("smiles"))
    mw, carbons = mol_props(smiles)
    new = Component(name=w["name"], role=w.get("role") or old.role, smiles=smiles, mw=mw, carbons=carbons,
                    stage=old.stage, coefficient=old.coefficient, estimated=True)
    amount = e.get("amount", "same_volume")
    if amount == "same_volume":
        vol = old.volume_ml
        if vol is None:
            d_old = _density(old.name, old.role, old.smiles)
            if not d_old or old.mass_g is None:
                raise EditError(f"the volume of '{old.name}' is not known")
            vol = old.mass_g / d_old
        d_new = _density(new.name, new.role, smiles)
        if not d_new:
            raise EditError(f"the density of '{new.name}' is not known")
        new.volume_ml, new.mass_g = vol, vol * d_new
        new.moles = new.mass_g / mw if mw else None
        new.note = f"assumes the same volume as {old.name} ({vol:.3g} mL x {d_new:g} g/mL)"
    else:
        if amount in ("same_moles", "same_equiv"):
            moles = old.moles
            label = f"assumes the same number of moles as {old.name}"
        else:
            per = _per_equiv(r)
            if per is None:
                raise EditError("the amount of the limiting reactant is not known")
            eq = amount["equiv"] if "equiv" in amount else amount["mol_percent"] / 100.0
            moles = eq * per
            label = f"assumes {eq:g} equivalents (from the cited procedure)"
        if moles is None or not mw:
            raise EditError(f"the amount of '{new.name}' cannot be computed")
        new.moles, new.mass_g, new.note = moles, moles * mw, label
        per = _per_equiv(r)
        new.equiv = round(moles / per, 4) if per else None
    r.components[r.components.index(old)] = new


def apply(reaction: Reaction, edit_list: list[dict]) -> Reaction:
    """A completed copy of the reaction with the edits applied. Raises EditError if one cannot be."""
    r = reaction.copy()
    for e in edit_list:
        op = e["op"]
        if op == "scale_mass":
            c = r.find(e["name"])
            if c is None:
                raise EditError(f"'{e['name']}' is not in the reaction")
            _scale(c, e["factor"])
        elif op == "set_equiv":
            c = r.find(e["name"])
            if c is None or not c.equiv:
                raise EditError(f"the equivalents of '{e['name']}' are not known")
            _scale(c, e["equiv"] / c.equiv)
        elif op == "remove":
            r.components = [c for c in r.components if c.name != e["name"]]
        elif op == "remove_stage":
            r.components = [c for c in r.components if c.stage != e["stage"] or c.role == "product"]
        elif op == "add":
            d = dict(e["component"])
            per = _per_equiv(r)
            smiles = canon(d.get("smiles"))
            mw, carbons = mol_props(smiles)
            c = Component(name=d["name"], role=d["role"], smiles=smiles, mw=mw, carbons=carbons,
                          equiv=d.get("equiv"), mass_g=d.get("mass_g"), volume_ml=d.get("volume_ml"),
                          estimated=True, note=d.get("note", "amount from the cited procedure"))
            if c.equiv is not None and c.mass_g is None:
                if per is None or not mw:
                    raise EditError(f"the amount of '{c.name}' cannot be computed")
                c.moles, c.mass_g = c.equiv * per, c.equiv * per * mw
            r.components.insert(max(0, len(r.components) - 1), c)
        elif op == "replace":
            _replace(r, e)
        elif op == "replace_step":
            if e["old"] not in r.workup_steps:
                raise EditError(f"the step '{e['old']}' is not in the workup")
            i = r.workup_steps.index(e["old"])
            r.workup_steps[i] = e["new"]
        elif op == "set":
            setattr(r, e["field"], e["value"])
        else:
            raise EditError(f"unknown edit '{op}'")
    amounts.complete(r, online=False)
    return r
