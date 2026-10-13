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

import ast
import base64
import json
import re
import warnings
from functools import lru_cache
from typing import Callable, Iterable, Optional

import pandas as pd
import requests

from modules import eco_scale as es

PIPELINE_VERSION = "2026-10-12.1"
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


def _open(pdf_bytes: bytes):
    """Open a PDF from bytes with a clear error for corrupt or password-protected files."""
    try:
        d = _pymupdf().open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"not a readable PDF ({e})") from e
    if d.needs_pass and not d.authenticate(""):
        d.close()
        raise ValueError("the PDF is password-protected; remove the password and upload again")
    return d


def page_count(pdf_bytes: bytes) -> int:
    with _open(pdf_bytes) as d:
        return len(d)


MAX_IMAGE_SIDE = 2000  # px; large pages are scaled down so they stay within VLM input limits


def render_page(pdf_bytes: bytes, page_idx: int, dpi: int = 200) -> bytes:
    with _open(pdf_bytes) as d:
        page = d.load_page(page_idx)
        longest_in = max(page.rect.width, page.rect.height) / 72 or 1
        dpi = max(50, min(dpi, int(MAX_IMAGE_SIDE / longest_in)))
        return page.get_pixmap(dpi=dpi).tobytes("png")


def call_vlm(png: bytes, base_url: str, model: str = DEFAULT_MODEL,
             api_key: str = "EMPTY", prompt: str = PROMPT, max_tokens: int = 2048,
             timeout: float = 300) -> str:
    """Send one page image to an OpenAI-compatible chat endpoint (vLLM, Ollama, OpenAI, ...)."""
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
    url = base_url.strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key or 'EMPTY'}"}
    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if not r.ok:  # surface the server's own message (wrong model name, bad key, ...)
        raise requests.HTTPError(f"{r.status_code} from {url}: {r.text[:500]}", response=r)
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected response from {url}: {r.text[:500]}") from e
    if isinstance(content, list):  # some servers return a list of content parts
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return content or ""


def _fix_json(text: str) -> str:
    """Repair what small VLMs commonly get wrong: arithmetic in values, trailing commas, None,
    Python literals, comments."""
    t = text
    for _ in range(5):
        n = re.sub(r"(:\s*)(\d+(?:\.\d+)?)\s*[*\u00d7xX]\s*(\d+(?:\.\d+)?)(?=\s*[,}\]])",
                   lambda m: f"{m.group(1)}{float(m.group(2)) * float(m.group(3)):g}", t)
        n = re.sub(r"(:\s*)(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)(?=\s*[,}\]])",
                   lambda m: f"{m.group(1)}{float(m.group(2)) + float(m.group(3)):g}", n)
        if n == t:
            break
        t = n
    t = re.sub(r"(?m)^\s*//.*$", "", t)                   # // comment lines
    t = re.sub(r",\s*([}\]])", r"\1", t)
    t = re.sub(r":\s*(None|NaN|undefined)\b", ": null", t)
    t = re.sub(r":\s*True\b", ": true", t)
    return re.sub(r":\s*False\b", ": false", t)


def _as_dict(obj) -> Optional[dict]:
    """Normalize the shapes models actually return into {'components': [...], ...}."""
    if isinstance(obj, dict):
        if "components" not in obj:
            for k in ("compounds", "materials", "reagents", "chemicals"):
                if isinstance(obj.get(k), list):
                    obj = {**obj, "components": obj[k]}
                    break
        if not isinstance(obj.get("components"), list):
            obj = {**obj, "components": []}
        if not isinstance(obj.get("conditions"), dict):
            obj = {**obj, "conditions": {}}
        return obj
    if isinstance(obj, list):  # a bare list of components, or a list of reaction objects
        dicts = [x for x in obj if isinstance(x, dict)]
        if dicts and all("name" in x for x in dicts):
            return _as_dict({"components": dicts})
        merged = [_as_dict(x) for x in dicts]
        return _merge_objects([m for m in merged if m]) if merged else None
    return None


def _merge_objects(objs: list[dict]) -> dict:
    out = {"components": [], "yield_percent": None, "conditions": {}}
    for o in objs:
        out["components"] += o.get("components") or []
        if out["yield_percent"] is None:
            out["yield_percent"] = o.get("yield_percent")
        for k, v in (o.get("conditions") or {}).items():
            out["conditions"].setdefault(k, v)
    return out


def _loads(body: str):
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:                                                    # single quotes, Python literals
        return ast.literal_eval(body)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        raise json.JSONDecodeError("invalid", body, 0)


