"""
pdf_extraction.py
-----------------
PDF -> Qwen2.5-VL (remote, OpenAI-compatible endpoint) -> normalized review table
-> app-ready tables (reactants, auxiliaries, product, water, Eco-Scale suggestions).

The VLM only *reads* the document. Everything numeric (MW, moles, carbons,
mL -> g, equivalents -> moles) is computed here, deterministically, with RDKit
and lookup tables. The user validates the result in the UI before it is used.
"""

from __future__ import annotations

import base64
import json
import re
from functools import lru_cache
from typing import Callable, Iterable, Optional
from urllib.parse import quote

import pandas as pd
import requests

from modules import eco_scale as es

PIPELINE_VERSION = "2026-10-07.2"
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

ROLES = ["reactant", "solvent", "base", "catalyst", "additive",
         "workup", "water", "product", "ignore"]
AUX_ROLES = {"solvent", "base", "catalyst", "additive", "workup"}

COLUMNS = ["Name", "Role", "Mass (g)", "Moles (mol)", "MW (g/mol)",
           "Carbons", "SMILES", "Page", "Flag"]

# ---------------------------------------------------------------------------
# 1. Prompt  (reaction-oriented schema, matches the calculator's inputs)
# ---------------------------------------------------------------------------

PROMPT = """You are an expert chemical-literature parser. This image is ONE page of a
paper or its supporting information. Extract ONLY the experimental details of the
reaction performed on this page (the quantities actually used and obtained).

Return ONLY valid JSON (no markdown, no commentary) in this schema:
{
  "components": [
    {"name": "chemical name or abbreviation exactly as written",
     "role": "reactant | solvent | base | catalyst | additive | workup | product | ignore",
     "quantity": <single number as printed, or null>,
     "unit": "g | mg | mL | uL | mmol | mol | equiv | mol% | null",
     "equivalents": <number or null>}
  ],
  "yield_percent": <number or null>,
  "conditions": {
    "temperature_c": <number or null>,
    "time_h": <number or null>,
    "atmosphere": "air | nitrogen | argon | null",
    "activation": "thermal | blue LED | microwave | ultrasound | null",
    "workup": ["extraction", "filtration", "chromatography", "distillation", "crystallization"]
  }
}

Rules:
- reactant = the substrate and stoichiometric reagents that are consumed.
- catalyst = sub-stoichiometric catalysts/photocatalysts/HAT catalysts. base/additive = bases, salts, acids.
- solvent = reaction solvent. workup = solvents, aqueous solutions, drying agents used in work-up/purification.
- product = the isolated product; give its isolated mass (or mmol) as quantity.
- Use "ignore" (or omit) for chromatography silica, cartridges, instruments, NMR standards, vendor/purity lines.
- "quantity" must be ONE literal number. Never write arithmetic: for "2 x 20 mL" write 40.
- Use only these roles: reactant, solvent, base, catalyst, additive, workup, product, ignore.
- Never invent numbers. If a value is not printed, use null.
- If the page has no experimental procedure, return {"components": [], "yield_percent": null, "conditions": {}}.
"""

# ---------------------------------------------------------------------------
# 2. PDF + VLM client
# ---------------------------------------------------------------------------

def _pymupdf():
    try:
        import pymupdf as m
    except ImportError:  # older installs
        import fitz as m
    return m


def page_count(pdf_bytes: bytes) -> int:
    with _pymupdf().open(stream=pdf_bytes, filetype="pdf") as d:
        return len(d)


def render_page(pdf_bytes: bytes, page_idx: int, dpi: int = 200) -> bytes:
    with _pymupdf().open(stream=pdf_bytes, filetype="pdf") as d:
        return d.load_page(page_idx).get_pixmap(dpi=dpi).tobytes("png")


