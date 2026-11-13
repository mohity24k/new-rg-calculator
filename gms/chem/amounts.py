"""Fill in what can be computed for each component of a reaction: structure, molecular weight,
carbon count, mass from volume and density, moles from mass, amounts from equivalents.

Everything here is a deterministic conversion. Where a mass is derived (not typed in), the
component's `note` says how. Nothing here is an estimate: a missing amount stays missing.
"""

from __future__ import annotations

from typing import Optional

from gms.chem import names, units
from gms.chem.structure import canon, mol_props
from gms.model import STOICHIOMETRIC_ROLES, Component, Reaction

WATER_MW = 18.015
EQUIV_ROLES = ("reactant", "reagent", "catalyst", "base", "additive")


def _add_note(c: Component, text: str) -> None:
    if text not in c.note:
        c.note = f"{c.note}; {text}".strip("; ")


def limiting(reaction: Reaction) -> Optional[Component]:
    """The component marked as limiting, else the reactant with the fewest moles per coefficient."""
    stoich = reaction.by_role(*STOICHIOMETRIC_ROLES)
    marked = [c for c in stoich if c.limiting]
    if marked:
        return marked[0]
    usable = [c for c in reaction.by_role("reactant") if c.moles and c.coefficient > 0] or \
             [c for c in stoich if c.moles and c.coefficient > 0]
    return min(usable, key=lambda c: c.moles / c.coefficient) if usable else None


def complete(reaction: Reaction, online: Optional[bool] = None) -> list[str]:
    """Complete the components in place. Returns plain-language notes about what is missing."""
    issues = []
    for c in reaction.components:
        if c.role != "water" or c.smiles:
            c.smiles = canon(c.smiles) if c.smiles else names.resolve_smiles(c.name, online)
        if c.smiles:
            mw, carbons = mol_props(c.smiles)
            c.mw = c.mw or mw
            c.carbons = c.carbons if c.carbons is not None else carbons
        if c.mass_g is None and c.volume_ml is not None:
            d = units.density(c.name, "water" if c.role == "water" else c.role, c.smiles)
            if d:
                c.mass_g = c.volume_ml * d
                _add_note(c, f"mass = volume x density ({d:g} g/mL)")
            else:
                issues.append(f"'{c.name}': a volume is given but the density is not known. Enter the mass.")
        if c.mass_g is not None and c.moles is None and c.mw:
            c.moles = c.mass_g / c.mw
        elif c.moles is not None and c.mass_g is None and c.mw:
            c.mass_g = c.moles * c.mw
            _add_note(c, "mass = moles x MW")

    # equivalents -> moles. Reference = moles per 1 equivalent.
    refs = [c.moles / c.equiv for c in reaction.by_role("reactant") if c.moles and c.equiv and c.equiv > 0]
    lim = limiting(reaction)
    ref = min(refs) if refs else (lim.moles / lim.coefficient if lim and lim.moles else None)
    for c in reaction.by_role(*EQUIV_ROLES):
        if c.equiv is not None and c.moles is None and ref:
            c.moles = c.equiv * ref
            _add_note(c, "amount from equivalents")
            if c.mw:
                c.mass_g = c.moles * c.mw
    lim = limiting(reaction)
    if lim is not None:
        if not any(c.limiting for c in reaction.by_role(*STOICHIOMETRIC_ROLES)):
            lim.limiting = True
        if lim.moles:
            per_equiv = lim.moles / (lim.coefficient or 1.0)
            for c in reaction.by_role(*EQUIV_ROLES):
                if c.equiv is None and c.moles:
                    c.equiv = round(c.moles / per_equiv, 4)

    for c in reaction.by_role(*STOICHIOMETRIC_ROLES, "product"):
        if not c.mw:
            issues.append(f"'{c.name}': structure not recognised, so the molecular weight is unknown. "
                          "Enter a SMILES or the molecular weight.")
    for c in reaction.components:
        if c.mass_g is None:
            issues.append(f"'{c.name}' ({c.role.replace('_', ' ')}): no amount. It is not counted in the mass metrics.")
    return issues