def parse_json_status(text: str) -> tuple[Optional[dict], str]:
    """Returns (data, status). Never silently loses a page: status explains what happened."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)   # reasoning models
    text = re.sub(r"```(?:json)?", "", text)
    starts = [i for i, ch in enumerate(text) if ch in "{["]
    if not starts:
        return None, "no JSON in answer"
    first = starts[0]
    end = max(text.rfind("}"), text.rfind("]"))
    body = text[first:end + 1] if end > first else text[first:]
    try:
        return _as_dict(_loads(body)), "ok"
    except json.JSONDecodeError:
        pass
    fixed = _fix_json(body)
    try:
        return _as_dict(_loads(fixed)), "repaired JSON"
    except json.JSONDecodeError:
        pass
    # several JSON objects in a row (one per reaction): decode each and merge them
    dec, objs, i = json.JSONDecoder(), [], 0
    src = _fix_json(text[first:])
    while i < len(src):
        j = src.find("{", i)
        if j < 0:
            break
        try:
            o, k = dec.raw_decode(src, j)
            if isinstance(o, dict):
                objs.append(o)
            i = k
        except json.JSONDecodeError:
            i = j + 1
    objs = [d for d in (_as_dict(o) for o in objs) if d and d["components"]]
    if objs:
        return _merge_objects(objs), "repaired (several JSON objects merged)"
    fixed = src
    closers = [i for i, ch in enumerate(fixed) if ch == "}"][::-1][:60]
    for j in closers:                       # answer was cut off: keep the complete items
        for tail in ("]}", "]}}", "}", "]"):
            try:
                d = _as_dict(json.loads(fixed[:j + 1] + tail))
            except json.JSONDecodeError:
                continue
            if d is not None:
                return d, "repaired (answer cut off, last items may be missing)"
    return None, "invalid JSON"


def parse_json(text: str) -> Optional[dict]:
    return parse_json_status(text)[0]


RETRY_SUFFIX = ("\n\nIMPORTANT: your previous answer was not valid JSON. Output strictly valid JSON only. "
                "Never write arithmetic such as 2 * 20; write the computed number (40).")


def extract_document(pdf_bytes: bytes, page_indices: Iterable[int], base_url: str,
                     model: str = DEFAULT_MODEL, api_key: str = "EMPTY",
                     on_progress: Optional[Callable[[int, int], None]] = None) -> list[dict]:
    """Returns [{'page', 'data', 'raw', 'status', 'n_components'}, ...] (page is 1-based).

    A failure on one page is recorded in its status instead of discarding the pages already
    read. If the server cannot be reached at all, the error is raised."""
    n_pages = page_count(pdf_bytes)
    idxs = [p for p in dict.fromkeys(int(i) for i in page_indices) if 0 <= p < n_pages]
    out, errors = [], []
    for i, p in enumerate(idxs, 1):
        try:
            png = render_page(pdf_bytes, p)
            raw = call_vlm(png, base_url, model, api_key)
            data, status = parse_json_status(raw)
            if data is None:  # retry with a stricter instruction (temperature is 0, so same prompt = same answer)
                raw2 = call_vlm(png, base_url, model, api_key, prompt=PROMPT + RETRY_SUFFIX, max_tokens=4096)
                data2, status2 = parse_json_status(raw2)
                if data2 is not None:
                    raw, data, status = raw2, data2, status2 + " (after retry)"
        except (requests.ConnectionError, requests.Timeout) as e:
            if not out:                    # nothing read yet: the server is not reachable
                raise
            raw, data, status = "", None, f"request failed: {e}"
            errors.append(e)
        except requests.HTTPError as e:
            if not out:                    # wrong URL / model / key: same for every page
                raise
            raw, data, status = "", None, f"server error: {e}"
            errors.append(e)
        except Exception as e:             # one bad page must not lose the others
            raw, data, status = "", None, f"page failed: {type(e).__name__}: {e}"
            errors.append(e)
        n = len(data.get("components") or []) if isinstance(data, dict) else 0
        if data is not None and n == 0:
            status = "no compounds on this page" + ("" if status == "ok" else f" ({status})")
        out.append({"page": p + 1, "data": data, "raw": raw, "status": status, "n_components": n})
        if on_progress:
            on_progress(i, len(idxs))
    if errors and len(errors) == len(out):
        raise errors[-1]
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
    # common solvents (so densities apply to any synonym that resolves to the same structure)
    "water": "O", "h2o": "O", "ethanol": "CCO", "etoh": "CCO",
    "isopropanol": "CC(C)O", "2-propanol": "CC(C)O", "ipa": "CC(C)O", "iproh": "CC(C)O",
    "acetone": "CC(C)=O", "chloroform": "ClC(Cl)Cl", "chcl3": "ClC(Cl)Cl",
    "1,2-dichloroethane": "ClCCCl", "dce": "ClCCCl", "1,4-dioxane": "C1COCCO1", "dioxane": "C1COCCO1",
    "pyridine": "c1ccncc1", "benzene": "c1ccccc1", "heptane": "CCCCCCC", "n-heptane": "CCCCCCC",
    "pentane": "CCCCC", "n-pentane": "CCCCC", "n-hexane": "CCCCCC", "cyclohexane": "C1CCCCC1",
    "petroleum ether": "CCCCCC",
    "dma": "CN(C)C(C)=O", "dmac": "CN(C)C(C)=O", "n,n-dimethylacetamide": "CN(C)C(C)=O",
    "nmp": "CN1CCCC1=O", "n-methyl-2-pyrrolidone": "CN1CCCC1=O",
    "mtbe": "COC(C)(C)C", "methyl tert-butyl ether": "COC(C)(C)C",
    "2-methf": "CC1CCCO1", "2-methyltetrahydrofuran": "CC1CCCO1",
    "acetic acid": "CC(=O)O", "acoh": "CC(=O)O", "hoac": "CC(=O)O",
    "tfa": "OC(=O)C(F)(F)F", "trifluoroacetic acid": "OC(=O)C(F)(F)F",
    "tfe": "OCC(F)(F)F", "2,2,2-trifluoroethanol": "OCC(F)(F)F",
    "hfip": "OC(C(F)(F)F)C(F)(F)F", "hexafluoroisopropanol": "OC(C(F)(F)F)C(F)(F)F",
    "carbon tetrachloride": "ClC(Cl)(Cl)Cl", "ccl4": "ClC(Cl)(Cl)Cl",
    "chlorobenzene": "Clc1ccccc1", "phcl": "Clc1ccccc1", "xylene": "Cc1ccccc1C", "xylenes": "Cc1ccccc1C",
    "o-xylene": "Cc1ccccc1C", "dme": "COCCOC", "1,2-dimethoxyethane": "COCCOC",
    "t-buoh": "CC(C)(C)O", "tert-butanol": "CC(C)(C)O", "n-butanol": "CCCCO", "1-butanol": "CCCCO",
    "nitromethane": "C[N+](=O)[O-]", "meno2": "C[N+](=O)[O-]",
    "pyrrolidine": "C1CCNC1", "piperidine": "C1CCNCC1", "morpholine": "C1COCCN1",
    # common inorganic / organometallic reagents (no IUPAC name, not parsable by OPSIN)
    "nahco3": "[Na+].OC([O-])=O", "sodium bicarbonate": "[Na+].OC([O-])=O",
    "sodium hydrogen carbonate": "[Na+].OC([O-])=O",
    "na2co3": "[Na+].[Na+].[O-]C([O-])=O", "sodium carbonate": "[Na+].[Na+].[O-]C([O-])=O",
    "k2co3": "[K+].[K+].[O-]C([O-])=O", "potassium carbonate": "[K+].[K+].[O-]C([O-])=O",
    "cs2co3": "[Cs+].[Cs+].[O-]C([O-])=O", "cesium carbonate": "[Cs+].[Cs+].[O-]C([O-])=O",
    "caesium carbonate": "[Cs+].[Cs+].[O-]C([O-])=O",
    "k3po4": "[K+].[K+].[K+].[O-]P([O-])([O-])=O", "potassium phosphate": "[K+].[K+].[K+].[O-]P([O-])([O-])=O",
    "naoh": "[Na+].[OH-]", "sodium hydroxide": "[Na+].[OH-]", "koh": "[K+].[OH-]",
    "potassium hydroxide": "[K+].[OH-]", "lioh": "[Li+].[OH-]", "lithium hydroxide": "[Li+].[OH-]",
    "nah": "[Na+].[H-]", "sodium hydride": "[Na+].[H-]",
    "kotbu": "CC(C)(C)[O-].[K+]", "t-buok": "CC(C)(C)[O-].[K+]", "potassium tert-butoxide": "CC(C)(C)[O-].[K+]",
    "naotbu": "CC(C)(C)[O-].[Na+]", "sodium tert-butoxide": "CC(C)(C)[O-].[Na+]",
    "hcl": "Cl", "hydrochloric acid": "Cl", "hbr": "Br", "h2so4": "OS(=O)(=O)O", "sulfuric acid": "OS(=O)(=O)O",
    "nacl": "[Na+].[Cl-]", "sodium chloride": "[Na+].[Cl-]", "nh4cl": "[NH4+].[Cl-]",
    "ammonium chloride": "[NH4+].[Cl-]", "licl": "[Li+].[Cl-]", "lithium chloride": "[Li+].[Cl-]",
    "na2so4": "[Na+].[Na+].[O-]S([O-])(=O)=O", "sodium sulfate": "[Na+].[Na+].[O-]S([O-])(=O)=O",
    "mgso4": "[Mg+2].[O-]S([O-])(=O)=O", "magnesium sulfate": "[Mg+2].[O-]S([O-])(=O)=O",
    "na2s2o3": "[Na+].[Na+].[O-]S([O-])(=O)=S", "sodium thiosulfate": "[Na+].[Na+].[O-]S([O-])(=O)=S",
    "nabh4": "[Na+].[BH4-]", "sodium borohydride": "[Na+].[BH4-]",
    "lialh4": "[Li+].[AlH4-]", "lithium aluminium hydride": "[Li+].[AlH4-]",
    "lithium aluminum hydride": "[Li+].[AlH4-]",
    "nabh(oac)3": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]", "stab": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]",
    "sodium triacetoxyborohydride": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]",
    "pd(oac)2": "CC(=O)[O-].CC(=O)[O-].[Pd+2]", "palladium(ii) acetate": "CC(=O)[O-].CC(=O)[O-].[Pd+2]",
    "palladium acetate": "CC(=O)[O-].CC(=O)[O-].[Pd+2]",
    "pd(pph3)4": "c1ccc(P(c2ccccc2)c2ccccc2)cc1.c1ccc(P(c2ccccc2)c2ccccc2)cc1."
                 "c1ccc(P(c2ccccc2)c2ccccc2)cc1.c1ccc(P(c2ccccc2)c2ccccc2)cc1.[Pd]",
    "pdcl2": "Cl[Pd]Cl", "pd/c": "[Pd]", "pd(dppf)cl2": "Cl[Pd]Cl.[Fe].c1ccc(P(c2ccccc2)[c-]2cccc2)cc1."
                                                        "c1ccc(P(c2ccccc2)[c-]2cccc2)cc1",
    "pph3": "c1ccc(P(c2ccccc2)c2ccccc2)cc1", "triphenylphosphine": "c1ccc(P(c2ccccc2)c2ccccc2)cc1",
    "cui": "[Cu]I", "copper(i) iodide": "[Cu]I", "cucl": "[Cu]Cl", "cubr": "[Cu]Br",
    "cu(oac)2": "CC(=O)[O-].CC(=O)[O-].[Cu+2]", "cuso4": "[Cu+2].[O-]S([O-])(=O)=O",
    "zncl2": "Cl[Zn]Cl", "fecl3": "Cl[Fe](Cl)Cl", "alcl3": "Cl[Al](Cl)Cl", "ticl4": "Cl[Ti](Cl)(Cl)Cl",
    "bf3·oet2": "B(F)(F)F.CCOCC", "bf3.oet2": "B(F)(F)F.CCOCC", "bf3 oet2": "B(F)(F)F.CCOCC",
    "nbs": "O=C1CCC(=O)N1Br", "n-bromosuccinimide": "O=C1CCC(=O)N1Br",
    "nis": "O=C1CCC(=O)N1I", "ncs": "O=C1CCC(=O)N1Cl", "i2": "II", "iodine": "II", "br2": "BrBr",
    "mcpba": "O=C(OO)c1cccc(Cl)c1", "m-cpba": "O=C(OO)c1cccc(Cl)c1",
    "dbu": "C1CCC2=NCCCN2CC1", "dabco": "C1CN2CCN1CC2", "tbab": "CCCC[N+](CCCC)(CCCC)CCCC.[Br-]",
    "tbaf": "CCCC[N+](CCCC)(CCCC)CCCC.[F-]",
    "boc2o": "CC(C)(C)OC(=O)OC(=O)OC(C)(C)C", "di-tert-butyl dicarbonate": "CC(C)(C)OC(=O)OC(=O)OC(C)(C)C",
    "ac2o": "CC(=O)OC(C)=O", "acetic anhydride": "CC(=O)OC(C)=O",
    "tscl": "Cc1ccc(S(=O)(=O)Cl)cc1", "mscl": "CS(=O)(=O)Cl",
    "dcc": "C1CCC(N=C=NC2CCCCC2)CC1", "hatu": "CN(C)C(=[N+](C)C)On1nnc2cccnc21.F[P-](F)(F)(F)(F)F",
    "tempo": "CC1(C)CCCC(C)(C)N1[O]", "h2o2": "OO", "hydrogen peroxide": "OO",
    "aibn": "CC(C)(C#N)N=NC(C)(C)C#N",
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
        with warnings.catch_warnings():         # OPSIN warns on every name it cannot parse
            warnings.simplefilter("ignore")
            s = py2opsin(name)
        return s or None
    except Exception:
        return None


class _LookupFailed(Exception):
    """Transient failure (network, rate limit): the result must not be cached."""


def _pubchem(name: str) -> Optional[str]:
    # POST keeps names with '/', '#', '?' or ',' intact (they break a URL path).
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/property/"
           "SMILES,ConnectivitySMILES,CanonicalSMILES/JSON")
    try:
        r = requests.post(url, data={"name": name}, timeout=10)
    except requests.RequestException as e:
        raise _LookupFailed(str(e)) from e
    if r.status_code in (400, 404):             # PubChem does not know this name
        return None
    if not r.ok:
        raise _LookupFailed(f"HTTP {r.status_code}")
    try:
        p = r.json()["PropertyTable"]["Properties"][0]
    except (ValueError, KeyError, IndexError):
        return None
    return p.get("SMILES") or p.get("CanonicalSMILES") or p.get("ConnectivitySMILES")


_SMILES_CACHE: dict[str, Optional[str]] = {}


def resolve_smiles(name: str) -> Optional[str]:
    """Name -> SMILES via the alias table, OPSIN, then PubChem. Results are cached, except
    lookups that failed for a transient reason (so they are retried on the next run)."""
    name = str(name or "").strip()
    if not name:
        return None
    if name in _SMILES_CACHE:
        return _SMILES_CACHE[name]
    transient = False
    result = None
    for cand in _variants(name):
        key = _norm(cand)
        if key in ALIASES:
            result = ALIASES[key]
            break
    if result is None:
        for cand in _variants(name):
            result = _opsin(cand)
            if result:
                break
            try:
                result = _pubchem(cand)
            except _LookupFailed:
                transient = True
            if result:
                break
    if result or not transient:
        _SMILES_CACHE[name] = result
    return result


def _rdkit():
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")          # unparsable SMILES are handled, not logged
    return Chem


def _canon(smiles: Optional[str]) -> Optional[str]:
    if not smiles or not isinstance(smiles, str):
        return None
    Chem = _rdkit()
    m = Chem.MolFromSmiles(smiles.strip())
    return Chem.MolToSmiles(m) if m else None


def _mol_props(smiles: Optional[str]) -> tuple[Optional[float], Optional[int]]:
    """Average molecular weight (what MW in the calculator means) and carbon count."""
    if not smiles or not isinstance(smiles, str):
        return None, None
    Chem = _rdkit()
    from rdkit.Chem import Descriptors
    m = Chem.MolFromSmiles(smiles.strip())
    if not m:
        return None, None
    return round(Descriptors.MolWt(m), 2), sum(1 for a in m.GetAtoms() if a.GetAtomicNum() == 6)

# ---------------------------------------------------------------------------
# 4. Units, densities, role heuristics
# ---------------------------------------------------------------------------

MASS_UNITS = {"g": 1.0, "mg": 1e-3, "kg": 1e3, "µg": 1e-6, "ng": 1e-9}
VOL_UNITS = {"ml": 1.0, "l": 1e3, "µl": 1e-3, "nl": 1e-6}          # -> mL
MOL_UNITS = {"mol": 1.0, "mmol": 1e-3, "µmol": 1e-6, "nmol": 1e-9, "kmol": 1e3}
EQUIV_UNITS = {"equiv", "equiv.", "eq", "eq.", "equivalent", "equivalents", "equiv(s)", "eqv", "mol%"}
_UNIT_ALIASES = {"grams": "g", "gram": "g", "gr": "g", "milligrams": "mg", "milligram": "mg",
                 "kilograms": "kg", "micrograms": "µg", "ug": "µg", "mcg": "µg",
                 "milliliters": "ml", "millilitres": "ml", "milliliter": "ml", "millilitre": "ml",
                 "mls": "ml", "cm3": "ml", "cc": "ml", "liters": "l", "litres": "l", "liter": "l",
                 "litre": "l", "ul": "µl", "microliters": "µl", "microlitres": "µl",
                 "millimoles": "mmol", "millimole": "mmol", "mmole": "mmol", "mmols": "mmol",
                 "moles": "mol", "mole": "mol", "mols": "mol", "umol": "µmol", "micromoles": "µmol",
                 "mol-%": "mol%", "%mol": "mol%", "mole%": "mol%", "mol.%": "mol%", "mmol%": "mol%",
                 "equivs": "equiv", "equivalents": "equiv", "eq.": "equiv", "eq": "equiv"}

_SOLVENT_DENSITY = {  # SMILES -> g/mL (20-25 degC)
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
_WATER_RE = re.compile(r"\b(water|brine|h2o|d2o|di water|deionized water|distilled water)\b", re.I)
_AQUEOUS_RE = re.compile(r"\b(aqueous|aq\.?|sat(urated|d)?\.?)(?=\W|$)", re.I)
_IGNORE_RE = re.compile(r"\b(silica|silica gel|cartridge|celite|tlc|sand|cotton|filter paper)\b", re.I)
_DRYING_RE = re.compile(r"\b(sodium sulfate|magnesium sulfate|na2so4|mgso4|calcium chloride|cacl2)\b", re.I)
_ROLE_MAP = {"reagent": "reactant", "substrate": "reactant", "starting material": "reactant",
             "reactant": "reactant", "limiting reagent": "reactant", "oxidant": "reactant",
             "reductant": "reactant", "reducing agent": "reactant", "oxidizing agent": "reactant",
             "coupling partner": "reactant", "nucleophile": "reactant", "electrophile": "reactant",
             "solvent": "solvent", "co-solvent": "solvent", "cosolvent": "solvent",
             "reaction solvent": "solvent", "eluent": "workup", "base": "base",
             "catalyst": "catalyst", "photocatalyst": "catalyst", "co-catalyst": "catalyst",
             "cocatalyst": "catalyst", "precatalyst": "catalyst", "ligand": "catalyst",
             "organocatalyst": "catalyst", "hat catalyst": "catalyst", "initiator": "catalyst",
             "additive": "additive", "acid": "additive", "salt": "additive", "promoter": "additive",
             "activator": "additive", "electrolyte": "additive", "desiccant": "additive",
             "workup": "workup", "product": "product", "main product": "product",
             "isolated product": "product", "internal standard": "ignore", "standard": "ignore",
             "ignore": "ignore", "byproduct": "ignore", "by-product": "ignore", "side product": "ignore",
             "extraction": "workup", "drying agent": "workup", "chromatography": "workup",
             "quench": "workup", "purification": "workup", "wash": "workup", "work-up": "workup",
             "work up": "workup", "support": "ignore", "stationary phase": "ignore", "water": "water"}

_SUBS = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089"
                      "\u2212\u2013\u2014\u00a0\u202f",
                      "0123456789---  ")
_NUM_RE = r"[-+]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][-+]?\d+)?"


def _to_float(tok: str) -> float:
    t = tok.replace(" ", "")
    if "," in t and "." not in t and re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", t) is None:
        t = t.replace(",", ".")                    # decimal comma: 1,5 -> 1.5
    return float(t.replace(",", ""))               # thousands separator: 1,250 -> 1250


def _parse_qty(x) -> tuple[Optional[float], str]:
    """Number (and unit, if one is written next to it) from a model value.
    Handles 20, '20', '20 mg', '2 x 20 mL', '−78', '1,5', '85%', '20-25' (mean)."""
    if isinstance(x, bool) or x is None:
        return None, ""
    if isinstance(x, (int, float)):
        return (float(x), "") if x == x else (None, "")    # NaN -> None
    if isinstance(x, (list, tuple)):               # [20, 20] -> 40 (e.g. two portions)
        vals = [_parse_qty(v) for v in x]
        nums = [v for v, _ in vals if v is not None]
        units = {u for _, u in vals if u}
        return (sum(nums), units.pop() if len(units) == 1 else "") if nums else (None, "")
    if isinstance(x, dict):
        return _parse_qty(x.get("value", x.get("quantity")))[0], _unit(x.get("unit"))
    t = str(x).translate(_SUBS).strip()
    if not t:
        return None, ""
    m = re.match(rf"^~?\s*({_NUM_RE})\s*[*\u00d7xX]\s*({_NUM_RE})\s*(.*)$", t)
    if m:
        return _to_float(m.group(1)) * _to_float(m.group(2)), _unit(m.group(3))
    m = re.match(rf"^~?\s*({_NUM_RE})\s*(-|to|–)\s*({_NUM_RE})\s*(.*)$", t)
    if m and (m.group(2) != "-" or not m.group(1).startswith("-")):   # ranges: take the mean
        return (_to_float(m.group(1)) + _to_float(m.group(3))) / 2, _unit(m.group(4))
    m = re.match(rf"^[~<>≈≤≥]?\s*({_NUM_RE})\s*(.*)$", t)
    if m:
        try:
            return _to_float(m.group(1)), _unit(m.group(2))
        except ValueError:
            return None, ""
    return None, ""


def _num(x) -> Optional[float]:
    return _parse_qty(x)[0]


def _unit(u) -> str:
    u = str(u or "").strip().lower().replace("μ", "µ").replace("\u00b0", "")
    u = re.sub(r"\s+", "", u)                      # 'mol %' -> 'mol%'
    u = re.sub(r"^\((.*)\)$", r"\1", u)            # '(mmol)' -> 'mmol'
    if u in ("null", "none", "n/a", "-"):
        return ""
    return _UNIT_ALIASES.get(u, u)


def _role(raw, name: str, unit: str = "") -> str:
    raw_role = re.sub(r"\s+", " ", str(raw or "").lower().strip())
    if _WATER_RE.search(name):
        return "water"
    if _AQUEOUS_RE.search(name) and (raw_role in ("workup", "solvent", "water", "quench", "wash",
                                                  "extraction", "work-up") or unit in VOL_UNITS):
        return "water"                              # aqueous wash / quench solutions
    if _IGNORE_RE.search(name):
        return "ignore"
    if _DRYING_RE.search(name):
        return "workup"
    if raw_role in _ROLE_MAP:
        return _ROLE_MAP[raw_role]
    for k, v in _ROLE_MAP.items():                  # 'photoredox catalyst', 'organic base', ...
        if re.search(rf"\b{re.escape(k)}\b", raw_role):
            return v
    return "additive"


@lru_cache(maxsize=1)
def _smiles_density() -> dict:
    return {c: v for k, v in _SOLVENT_DENSITY.items() if (c := _canon(k))}


def _density(name: str, role: str, smiles: Optional[str] = None) -> Optional[float]:
    if role == "water":
        n = _norm(_clean(name))
        return DENSITY.get(n, 1.2 if "brine" in n else 1.0)
    if smiles and smiles in _smiles_density():          # any name that resolves to a known liquid
        return _smiles_density()[smiles]
    n = _norm(_clean(name))
    if n in DENSITY:
        return DENSITY[n]
    key = ALIASES.get(n)
    return _smiles_density().get(_canon(key)) if key else None


# ---------------------------------------------------------------------------
# 5. Merge pages -> one validated review table
# ---------------------------------------------------------------------------

NUMERIC_COLUMNS = ["Mass (g)", "Moles (mol)", "MW (g/mol)", "Carbons"]
_SUMMABLE_ROLES = {"solvent", "workup", "water"}   # e.g. "extract with EtOAc (3 x 20 mL), wash with EtOAc"


def _sig(x: Optional[float], digits: int = 4) -> Optional[float]:
    """Round to significant figures, so 1.2e-6 mol of a catalyst does not become 0."""
    return None if x is None else float(f"{x:.{digits}g}")


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric columns as float, text columns as str (Streamlit editors need stable dtypes)."""
    d = df.copy()
    for c in NUMERIC_COLUMNS:
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    for c in ("Name", "Role", "SMILES", "Page", "Flag"):
        if c in d:
            d[c] = d[c].astype("object").where(d[c].notna(), None)
    return d