def call_vlm(png: bytes, base_url: str, model: str = DEFAULT_MODEL,
             api_key: str = "EMPTY", prompt: str = PROMPT, max_tokens: int = 2048) -> str:
    """Send one page image to an OpenAI-compatible chat endpoint (vLLM, etc.)."""
    b64 = base64.b64encode(png).decode()
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=payload,
                      headers={"Authorization": f"Bearer {api_key}"}, timeout=300)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _fix_json(text: str) -> str:
    """Repair what small VLMs commonly get wrong: arithmetic in values, trailing commas, None."""
    t = text
    for _ in range(5):
        n = re.sub(r"(:\s*)(\d+(?:\.\d+)?)\s*[*\u00d7xX]\s*(\d+(?:\.\d+)?)(?=\s*[,}\]])",
                   lambda m: f"{m.group(1)}{float(m.group(2)) * float(m.group(3)):g}", t)
        n = re.sub(r"(:\s*)(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)(?=\s*[,}\]])",
                   lambda m: f"{m.group(1)}{float(m.group(2)) + float(m.group(3)):g}", n)
        if n == t:
            break
        t = n
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return re.sub(r":\s*(None|NaN)\b", ": null", t)


def parse_json_status(text: str) -> tuple[Optional[dict], str]:
    """Returns (data, status). Never silently loses a page: status explains what happened."""
    text = text or ""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        body = m.group(0)
    elif "{" in text:
        body = text[text.index("{"):]
    else:
        return None, "no JSON in answer"
    try:
        return json.loads(body), "ok"
    except json.JSONDecodeError:
        pass
    fixed = _fix_json(body)
    try:
        return json.loads(fixed), "repaired JSON"
    except json.JSONDecodeError:
        pass
    closers = [i for i, ch in enumerate(fixed) if ch == "}"][::-1][:60]
    for j in closers:                       # answer was cut off: keep the complete items
        for tail in ("]}", "]}}", "}"):
            try:
                return json.loads(fixed[:j + 1] + tail), "repaired (answer cut off, last items may be missing)"
            except json.JSONDecodeError:
                continue
    return None, "invalid JSON"


def parse_json(text: str) -> Optional[dict]:
    return parse_json_status(text)[0]


RETRY_SUFFIX = ("\n\nIMPORTANT: your previous answer was not valid JSON. Output strictly valid JSON only. "
                "Never write arithmetic such as 2 * 20; write the computed number (40).")


def extract_document(pdf_bytes: bytes, page_indices: Iterable[int], base_url: str,
                     model: str = DEFAULT_MODEL, api_key: str = "EMPTY",
                     on_progress: Optional[Callable[[int, int], None]] = None) -> list[dict]:
    """Returns [{'page', 'data', 'raw', 'status', 'n_components'}, ...] (page is 1-based)."""
    idxs = list(page_indices)
    out = []
    for i, p in enumerate(idxs, 1):
        png = render_page(pdf_bytes, p)
        raw = call_vlm(png, base_url, model, api_key)
        data, status = parse_json_status(raw)
        if data is None:  # retry with a stricter instruction (temperature is 0, so same prompt = same answer)
            raw2 = call_vlm(png, base_url, model, api_key, prompt=PROMPT + RETRY_SUFFIX, max_tokens=4096)
            data2, status2 = parse_json_status(raw2)
            if data2 is not None:
                raw, data, status = raw2, data2, status2 + " (after retry)"
        n = len(data.get("components") or []) if isinstance(data, dict) else 0
        if data is not None and n == 0:
            status = "no compounds on this page" + ("" if status == "ok" else f" ({status})")
        out.append({"page": p + 1, "data": data, "raw": raw, "status": status, "n_components": n})
        if on_progress:
            on_progress(i, len(idxs))
    return out

# ---------------------------------------------------------------------------
# 3. Chemistry resolution: alias table -> OPSIN -> PubChem, then RDKit
# ---------------------------------------------------------------------------

