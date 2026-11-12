"""Quantities as printed in papers: numbers, units, densities, times."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from gms.chem.names import ALIASES, clean, norm
from gms.chem.structure import canon

MASS_UNITS = {"g": 1.0, "mg": 1e-3, "kg": 1e3, "µg": 1e-6, "ng": 1e-9}
VOL_UNITS = {"ml": 1.0, "l": 1e3, "µl": 1e-3, "nl": 1e-6}          # -> mL
MOL_UNITS = {"mol": 1.0, "mmol": 1e-3, "µmol": 1e-6, "nmol": 1e-9, "kmol": 1e3}
EQUIV_UNITS = {"equiv", "equiv.", "eq", "eq.", "equivalent", "equivalents", "equiv(s)", "eqv", "mol%"}
UNIT_ALIASES = {"grams": "g", "gram": "g", "gr": "g", "milligrams": "mg", "milligram": "mg",
                 "kilograms": "kg", "micrograms": "µg", "ug": "µg", "mcg": "µg",
                 "milliliters": "ml", "millilitres": "ml", "milliliter": "ml", "millilitre": "ml",
                 "mls": "ml", "cm3": "ml", "cc": "ml", "liters": "l", "litres": "l", "liter": "l",
                 "litre": "l", "ul": "µl", "microliters": "µl", "microlitres": "µl",
                 "millimoles": "mmol", "millimole": "mmol", "mmole": "mmol", "mmols": "mmol",
                 "moles": "mol", "mole": "mol", "mols": "mol", "umol": "µmol", "micromoles": "µmol",
                 "mol-%": "mol%", "%mol": "mol%", "mole%": "mol%", "mol.%": "mol%", "mmol%": "mol%",
                 "equivs": "equiv", "equivalents": "equiv", "eq.": "equiv", "eq": "equiv"}

SOLVENT_DENSITY = {  # SMILES -> g/mL (20-25 degC)
    "O": 0.997, "CS(C)=O": 1.10, "ClCCl": 1.33, "CCOCC": 0.713, "CCCCCC": 0.655,
    "CCOC(C)=O": 0.902, "Cc1ccccc1": 0.867, "C1CCOC1": 0.889, "CC#N": 0.786, "CO": 0.792,
    "CN(C)C=O": 0.944, "CCN(CC)CC": 0.726, "CCN(C(C)C)C(C)C": 0.742, "OC=O": 1.22,
    "CCO": 0.789, "CC(C)O": 0.786, "CC(C)=O": 0.784, "ClC(Cl)Cl": 1.49, "ClCCCl": 1.25,
    "C1COCCO1": 1.03, "c1ccncc1": 0.982, "c1ccccc1": 0.876, "CCCCCCC": 0.684, "CCCCC": 0.626,
    "C1CCCCC1": 0.779, "CN(C)C(C)=O": 0.937, "CN1CCCC1=O": 1.03, "COC(C)(C)C": 0.740,
    "CC1CCCO1": 0.854, "CC(=O)O": 1.049, "OC(=O)C(F)(F)F": 1.49, "OCC(F)(F)F": 1.38,
    "OC(C(F)(F)F)C(F)(F)F": 1.596, "ClC(Cl)(Cl)Cl": 1.59, "Clc1ccccc1": 1.11, "Cc1ccccc1C": 0.88,
    "COCCOC": 0.868, "CC(C)(C)O": 0.775, "CCCCO": 0.81, "C[N+](=O)[O-]": 1.137,
    "C1CCNC1": 0.866, "C1CCNCC1": 0.862, "C1COCCN1": 1.0, "CC(=O)OC(C)=O": 1.08,
    "C1CCC2=NCCCN2CC1": 1.018, "CS(=O)(=O)Cl": 1.48, "BrBr": 3.10, "OO": 1.11,
    "Cl": 1.0, "OS(=O)(=O)O": 1.83, "B(F)(F)F.CCOCC": 1.15, "Cl[Ti](Cl)(Cl)Cl": 1.73,
    "OCc1ccccc1": 1.044, "O=Cc1ccccc1": 1.044, "Nc1ccccc1": 1.022,
}
DENSITY = {  # name -> g/mL, for names that do not resolve to a structure
    "hexanes": 0.655, "petroleum ether": 0.65, "petrol ether": 0.65, "pet. ether": 0.65,
    "ligroin": 0.656, "brine": 1.2, "sat. brine": 1.2, "saturated brine": 1.2,
    "xylenes": 0.86, "mixed xylenes": 0.86,
}

SUBS = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"
                      "\u2212\u2013\u2014\u00a0\u202f",
                      "0123456789---  ")
NUM_RE = r"[-+]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][-+]?\d+)?"


def to_float(tok: str) -> float:
    t = tok.replace(" ", "")
    if "," in t and "." not in t and re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", t) is None:
        t = t.replace(",", ".")                    # decimal comma: 1,5 -> 1.5
    return float(t.replace(",", ""))               # thousands separator: 1,250 -> 1250


def parse_qty(x) -> tuple[Optional[float], str]:
    """Number (and unit, if one is written next to it) from a model value.
    Handles 20, '20', '20 mg', '2 x 20 mL', '−78', '1,5', '85%', '20-25' (mean)."""
    if isinstance(x, bool) or x is None:
        return None, ""
    if isinstance(x, (int, float)):
        return (float(x), "") if x == x else (None, "")    # NaN -> None
    if isinstance(x, (list, tuple)):               # [20, 20] -> 40 (e.g. two portions)
        vals = [parse_qty(v) for v in x]
        nums = [v for v, _ in vals if v is not None]
        units = {u for _, u in vals if u}
        return (sum(nums), units.pop() if len(units) == 1 else "") if nums else (None, "")
    if isinstance(x, dict):
        return parse_qty(x.get("value", x.get("quantity")))[0], unit(x.get("unit"))
    t = str(x).translate(SUBS).strip()
    if not t:
        return None, ""
    m = re.match(rf"^~?\s*({NUM_RE})\s*[*\u00d7xX]\s*({NUM_RE})\s*(.*)$", t)
    if m:
        return to_float(m.group(1)) * to_float(m.group(2)), unit(m.group(3))
    m = re.match(rf"^~?\s*({NUM_RE})\s*(-|to|–)\s*({NUM_RE})\s*(.*)$", t)
    if m and (m.group(2) != "-" or not m.group(1).startswith("-")):   # ranges: take the mean
        return (to_float(m.group(1)) + to_float(m.group(3))) / 2, unit(m.group(4))
    m = re.match(rf"^[~<>≈≤≥]?\s*({NUM_RE})\s*(.*)$", t)
    if m:
        try:
            return to_float(m.group(1)), unit(m.group(2))
        except ValueError:
            return None, ""
    return None, ""


def num(x) -> Optional[float]:
    return parse_qty(x)[0]


def unit(u) -> str:
    u = str(u or "").strip().lower().replace("μ", "µ").replace("\u00b0", "")
    u = re.sub(r"\s+", "", u)                      # 'mol %' -> 'mol%'
    u = re.sub(r"^\((.*)\)$", r"\1", u)            # '(mmol)' -> 'mmol'
    if u in ("null", "none", "n/a", "-"):
        return ""
    return UNIT_ALIASES.get(u, u)


@lru_cache(maxsize=1)
def smiles_density() -> dict:
    return {c: v for k, v in SOLVENT_DENSITY.items() if (c := canon(k))}


def density(name: str, role: str, smiles: Optional[str] = None) -> Optional[float]:
    if role == "water":
        n = norm(clean(name))
        return DENSITY.get(n, 1.2 if "brine" in n else 1.0)
    if smiles and smiles in smiles_density():          # any name that resolves to a known liquid
        return smiles_density()[smiles]
    n = norm(clean(name))
    if n in DENSITY:
        return DENSITY[n]
    key = ALIASES.get(n)
    return smiles_density().get(canon(key)) if key else None


def sig(x: Optional[float], digits: int = 4) -> Optional[float]:
    """Round to significant figures, so 1.2e-6 mol of a catalyst does not become 0."""
    return None if x is None else float(f"{x:.{digits}g}")


def hours(x) -> Optional[float]:
    """'30 min' -> 0.5, '2 d' -> 48, '16 h' -> 16, 'overnight' -> 16, 3 -> 3 (already hours)."""
    if isinstance(x, str) and re.search(r"overnight|o/n", x, re.I):
        return 16.0
    v, u = parse_qty(x)
    if v is None:
        return None
    if u.startswith("min"):
        return v / 60
    if u.startswith(("s", "sec")):
        return v / 3600
    if u.startswith(("d", "day")):
        return v * 24
    if u.startswith(("w", "week")):
        return v * 168
    return v


def text(v) -> Optional[str]:
    if v is None or isinstance(v, (dict, list)):
        return None
    v = str(v).strip()
    return None if v.lower() in ("", "null", "none", "n/a") else v