def _component(c, page: int) -> Optional[dict]:
    """One model component -> normalized record, or None if it carries nothing usable."""
    if not isinstance(c, dict):
        return None
    name = _clean(str(c.get("name") or "").translate(_SUBS))
    if not name or _norm(name) in _GENERIC:
        return None
    qty, unit_in_qty = _parse_qty(c.get("quantity", c.get("amount")))
    unit = _unit(c.get("unit")) or unit_in_qty
    equiv = _num(c.get("equivalents", c.get("equiv")))
    role = _role(c.get("role"), name, unit)
    if unit == "%" and role in ("catalyst", "base", "additive", "reactant"):
        unit = "mol%"                               # '5 %' next to a catalyst means mol%
    if unit == "mol%" and qty is not None:
        equiv, qty, unit = qty / 100, None, ""
    elif unit in EQUIV_UNITS and qty is not None:
        equiv, qty, unit = qty, None, ""
    if role in ("solvent", "workup", "water", "ignore") and qty is None:
        equiv = None
    known = unit in MASS_UNITS or unit in VOL_UNITS or unit in MOL_UNITS
    has_amt = (qty is not None and known) or equiv is not None
    smiles = None if role in ("ignore", "water") else _canon(resolve_smiles(name))
    return dict(name=name, role=role, qty=qty, unit=unit, equiv=equiv, smiles=smiles,
                pages={page}, flags=set(), has_amt=has_amt)