ALIASES = {
    "dmso": "CS(C)=O", "dimethyl sulfoxide": "CS(C)=O",
    "dmap": "CN(C)c1ccncc1", "4-dimethylaminopyridine": "CN(C)c1ccncc1",
    "dipea": "CCN(C(C)C)C(C)C", "etn(ipr)2": "CCN(C(C)C)C(C)C",
    "n,n-diisopropylethylamine": "CCN(C(C)C)C(C)C", "diisopropylethylamine": "CCN(C(C)C)C(C)C",
    "bz2o": "O=C(OC(=O)c1ccccc1)c1ccccc1", "benzoic anhydride": "O=C(OC(=O)c1ccccc1)c1ccccc1",
    "dcm": "ClCCl", "dichloromethane": "ClCCl",
    "thf": "C1CCOC1", "tetrahydrofuran": "C1CCOC1",
    "etoac": "CCOC(C)=O", "ethyl acetate": "CCOC(C)=O",
    "et2o": "CCOCC", "diethyl ether": "CCOCC",
    "hexane": "CCCCCC", "hexanes": "CCCCCC",
    "mecn": "CC#N", "acetonitrile": "CC#N", "meoh": "CO", "methanol": "CO",
    "dmf": "CN(C)C=O", "toluene": "Cc1ccccc1",
    "et3n": "CCN(CC)CC", "triethylamine": "CCN(CC)CC",
    "formic acid": "OC=O", "hco2h": "OC=O",
    "n,n-dimethylformamide": "CN(C)C=O", "dimethylformamide": "CN(C)C=O",
    "hobt": "On1nnc2ccccc21", "1-hydroxybenzotriazole": "On1nnc2ccccc21",
    "edc\u00b7hcl": "CCN=C=NCCCN(C)C.Cl", "edc.hcl": "CCN=C=NCCCN(C)C.Cl",
    "edc hcl": "CCN=C=NCCCN(C)C.Cl", "edc-hcl": "CCN=C=NCCCN(C)C.Cl",
    "edci": "CCN=C=NCCCN(C)C.Cl", "edc hydrochloride": "CCN=C=NCCCN(C)C.Cl",
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower()).strip()


_QUALIFIER = re.compile(
    r"\s*\((?:product|products|extraction|solvent|reagent|substrate|catalyst|base|crude|"
    r"anhydrous[^()]*|\d+[a-z]?|[^()]*%[^()]*|[^()]*\d{2,7}-\d{2}-\d[^()]*|"
    r"[^()]*(?:received|combi|sigma|aldrich|purified)[^()]*)\)\s*$", re.I)

_GENERIC = {"product", "products", "compound", "compounds", "substrate", "substrates",
            "reagent", "reagents", "solvent", "catalyst", "base", "additive", "crude product",
            "starting material", "mixture", "residue"}

_SYNONYMS = [(r"\bp-toluene", "4-toluene"), (r"toluenesulfonate", "methylbenzenesulfonate"),
             (r"toluenesulfonyl", "methylbenzenesulfonyl"), (r"toluenesulfonic", "methylbenzenesulfonic")]


def _clean(name: str) -> str:
    """Strip trailing qualifiers: '(product)', '(3a)', '(99.5%)', '(extraction)', CAS, vendor..."""
    n, prev = name.strip(), None
    while prev != n:
        prev = n
        n = _QUALIFIER.sub("", n).strip(" ;,")
    return re.sub(r"\s+", " ", n)


def _variants(name: str) -> list[str]:
    c = _clean(name)
    s = c
    for pat, rep in _SYNONYMS:
        s = re.sub(pat, rep, s, flags=re.I)
    return list(dict.fromkeys(x for x in (name, c, s) if x))


def _opsin(name: str) -> Optional[str]:
    try:
        from py2opsin import py2opsin  # pip install py2opsin (needs a Java runtime)
        s = py2opsin(name)
        return s or None
    except Exception:
        return None


def _pubchem(name: str) -> Optional[str]:
    try:
        url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
               f"{quote(name)}/property/CanonicalSMILES/JSON")
        r = requests.get(url, timeout=10)
        if r.ok:
            p = r.json()["PropertyTable"]["Properties"][0]
            return p.get("CanonicalSMILES") or p.get("ConnectivitySMILES") or p.get("SMILES")
    except Exception:
        pass
    return None


@lru_cache(maxsize=1024)
def resolve_smiles(name: str) -> Optional[str]:
    for cand in _variants(name):
        if not cand:
            continue
        key = _norm(cand)
        if key in ALIASES:
            return ALIASES[key]
        s = _opsin(cand) or _pubchem(cand)
        if s:
            return s
    return None


