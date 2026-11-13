"""Stoichiometric coefficients from atom balance, instead of assuming 1 : 1.

infer_coefficients() finds the smallest whole-number coefficients of the reactants whose atoms
cover the heavy atoms of the product. It is a suggestion for the user to confirm, never applied
silently: by-products and reagents that donate atoms can make other answers correct.
"""

from __future__ import annotations

import itertools
from collections import Counter
from typing import Optional

from gms.chem.structure import rdkit


def heavy_atoms(smiles: Optional[str]) -> Optional[Counter]:
    if not smiles:
        return None
    m = rdkit().MolFromSmiles(smiles)
    if m is None:
        return None
    return Counter(a.GetSymbol() for a in m.GetAtoms() if a.GetAtomicNum() > 1)


def infer_coefficients(reactant_smiles: list[Optional[str]], product_smiles: Optional[str],
                       max_coefficient: int = 3, elements: tuple = ("C",)) -> Optional[dict]:
    """Smallest coefficients (1..max) such that the reactants supply at least the product's atoms
    of the given elements (carbon by default: heteroatoms often come from reagents or leave in
    by-products). Returns {'coefficients': [...], 'covered': bool} or None without structures."""
    prod = heavy_atoms(product_smiles)
    reac = [heavy_atoms(s) for s in reactant_smiles]
    if prod is None or not reac or any(r is None for r in reac):
        return None
    need = {e: prod.get(e, 0) for e in elements}
    best = None
    for coefs in sorted(itertools.product(range(1, max_coefficient + 1), repeat=len(reac)),
                        key=lambda c: (sum(c), c)):
        have = {e: sum(n * r.get(e, 0) for n, r in zip(coefs, reac)) for e in elements}
        if all(have[e] >= need[e] for e in elements):
            best = coefs
            break
    if best is None:
        return {"coefficients": [1] * len(reac), "covered": False}
    return {"coefficients": list(best), "covered": True}