def _key(r: dict) -> str:
    return (r["smiles"] or _norm(r["name"])) + ("|workup" if r["role"] == "workup" else "")


def _in_mol(r: dict) -> Optional[float]:
    """Amount of a record in mol (via MW and density where needed), or None."""
    q, u = r["qty"], r["unit"]
    if q is None:
        return None
    if u in MOL_UNITS:
        return q * MOL_UNITS[u]
    mw = _mol_props(r["smiles"])[0]
    mass = q * MASS_UNITS[u] if u in MASS_UNITS else None
    if u in VOL_UNITS:
        d = _density(r["name"], r["role"], r["smiles"])
        mass = q * VOL_UNITS[u] * d if d else None
    return mass / mw if mass is not None and mw else None


def _same_amount(a: dict, b: dict, rel_tol: float = 0.03) -> bool:
    """True if two records state the same amount, e.g. '21.6 mg' and '0.200 mmol' of a 108 g/mol compound."""
    if (a["qty"], a["unit"]) == (b["qty"], b["unit"]):
        return True
    x, y = _in_mol(a), _in_mol(b)
    if x is None or y is None:
        return False
    return abs(x - y) <= rel_tol * max(abs(x), abs(y))


def build_review_table(pages: list[dict]) -> pd.DataFrame:
    rec: dict[str, dict] = {}
    for p in pages:
        data = p.get("data")
        data = _as_dict(data) if data is not None else None
        page_rec: dict[str, dict] = {}
        for c in (data or {}).get("components") or []:   # 1) within one page
            r = _component(c, p.get("page", 0))
            if r is None:
                continue
            k = _key(r)
            if k not in page_rec:
                page_rec[k] = r
                continue
            q = page_rec[k]
            if q["role"] == "ignore" and r["role"] != "ignore":
                q["role"] = r["role"]
            if not q["has_amt"] and r["has_amt"]:
                q.update(qty=r["qty"], unit=r["unit"], equiv=r["equiv"], has_amt=True)
            elif r["qty"] is not None and q["qty"] is not None and r["unit"] == q["unit"] \
                    and q["role"] in _SUMMABLE_ROLES:
                q["qty"] += r["qty"]
                q["flags"].add("several portions summed")
            elif r["has_amt"] and not (_same_amount(q, r) if r["qty"] is not None and q["qty"] is not None
                                        else (r["qty"], r["equiv"]) == (q["qty"], q["equiv"])):
                q["flags"].add("listed twice with different amounts: check")
        for k, r in page_rec.items():                   # 2) across pages
            if k not in rec:
                rec[k] = r
                continue
            q = rec[k]
            q["pages"] |= r["pages"]
            q["flags"] |= r["flags"]
            if q["role"] == "ignore" and r["role"] != "ignore":
                q["role"] = r["role"]
            if not q["has_amt"] and r["has_amt"]:
                q.update(qty=r["qty"], unit=r["unit"], equiv=r["equiv"], has_amt=True)
            elif r["has_amt"] and q["has_amt"] and r["qty"] is not None and q["qty"] is not None \
                    and not _same_amount(q, r):
                q["flags"].add("different amounts on different pages")

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
            else:
                r["flags"].add(f"unit '{u or '?'}' not understood: enter mass")
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

    # pass 2: equivalents -> moles. Reference = moles per 1 equiv, taken from a reactant whose
    # moles and equivalents are both known; otherwise the smallest-moles reactant (= 1 equiv).
    refs = [r["mol"] / r["equiv"] for r in rec.values()
            if r["role"] == "reactant" and r["mol"] and r["equiv"] and r["equiv"] > 0]
    lim = [r["mol"] for r in rec.values() if r["role"] == "reactant" and r["mol"]]
    lim_mol = min(refs) if refs else (min(lim) if lim else None)
    for r in rec.values():
        if (r["equiv"] is not None and r["mol"] is None and lim_mol
                and r["role"] in ("reactant", "catalyst", "base", "additive")):
            r["mol"] = r["equiv"] * lim_mol
            r["flags"].add("derived from equivalents")
            if r["mw"]:
                r["mass"] = r["mol"] * r["mw"]

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
            r["flags"].add("MW unknown: enter MW or SMILES")
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
            "Mass (g)": _sig(r["mass"]), "Moles (mol)": _sig(r["mol"]),
            "MW (g/mol)": r["mw"], "Carbons": r["carbons"], "SMILES": r["smiles"],
            "Page": ", ".join(str(x) for x in sorted(r["pages"])),
            "Flag": "; ".join(sorted(r["flags"])),
        })
    df = _typed(pd.DataFrame(rows, columns=COLUMNS))
    if df.empty:
        return df
    df["_o"] = df["Role"].map(ROLES.index)
    return df.sort_values(["_o", "Name"], key=lambda c: c.str.lower() if c.dtype == object else c) \
             .drop(columns="_o").reset_index(drop=True)