def _canon(smiles: Optional[str]) -> Optional[str]:
    if not smiles:
        return None
    from rdkit import Chem
    m = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(m) if m else None


def _mol_props(smiles: Optional[str]) -> tuple[Optional[float], Optional[int]]:
    """Average molecular weight (what MW in the calculator means) and carbon count."""
    if not smiles:
        return None, None
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    m = Chem.MolFromSmiles(smiles)
    if not m:
        return None, None
    return round(Descriptors.MolWt(m), 2), sum(1 for a in m.GetAtoms() if a.GetAtomicNum() == 6)

# ---------------------------------------------------------------------------
# 4. Units, densities, role heuristics
# ---------------------------------------------------------------------------

MASS_UNITS = {"g": 1.0, "mg": 1e-3, "kg": 1e3, "µg": 1e-6}
VOL_UNITS = {"ml": 1.0, "l": 1e3, "µl": 1e-3}          # -> mL
MOL_UNITS = {"mol": 1.0, "mmol": 1e-3, "µmol": 1e-6}
EQUIV_UNITS = {"equiv", "equiv.", "eq", "eq.", "equivalent", "equivalents", "mol%"}
_UNIT_ALIASES = {"grams": "g", "gram": "g", "milligrams": "mg", "milliliters": "ml",
                 "millimoles": "mmol", "moles": "mol", "ul": "µl", "ug": "µg", "umol": "µmol"}

DENSITY = {  # g/mL
    "dimethyl sulfoxide": 1.10, "dmso": 1.10, "dichloromethane": 1.33, "dcm": 1.33,
    "diethyl ether": 0.713, "et2o": 0.713, "hexanes": 0.655, "hexane": 0.655,
    "ethyl acetate": 0.902, "etoac": 0.902, "toluene": 0.867, "tetrahydrofuran": 0.889,
    "thf": 0.889, "acetonitrile": 0.786, "methanol": 0.792, "dmf": 0.944,
    "triethylamine": 0.726, "n,n-diisopropylethylamine": 0.742, "diisopropylethylamine": 0.742,
    "dipea": 0.742, "formic acid": 1.22,
    "n,n-dimethylformamide": 0.944, "dimethylformamide": 0.944,
}
_WATER_RE = re.compile(r"\b(water|brine|bicarbonate|aqueous|h2o)\b", re.I)
_IGNORE_RE = re.compile(r"\b(silica|cartridge|celite)\b", re.I)
_DRYING_RE = re.compile(r"\b(sodium sulfate|magnesium sulfate|na2so4|mgso4)\b", re.I)
_ROLE_MAP = {"reagent": "reactant", "substrate": "reactant", "starting material": "reactant",
             "reactant": "reactant", "solvent": "solvent", "eluent": "workup", "base": "base",
             "catalyst": "catalyst", "photocatalyst": "catalyst", "additive": "additive",
             "workup": "workup", "product": "product", "internal standard": "ignore",
             "ignore": "ignore", "byproduct": "ignore", "extraction": "workup", "drying agent": "workup",
             "chromatography": "workup", "quench": "workup", "purification": "workup",
             "work-up": "workup", "support": "ignore", "stationary phase": "ignore"}

_SUBS = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089", "0123456789")


def _num(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):  # None, lists like [8, 2], strings
        pass
    if isinstance(x, str):           # "2 x 20" -> 40
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[*\u00d7xX]\s*(\d+(?:\.\d+)?)\s*", x)
        if m:
            return float(m.group(1)) * float(m.group(2))
    return None


def _unit(u) -> str:
    u = str(u or "").strip().lower().replace("μ", "µ")
    return _UNIT_ALIASES.get(u, u)


def _role(raw, name: str) -> str:
    if _WATER_RE.search(name):
        return "water"
    if _IGNORE_RE.search(name):
        return "ignore"
    if _DRYING_RE.search(name):
        return "workup"
    return _ROLE_MAP.get(str(raw or "").lower().strip(), "additive")


