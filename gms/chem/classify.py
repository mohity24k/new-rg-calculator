"""Reaction class from the structures of the reactants and the product, with reagent names as
a weaker fallback. v1 covers amide coupling, Suzuki-Miyaura coupling and reductive amination.

classify() returns (class or None, confidence 'structure' | 'reagents' | None, reason)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from gms.chem.structure import rdkit
from gms.model import Reaction

CLASSES = {
    "amide_coupling": "Amide coupling",
    "suzuki_miyaura": "Suzuki-Miyaura coupling",
    "reductive_amination": "Reductive amination",
}

_SMARTS = {
    "acid": "[CX3](=O)[OX2H1,OX1-]",
    "amine": "[NX3;H2,H1;!$(N[C,S,P]=[O,S,N]);!$(N-[N,O]);!$(N=*)]",
    "amine_any": "[NX3;!$(N[C,S,P]=[O,S,N]);!$(N=*);!$([N]-[N,O])]",
    "amide": "[CX3](=O)[NX3]",
    "boron": "[#6][BX3]([OX2])[OX2]",
    "bf3k": "[#6][BX4-](F)(F)F",
    "halide": "[c,$([CX3]=[CX3])][Cl,Br,I]",
    "triflate": "[c,$([CX3]=[CX3])]OS(=O)(=O)C(F)(F)F",
    "carbonyl": "[CX3;!$(C(=O)[N,O,S,Cl,Br])](=[OX1])[#6]",
    "pd_ni": "[Pd,Ni]",
}

_COUPLING_REAGENTS = ("hatu", "hbtu", "tbtu", "pybop", "bop", "edc", "edci", "dcc", "dic", "t3p", "cdi",
                      "comu", "propylphosphonic anhydride", "carbonyldiimidazole", "carbodiimide")
_REDUCTANTS = ("nabh(oac)3", "triacetoxyborohydride", "stab", "nabh3cn", "cyanoborohydride", "nabh4",
               "sodium borohydride", "picoline borane", "picoline-borane", "pic-bh3", "h2", "hydrogen",
               "borane", "silane")
_PD = ("pd", "palladium")


@lru_cache(maxsize=None)
def _patt(key: str):
    return rdkit().MolFromSmarts(_SMARTS[key])


def _count(smiles: Optional[str], key: str) -> int:
    if not smiles:
        return 0
    m = rdkit().MolFromSmiles(smiles)
    return len(m.GetSubstructMatches(_patt(key))) if m else 0


def classify(reaction: Reaction) -> tuple[Optional[str], Optional[str], str]:
    reactants = [c for c in reaction.by_role("reactant", "reagent") if c.smiles]
    product = reaction.product
    names = " ".join(c.name.lower() for c in reaction.inputs)
    smiles_all = [c.smiles for c in reaction.inputs if c.smiles]
    has = lambda key: any(_count(c.smiles, key) for c in reactants)          # noqa: E731
    total = lambda key: sum(_count(c.smiles, key) for c in reactants)        # noqa: E731

    if product is not None and product.smiles and reactants:
        p = product.smiles
        if (has("boron") or has("bf3k")) and (has("halide") or has("triflate")) and \
                not _count(p, "boron") and not _count(p, "bf3k"):
            pd = any(_count(s, "pd_ni") for s in smiles_all) or "palladium" in names or "pd(" in names or "pd/" in names
            return ("suzuki_miyaura", "structure",
                    "A boronic acid or ester and an aryl or vinyl halide give a product without boron"
                    + ("" if pd else "; no palladium or nickel catalyst was found in the list") + ".")
        if has("acid") and has("amine") and _count(p, "amide") > total("amide"):
            return ("amide_coupling", "structure",
                    "A carboxylic acid and an amine give a product with a new amide bond.")
        if has("carbonyl") and has("amine") and _count(p, "carbonyl") < total("carbonyl") and \
                _count(p, "amine_any") and not _count(p, "amide") > total("amide"):
            red = any(k in names for k in _REDUCTANTS if len(k) > 3)
            return ("reductive_amination", "structure",
                    "An aldehyde or ketone and an amine give an amine product with one carbonyl group less"
                    + ("" if red else "; no reducing agent was recognised in the list") + ".")
    # fallback: reagent names only (whole words, so "dic" does not match "dichloroethane")
    words = set(re.split(r"[^a-z0-9()]+", names))
    word = lambda keys: any(k in words if " " not in k else k in names for k in keys)   # noqa: E731
    if word(_COUPLING_REAGENTS) and ("amine" in names or has("amine") or "acid" in names):
        return "amide_coupling", "reagents", "A coupling reagent is present (structures were not all recognised)."
    if "boronic" in names and (word(_PD) or "pd(" in names):
        return "suzuki_miyaura", "reagents", "A boronic acid and a palladium catalyst are present."
    if (word(_REDUCTANTS) or "borohydride" in names) and ("aldehyde" in names or has("carbonyl")) and \
            ("amine" in names or has("amine")):
        return "reductive_amination", "reagents", "A carbonyl compound, an amine and a hydride reductant are present."
    return None, None, "The reaction was not recognised as one of the supported classes."