def _hours(x) -> Optional[float]:
    """'30 min' -> 0.5, '2 d' -> 48, '16 h' -> 16, 'overnight' -> 16, 3 -> 3 (already hours)."""
    if isinstance(x, str) and re.search(r"overnight|o/n", x, re.I):
        return 16.0
    v, u = _parse_qty(x)
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


def _text(v) -> Optional[str]:
    if v is None or isinstance(v, (dict, list)):
        return None
    v = str(v).strip()
    return None if v.lower() in ("", "null", "none", "n/a") else v


def merge_meta(pages: list[dict]) -> dict:
    meta = {"yield_percent": None, "temperature_c": None, "time_h": None,
            "atmosphere": None, "activation": None, "workup": [], "heated": False}
    for p in pages:
        d = p.get("data")
        d = _as_dict(d) if d is not None else None
        if not d:
            continue
        if meta["yield_percent"] is None:
            y = _num(d.get("yield_percent", d.get("yield")))
            if y is not None and 0 < y <= 100:
                meta["yield_percent"] = y
        cond = d.get("conditions") if isinstance(d.get("conditions"), dict) else {}
        t = cond.get("temperature_c", cond.get("temperature"))
        if isinstance(t, str) and re.search(r"reflux|heat", t, re.I):
            meta["heated"] = True
        if meta["temperature_c"] is None:
            meta["temperature_c"] = _num(t)
        if meta["time_h"] is None:
            meta["time_h"] = _hours(cond.get("time_h", cond.get("time")))
        for k in ("atmosphere", "activation"):
            v = _text(cond.get(k))
            if not meta[k] and v:
                meta[k] = v
        w = cond.get("workup") or []
        for item in ([w] if isinstance(w, (str, dict)) else w if isinstance(w, list) else []):
            item = _text(item)
            if item and item.lower() not in meta["workup"]:
                meta["workup"].append(item.lower())
    return meta