@lru_cache(maxsize=1)
def _smiles_density() -> dict:
    out = {}
    for k, v in DENSITY.items():
        if k in ALIASES and _canon(ALIASES[k]):
            out[_canon(ALIASES[k])] = v
    return out


def _density(name: str, role: str, smiles: Optional[str] = None) -> Optional[float]:
    if role == "water":
        return 1.0
    if smiles and smiles in _smiles_density():          # any name that resolves to a known solvent
        return _smiles_density()[smiles]
    return DENSITY.get(_norm(_clean(name)))


# ---------------------------------------------------------------------------
# 5. Merge pages -> one validated review table
# ---------------------------------------------------------------------------

def build_review_table(pages: list[dict]) -> pd.DataFrame:
    rec: dict[str, dict] = {}
    for p in pages:
        for c in (p.get("data") or {}).get("components") or []:
            if not isinstance(c, dict) or not str(c.get("name") or "").strip():
                continue
            name = _clean(str(c["name"]).translate(_SUBS))
            if not name or _norm(name) in _GENERIC:
                continue
            role = _role(c.get("role"), name)
            qty, unit, equiv = _num(c.get("quantity")), _unit(c.get("unit")), _num(c.get("equivalents"))
            if unit == "mol%" and qty is not None:
                equiv, qty, unit = qty / 100, None, ""
            elif unit in EQUIV_UNITS and qty is not None:
                equiv, qty, unit = qty, None, ""
            if role in ("solvent", "workup", "water", "ignore") and qty is None:
                equiv = None
            known = unit in MASS_UNITS or unit in VOL_UNITS or unit in MOL_UNITS
            has_amt = (qty is not None and known) or equiv is not None

            smiles = None if role in ("ignore", "water") else _canon(resolve_smiles(name))
            key = (smiles or _norm(_clean(name))) + ("|workup" if role == "workup" else "")
            if key not in rec:
                rec[key] = dict(name=name, role=role, qty=qty, unit=unit, equiv=equiv,
                                smiles=smiles, pages={p["page"]}, flags=set(), has_amt=has_amt)
                continue
            r = rec[key]
            r["pages"].add(p["page"])
            if r["role"] == "ignore" and role != "ignore":
                r["role"] = role
            if not r["has_amt"] and has_amt:
                r.update(qty=qty, unit=unit, equiv=equiv, has_amt=True)
            elif has_amt and r["has_amt"] and qty is not None and r["qty"] is not None \
                    and (qty, unit) != (r["qty"], r["unit"]):
                r["flags"].add("different amounts on different pages")

    # pass 1: absolute amounts
    for r in rec.values():
        mass = vol = mol = None
        q, u = r["qty"], r["unit"]
        if q is not None:
            if u in MASS_UNITS:
                mass = q * MASS_UNITS[u]
            elif u in VOL_UNITS:
                vol = q * VOL_UNITS[u]
            elif u in MOL_UNITS:
                mol = q * MOL_UNITS[u]
        mw, carbons = _mol_props(r["smiles"])
        if vol is not None and mass is None:
            d = _density(r["name"], r["role"], r["smiles"])
            if d:
                mass = vol * d
                r["flags"].add("mass = volume x density")
            else:
                r["flags"].add("volume given, density unknown: enter mass")
        if mass is not None and mol is None and mw:
            mol = mass / mw
        elif mol is not None and mass is None and mw:
            mass = mol * mw
        r.update(mass=mass, mol=mol, mw=mw, carbons=carbons)

    # pass 2: equivalents -> moles, relative to the smallest-moles reactant
    lim = [r["mol"] for r in rec.values() if r["role"] == "reactant" and r["mol"]]
    lim_mol = min(lim) if lim else None
    for r in rec.values():
        if (r["equiv"] is not None and r["mol"] is None and lim_mol and r["mw"]
                and r["role"] in ("reactant", "catalyst", "base", "additive")):
            r["mol"] = r["equiv"] * lim_mol
            r["mass"] = r["mol"] * r["mw"]
            r["flags"].add("derived from equivalents")

    for r in rec.values():
        if r["role"] != "ignore" and r["qty"] is None and r["equiv"] is None:
            r["role"] = "ignore"
            r["flags"] = {"no amount stated on any page (set Role back to include)"}
    n_prod = sum(1 for r in rec.values() if r["role"] == "product")
    rows, seen_ignored = [], set()
    for r in rec.values():
        if r["role"] == "ignore":                      # show each ignored name only once
            if _norm(r["name"]) in seen_ignored:
                continue
            seen_ignored.add(_norm(r["name"]))
        if r["role"] in ("reactant", "product") and not r["mw"]:
            r["flags"].add("MW unknown: enter MW")
        if r["role"] != "ignore" and r["mass"] is None:
            r["flags"].add("mass missing")
        if r["role"] == "reactant" and r["mol"] is None:
            r["flags"].add("moles missing")
        if r["role"] == "catalyst" and r["equiv"] is not None and r["equiv"] >= 0.5:
            r["flags"].add("stoichiometric amount: check Role (reagent?)")
        if r["role"] == "product" and n_prod > 1:
            r["flags"].add("several products: keep one, set others to ignore")
        rows.append({
            "Name": r["name"], "Role": r["role"],
            "Mass (g)": None if r["mass"] is None else round(r["mass"], 4),
            "Moles (mol)": None if r["mol"] is None else round(r["mol"], 5),
            "MW (g/mol)": r["mw"], "Carbons": r["carbons"], "SMILES": r["smiles"],
            "Page": ", ".join(str(x) for x in sorted(r["pages"])),
            "Flag": "; ".join(sorted(r["flags"])),
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["_o"] = df["Role"].map(ROLES.index)
    return df.sort_values(["_o", "Name"]).drop(columns="_o").reset_index(drop=True)


def merge_meta(pages: list[dict]) -> dict:
    meta = {"yield_percent": None, "temperature_c": None, "time_h": None,
            "atmosphere": None, "activation": None, "workup": []}
    for p in pages:
        d = p.get("data") or {}
        if meta["yield_percent"] is None:
            meta["yield_percent"] = _num(d.get("yield_percent"))
        cond = d.get("conditions") or {}
        for k in ("temperature_c", "time_h"):
            if meta[k] is None:
                meta[k] = _num(cond.get(k))
        for k in ("atmosphere", "activation"):
            v = cond.get(k)
            if not meta[k] and v and str(v).lower() not in ("null", "none"):
                meta[k] = str(v)
        for w in cond.get("workup") or []:
            if str(w).lower() not in meta["workup"]:
                meta["workup"].append(str(w).lower())
    return meta

# ---------------------------------------------------------------------------
# 6. Review table -> calculator inputs
# ---------------------------------------------------------------------------

def _complete(df: pd.DataFrame) -> pd.DataFrame:
    """Fill MW / carbons / moles for rows the user added or edited by hand."""
    d = df.copy()
    for i, row in d.iterrows():
        if pd.isna(row.get("MW (g/mol)")) and str(row.get("Name") or "").strip() \
                and row.get("Role") not in ("water", "ignore"):
            smi = _canon(resolve_smiles(str(row["Name"])))
            mw, c = _mol_props(smi)
            if mw:
                d.at[i, "MW (g/mol)"], d.at[i, "Carbons"], d.at[i, "SMILES"] = mw, c, smi
        mw = d.at[i, "MW (g/mol)"]
        if pd.isna(d.at[i, "Moles (mol)"]) and pd.notna(d.at[i, "Mass (g)"]) and pd.notna(mw) and mw:
            d.at[i, "Moles (mol)"] = d.at[i, "Mass (g)"] / mw
    return d


def _active(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["Role"].isin(ROLES) & (df["Role"] != "ignore")]


def to_app_tables(df: pd.DataFrame) -> dict:
    d = _complete(_active(df))

    rx = d[d["Role"] == "reactant"]
    lim_idx = rx["Moles (mol)"].idxmin() if rx["Moles (mol)"].notna().any() else None
    reactants_df = pd.DataFrame({
        "Name": rx["Name"],
        "MW (g/mol)": rx["MW (g/mol)"].fillna(0.0),
        "Moles charged (mol)": rx["Moles (mol)"].fillna(0.0),
        "Mass charged (g)": rx["Mass (g)"].fillna(0.0),
        "Carbons/molecule": rx["Carbons"].fillna(0).round().astype(int),
        "Stoich. ratio (per limiting reagent)": 1.0,   # edit if the reaction is not 1:1
        "Limiting reagent?": rx.index == lim_idx,
    }).reset_index(drop=True)

    ax = d[d["Role"].isin(AUX_ROLES)]
    aux_df = pd.DataFrame({"Name": ax["Name"], "Mass (g)": ax["Mass (g)"].fillna(0.0)}).reset_index(drop=True)

    out = {"reactants_df": reactants_df, "aux_df": aux_df,
           "water_mass": float(d.loc[d["Role"] == "water", "Mass (g)"].fillna(0).sum())}

    pr = d[d["Role"] == "product"]
    if not pr.empty:
        p = pr.iloc[0]
        out["product"] = {
            "name": str(p["Name"]),
            "mw": float(p["MW (g/mol)"]) if pd.notna(p["MW (g/mol)"]) else 0.0,
            "actual_mass": float(p["Mass (g)"]) if pd.notna(p["Mass (g)"]) else 0.0,
            "moles": float(p["Moles (mol)"]) if pd.notna(p["Moles (mol)"]) else 0.0,
            "carbons": int(p["Carbons"]) if pd.notna(p["Carbons"]) else 0,
        }
    return out


def estimate_yield(df: pd.DataFrame) -> Optional[float]:
    """Product moles / limiting-reactant moles x 100 (assumes 1:1 stoichiometry)."""
    d = _complete(_active(df))
    pr = d[(d["Role"] == "product") & d["Moles (mol)"].notna()]
    rx = d[(d["Role"] == "reactant") & d["Moles (mol)"].notna()]
    if pr.empty or rx.empty or rx["Moles (mol)"].min() <= 0:
        return None
    return round(100 * pr.iloc[0]["Moles (mol)"] / rx["Moles (mol)"].min(), 1)


_WORKUP_RULES = [  # ordered highest penalty first
    ("chromatograph", "Classical chromatography"),
    ("distill", "Distillation"),
    ("sublim", "Sublimation"),
    ("extract", "Liquid-liquid extraction (incl. drying/filtration of desiccant)"),
    ("crystal", "Crystallization and filtration"),
    ("filtrat", "Simple filtration"),
]


def suggest_eco_inputs(meta: dict, yield_pct: Optional[float]) -> dict:
    """Best-guess Eco-Scale inputs. Price and safety are NOT in a paper: the user sets them."""
    t, h = meta.get("temperature_c"), meta.get("time_h")
    short = h is not None and h < 1
    if t is not None and t < 0:
        tt = "Cooling, < 0 degC"
    elif t is not None and t <= 5:
        tt = "Cooling to 0 degC"
    elif t is not None and t > 35:
        tt = "Heating, < 1 h" if short else "Heating, > 1 h"
    else:
        tt = "Room temperature, < 1 h" if short else "Room temperature, < 24 h"

    setups = []
    if re.search(r"nitrogen|argon|inert", meta.get("atmosphere") or "", re.I):
        setups.append("Inert gas atmosphere")
    if re.search(r"led|light|photo|microwave|ultrasound", meta.get("activation") or "", re.I):
        setups.append("Unconventional activation (microwave / ultrasound / photochemical)")

    workup_text = " ".join(meta.get("workup") or [])
    workup = next((v for k, v in _WORKUP_RULES if k in workup_text), "None")

    assert tt in es.TEMPERATURE_TIME_PENALTIES and workup in es.WORKUP_PENALTIES
    return {"yield_pct": float(min(100.0, max(0.0, yield_pct if yield_pct is not None else 90.0))),
            "temp_time": tt, "setups": setups or ["Common setup"], "workup": workup}
