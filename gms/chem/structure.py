"""RDKit helpers: canonical SMILES, molecular weight, atom counts."""

from __future__ import annotations

from typing import Optional


def rdkit():
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")          # unparsable SMILES are handled, not logged
    return Chem


def canon(smiles: Optional[str]) -> Optional[str]:
    if not smiles or not isinstance(smiles, str):
        return None
    Chem = rdkit()
    m = Chem.MolFromSmiles(smiles.strip())
    return Chem.MolToSmiles(m) if m else None


def mol_props(smiles: Optional[str]) -> tuple[Optional[float], Optional[int]]:
    """Average molecular weight (what MW in the calculator means) and carbon count."""
    if not smiles or not isinstance(smiles, str):
        return None, None
    Chem = rdkit()
    from rdkit.Chem import Descriptors
    m = Chem.MolFromSmiles(smiles.strip())
    if not m:
        return None, None
    return round(Descriptors.MolWt(m), 2), sum(1 for a in m.GetAtoms() if a.GetAtomicNum() == 6)