# ---------------------------------------------------------------------------
# 6. Review table -> calculator inputs
# ---------------------------------------------------------------------------

def _changed(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return False
    if pd.isna(a) or pd.isna(b):
        return True
    return abs(float(a) - float(b)) > 1e-12 * max(1.0, abs(float(a)))


def _complete(df: pd.DataFrame, original: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Fill MW / carbons / moles / mass for rows the user added or edited by hand.

    If `original` (the table before editing) is given, a hand-edited mass recomputes the moles
    and a hand-edited moles value recomputes the mass, so the two never contradict each other."""
    d = _typed(df.copy())
    for c in COLUMNS:
        if c not in d:
            d[c] = None if c not in NUMERIC_COLUMNS else float("nan")
    orig = _typed(original) if original is not None else None
    for i in d.index:
        name = str(d.at[i, "Name"] or "").strip()
        role = d.at[i, "Role"]
        if role in ("water", "ignore") or not name:
            continue
        smi_in = d.at[i, "SMILES"]
        if orig is not None and i in orig.index and isinstance(smi_in, str) and smi_in.strip() \
                and smi_in != orig.at[i, "SMILES"]:          # SMILES corrected by hand
            if not _changed(d.at[i, "MW (g/mol)"], orig.at[i, "MW (g/mol)"]):
                d.at[i, "MW (g/mol)"] = float("nan")
            if not _changed(d.at[i, "Carbons"], orig.at[i, "Carbons"]):
                d.at[i, "Carbons"] = float("nan")
        if pd.isna(d.at[i, "MW (g/mol)"]) or pd.isna(d.at[i, "Carbons"]):
            smi = _canon(smi_in) if isinstance(smi_in, str) and smi_in.strip() else None
            smi = smi or _canon(resolve_smiles(name))
            mw, c = _mol_props(smi)
            if mw:
                if pd.isna(d.at[i, "MW (g/mol)"]):
                    d.at[i, "MW (g/mol)"] = mw
                if pd.isna(d.at[i, "Carbons"]):
                    d.at[i, "Carbons"] = c
                d.at[i, "SMILES"] = smi
        mw = d.at[i, "MW (g/mol)"]
        mass, mol = d.at[i, "Mass (g)"], d.at[i, "Moles (mol)"]
        if orig is not None and i in orig.index:
            o = orig.loc[i]
            mass_edit = _changed(mass, o["Mass (g)"])
            mol_edit = _changed(mol, o["Moles (mol)"])
            mw_edit = _changed(mw, o["MW (g/mol)"])
            if (mass_edit or mw_edit) and not mol_edit:
                mol = float("nan")                  # stale: recompute from the new mass / MW
            elif mol_edit and not mass_edit:
                mass = float("nan")
        has_mw = pd.notna(mw) and mw > 0
        if pd.isna(mol) and pd.notna(mass) and has_mw:
            mol = mass / mw
        elif pd.isna(mass) and pd.notna(mol) and has_mw:
            mass = mol * mw
        d.at[i, "Mass (g)"], d.at[i, "Moles (mol)"] = mass, mol
    return d


def _active(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["Role"].isin(ROLES) & (df["Role"] != "ignore")]


def to_app_tables(df: pd.DataFrame, original: Optional[pd.DataFrame] = None) -> dict:
    d = _complete(_active(df), original)

    rx = d[d["Role"] == "reactant"]
    lim_idx = rx["Moles (mol)"].idxmin() if rx["Moles (mol)"].notna().any() else None
    reactants_df = pd.DataFrame({
        "Name": rx["Name"].astype(str),
        "MW (g/mol)": rx["MW (g/mol)"].fillna(0.0),
        "Moles charged (mol)": rx["Moles (mol)"].fillna(0.0),
        "Mass charged (g)": rx["Mass (g)"].fillna(0.0),
        "Carbons/molecule": rx["Carbons"].fillna(0).round().astype(int),
        "Stoich. ratio (per limiting reagent)": 1.0,   # edit if the reaction is not 1:1
        "Limiting reagent?": rx.index == lim_idx,
        "Benign?": False,
    }).reset_index(drop=True)

    ax = d[d["Role"].isin(AUX_ROLES)]
    aux_df = pd.DataFrame({"Name": ax["Name"].astype(str),
                           "Mass (g)": ax["Mass (g)"].fillna(0.0), "Benign?": False}).reset_index(drop=True)

    out = {"reactants_df": reactants_df, "aux_df": aux_df,
           "compounds": list(dict.fromkeys(list(reactants_df["Name"]) + list(aux_df["Name"]))),
           "water_mass": float(d.loc[d["Role"] == "water", "Mass (g)"].fillna(0).sum())}

    pr = d[d["Role"] == "product"]
    if not pr.empty:
        p = pr.iloc[0]
        out["product"] = {
            "name": str(p["Name"]),
            "mw": float(p["MW (g/mol)"]) if pd.notna(p["MW (g/mol)"]) else 0.0,
            "actual_mass": float(p["Mass (g)"]) if pd.notna(p["Mass (g)"]) else 0.0,
            "moles": float(p["Moles (mol)"]) if pd.notna(p["Moles (mol)"]) else 0.0,
            "carbons": int(round(p["Carbons"])) if pd.notna(p["Carbons"]) else 0,
        }
    return out


def estimate_yield(df: pd.DataFrame, original: Optional[pd.DataFrame] = None) -> Optional[float]:
    """Product moles / limiting-reactant moles x 100 (assumes 1:1 stoichiometry)."""
    d = _complete(_active(df), original)
    pr = d[(d["Role"] == "product") & d["Moles (mol)"].notna()]
    rx = d[(d["Role"] == "reactant") & d["Moles (mol)"].notna() & (d["Moles (mol)"] > 0)]
    if pr.empty or rx.empty:
        return None
    return round(100 * pr.iloc[0]["Moles (mol)"] / rx["Moles (mol)"].min(), 1)


_WORKUP_RULES = [  # (regex, Eco-Scale category), ordered highest penalty first
    (r"chromatograph|\bcolumn\b", "Classical chromatography"),
    (r"distill", "Distillation"),
    (r"sublim", "Sublimation"),
    (r"(?<!solid-phase )(?<!solid phase )extract|separatory|\bwash",
     "Liquid-liquid extraction (incl. drying/filtration of desiccant)"),
    (r"solid[- ]phase extract|\bspe\b", "Solid-phase extraction"),
    (r"crystal", "Crystallization and filtration"),
    (r"filtrat|filter", "Simple filtration"),
]


def suggest_eco_inputs(meta: dict, yield_pct: Optional[float]) -> dict:
    """Best-guess Eco-Scale inputs. Price and safety are NOT in a paper: the user sets them."""
    t, h = meta.get("temperature_c"), meta.get("time_h")
    short = h is not None and h < 1
    if t is not None and t < 0:
        tt = "Cooling, < 0 degC"
    elif t is not None and t <= 5:
        tt = "Cooling to 0 degC"
    elif (t is not None and t > 35) or meta.get("heated"):
        tt = "Heating, < 1 h" if short else "Heating, > 1 h"
    else:
        tt = "Room temperature, < 1 h" if short else "Room temperature, < 24 h"

    setups = []
    if re.search(r"nitrogen|argon|inert|\bn2\b|\bar\b|glove", str(meta.get("atmosphere") or ""), re.I):
        setups.append("Inert gas atmosphere")
    if re.search(r"\bleds?\b|light|photo|irradiat|\buv\b|h\u03bd|\bhv\b|microwave|\bmw\b|"
                 r"ultrasound|sonicat|electro", str(meta.get("activation") or ""), re.I):
        setups.append("Unconventional activation (microwave / ultrasound / photochemical)")

    workup_text = " ".join(str(w) for w in meta.get("workup") or []).lower()
    workup = next((v for k, v in _WORKUP_RULES if re.search(k, workup_text)), "None")
    steps = []                                   # every step mentioned (penalties add up)
    for item in meta.get("workup") or []:
        for part in re.split(r"[;,/]| and | then ", str(item).lower()):
            hit = next((v for k, v in _WORKUP_RULES if re.search(k, part)), None)
            if hit:
                steps.append(hit)

    tt = tt if tt in es.TEMPERATURE_TIME_PENALTIES else next(iter(es.TEMPERATURE_TIME_PENALTIES))
    workup = workup if workup in es.WORKUP_PENALTIES else "None"
    setups = [x for x in setups if x in es.TECHNICAL_SETUP_PENALTIES]
    y = _num(yield_pct)
    steps = [x for x in steps if x in es.WORKUP_PENALTIES]
    return {"yield_pct": float(min(100.0, max(0.0, y if y is not None else 90.0))),
            "temp_time": tt, "setups": setups or ["Common setup"], "workup": workup,
            "workup_steps": steps}
