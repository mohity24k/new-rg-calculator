"""
Reaction-record schema for dataset extraction.

* PROCEDURE_SCHEMA / TABLE_SCHEMA are JSON schemas sent to the server as `response_format`,
  so vLLM can only produce valid JSON in this shape (no repair, no retries, shorter answers).
* normalize_reaction() turns one model answer into a role-tagged record. It does no network
  calls and no chemistry: structures, masses and checks are added later by enrich.py, so the
  GPU step never waits on PubChem.

Roles (every mass is tagged, the diagnosis rules depend on it):
    reactant          substrate whose atoms end up in the product
    reagent           other stoichiometric reagent that is consumed (coupling reagent, oxidant...)
    catalyst          catalyst, precatalyst, ligand, photocatalyst
    base, additive
    reaction_solvent  solvent the reaction runs in
    workup            quench, extraction and wash solvents, aqueous solutions, drying agents
    purification      chromatography eluent and silica, recrystallization solvent
    water             any water / brine / aqueous solution (stage says where it was used)
    product           isolated product
    ignore            not part of the mass balance (TLC plates, NMR standards...)
"""

from __future__ import annotations

import copy
import re
from typing import Optional

from modules import pdf_extraction as px

SCHEMA_VERSION = "1"

MODEL_ROLES = ["reactant", "reagent", "catalyst", "base", "additive", "reaction_solvent",
               "workup", "purification", "product", "ignore"]
ROLES = MODEL_ROLES[:-1] + ["water", "ignore"]
REACTION_STAGE_ROLES = {"reactant", "reagent", "catalyst", "base", "additive", "reaction_solvent", "product"}

# ---------------------------------------------------------------------------
# JSON schemas (structured output)
# ---------------------------------------------------------------------------

_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

COMPONENT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "role": {"type": "string", "enum": MODEL_ROLES},
        "quantity": _NUM,
        "unit": _STR,
        "mmol": _NUM,
        "equivalents": _NUM,
    },
    "required": ["name", "role", "quantity", "unit", "mmol", "equivalents"],
}

CONDITIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "temperature_c": _NUM,
        "reflux": {"type": "boolean"},
        "time_h": _NUM,
        "atmosphere": _STR,
        "activation": _STR,
    },
    "required": ["temperature_c", "reflux", "time_h", "atmosphere", "activation"],
}

REACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "label": _STR,
        "procedure_label": _STR,
        "follows_procedure": _STR,
        "components": {"type": "array", "items": COMPONENT_SCHEMA},
        "yield_percent": _NUM,
        "conditions": CONDITIONS_SCHEMA,
        "workup": {"type": "array", "items": {"type": "string"}},
        "purification": _STR,
    },
    "required": ["label", "procedure_label", "follows_procedure", "components", "yield_percent",
                 "conditions", "workup", "purification"],
}

PROCEDURE_SCHEMA = {
    "type": "object",
    "properties": {"reactions": {"type": "array", "items": REACTION_SCHEMA}},
    "required": ["reactions"],
}

TABLE_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "entry": _STR,
        "solvent": _STR, "catalyst": _STR, "base": _STR, "reagent": _STR, "additive": _STR,
        "temperature_c": _NUM, "time_h": _NUM,
        "yield_percent": _NUM,
        "notes": _STR,
    },
    "required": ["entry", "solvent", "catalyst", "base", "reagent", "additive",
                 "temperature_c", "time_h", "yield_percent", "notes"],
}

TABLE_SCHEMA = {
    "type": "object",
    "properties": {"tables": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": _STR,
            "standard_conditions": _STR,
            "components": {"type": "array", "items": COMPONENT_SCHEMA},
            "entries": {"type": "array", "items": TABLE_ENTRY_SCHEMA},
        },
        "required": ["title", "standard_conditions", "components", "entries"],
    }}},
    "required": ["tables"],
}

# ---------------------------------------------------------------------------
# Prompts (instructions first, document second, so the server can cache the shared prefix)
# ---------------------------------------------------------------------------

_ROLE_RULES = """Roles:
- reactant: substrates whose atoms end up in the product.
- reagent: other stoichiometric reagents that are consumed (coupling reagents, oxidants, reductants, activators).
- catalyst: catalysts, precatalysts, ligands, photocatalysts.
- base / additive: bases; acids, salts and other additives.
- reaction_solvent: the solvent the reaction runs in.
- workup: quench, extraction and washing solvents, aqueous solutions, drying agents.
- purification: chromatography eluent, silica gel, recrystallization solvent.
- product: the isolated product, with its isolated mass (or mmol).
- ignore: TLC plates, NMR standards, instruments, vendor or purity lines."""

PROCEDURE_PROMPT = f"""You are an expert chemical-literature parser. You are given part of a chemistry paper or its
supporting information. Extract EVERY reaction procedure in it, with the quantities actually used and obtained.

Return JSON only: {{"reactions": [ ... ]}}. Each reaction has:
label, procedure_label, follows_procedure, components [{{name, role, quantity, unit, mmol, equivalents}}],
yield_percent, conditions {{temperature_c, reflux, time_h, atmosphere, activation}}, workup [steps], purification.

Rules:
- One reaction per product. In a substrate-scope list where each product has its own amounts, write one
  reaction per product, with label = the compound number (e.g. "3a").
- A general procedure ("General Procedure A", "GP2", "Method B", "Typical procedure") is its own reaction:
  procedure_label = its label (e.g. "A"); keep generic names as written (e.g. "aryl bromide").
- A product made "following General Procedure A": follows_procedure = "A"; list only what is printed for it.
- quantity = ONE number as printed, unit = its unit (g, mg, mL, uL, mmol, mol, equiv, mol%).
  For "3 x 20 mL" write 60. mmol = the mmol amount if it is also printed, else null.
- time_h in hours (overnight = 16). temperature_c in degrees C; reflux = true if heated at reflux.
- workup: short step names, e.g. ["quench", "extraction", "washing", "drying", "filtration", "concentration"].
- purification: as written, e.g. "column chromatography (hexane/EtOAc 10:1)" or "recrystallization from EtOH".
- Never invent numbers. Use null when a value is not printed.
- If there is no reaction procedure, return {{"reactions": []}}.

{_ROLE_RULES}
"""

TABLE_PROMPT = f"""You are an expert chemical-literature parser. This image is one page of a chemistry paper.
If it contains a reaction optimization or condition-screening table (entries that vary solvent, catalyst,
base, reagent, temperature or time, with yields), extract it.

Return JSON only: {{"tables": [{{title, standard_conditions, components, entries}}]}}.
- standard_conditions: the footnote that gives the amounts and conditions, copied as written.
- components: the amounts from that footnote, as [{{name, role, quantity, unit, mmol, equivalents}}].
- entries: one per table row: entry, solvent, catalyst, base, reagent, additive, temperature_c, time_h,
  yield_percent, notes. Write the value used in that row; write null where the row uses the standard value.
  Put "NMR yield", "GC yield" or "conversion" in notes when the yield is not isolated.
- Never invent numbers. If there is no such table, return {{"tables": []}}.

{_ROLE_RULES}
"""

# ---------------------------------------------------------------------------
# Normalization (no network)
# ---------------------------------------------------------------------------

_LOOSE_ROLES = {  # what models write when the schema is not enforced (older servers)
    "reactant": "reactant", "substrate": "reactant", "starting material": "reactant",
    "limiting reagent": "reactant", "coupling partner": "reactant", "nucleophile": "reactant",
    "electrophile": "reactant",
    "reagent": "reagent", "oxidant": "reagent", "reductant": "reagent", "reducing agent": "reagent",
    "oxidizing agent": "reagent", "coupling reagent": "reagent", "activator": "reagent",
    "catalyst": "catalyst", "photocatalyst": "catalyst", "precatalyst": "catalyst", "ligand": "catalyst",
    "co-catalyst": "catalyst", "cocatalyst": "catalyst", "organocatalyst": "catalyst",
    "initiator": "catalyst", "hat catalyst": "catalyst",
    "base": "base", "additive": "additive", "acid": "additive", "salt": "additive",
    "promoter": "additive", "electrolyte": "additive",
    "reaction_solvent": "reaction_solvent", "reaction solvent": "reaction_solvent", "solvent": "reaction_solvent",
    "co-solvent": "reaction_solvent", "cosolvent": "reaction_solvent",
    "workup": "workup", "work-up": "workup", "work up": "workup", "quench": "workup", "wash": "workup",
    "extraction": "workup", "drying agent": "workup", "desiccant": "workup",
    "purification": "purification", "eluent": "purification", "chromatography": "purification",
    "stationary phase": "purification", "recrystallization": "purification",
    "product": "product", "main product": "product", "isolated product": "product",
    "ignore": "ignore", "internal standard": "ignore", "standard": "ignore", "byproduct": "ignore",
    "by-product": "ignore", "side product": "ignore", "water": "water",
}

_SILICA_RE = re.compile(r"\b(silica|silica gel|alumina|sio2)\b", re.I)
_PURE_IGNORE_RE = re.compile(r"\b(tlc|cartridge|sand|cotton|filter paper|nmr|internal standard)\b", re.I)
_FILTER_AID_RE = re.compile(r"\b(celite|florisil)\b", re.I)

# names of compound classes used in general procedures ("aryl bromide (0.5 mmol)")
_GENERIC_RE = re.compile(
    r"^(the\s+)?(corresponding|appropriate|respective|substituted|desired|requisite)\b|"
    r"^(hetero)?aryl\s|^alkyl\s|"   # not benzyl/allyl/vinyl: "benzyl alcohol" is a compound
    r"^(amine|aldehyde|ketone|alcohol|carboxylic acid|acid|boronic acid|halide|alkene|alkyne|olefin|"
    r"ester|amide|nitrile|thiol|phenol|indole|imine|substrate|starting material|product|"
    r"compound|reagent|catalyst|solvent|base)s?$|"
    r"^(compound\s+)?\(?\d{1,3}[a-z]{0,2}\)?$|"           # "1a", "(3)", "compound 2b"
    r"^(substrate|product|compound)\s+\(?\d{1,3}[a-z]{0,2}\)?$", re.I)


def is_generic(name: str) -> bool:
    n = px._norm(name)
    return n in px._GENERIC or bool(_GENERIC_RE.search(n))


def _role(raw, name: str) -> tuple[str, str]:
    """(role, stage). Water and aqueous solutions always get role 'water'."""
    r = re.sub(r"\s+", " ", str(raw or "").lower().strip())
    role = _LOOSE_ROLES.get(r)
    if role is None:
        role = next((v for k, v in _LOOSE_ROLES.items() if re.search(rf"\b{re.escape(k)}\b", r)), "additive")
    stage = "reaction" if role in REACTION_STAGE_ROLES else ("purification" if role == "purification" else "workup")
    if role == "ignore":
        stage = "none"
    if px._WATER_RE.search(name) or (px._AQUEOUS_RE.search(name)
                                      and role in ("workup", "reaction_solvent", "water")):
        return "water", stage if stage != "none" else "workup"
    if _PURE_IGNORE_RE.search(name):
        return "ignore", "none"
    if _SILICA_RE.search(name):
        return "purification", "purification"
    if _FILTER_AID_RE.search(name) or (px._DRYING_RE.search(name) and role not in ("reactant", "reagent", "additive")):
        return "workup", "workup"
    return role, stage


def _label(x) -> Optional[str]:
    v = px._text(x)
    return v.strip() if v else None


def procedure_key(label: Optional[str]) -> Optional[str]:
    """'General Procedure A' / 'GP-A' / 'procedure (A)' / 'A' -> 'a'."""
    if not label:
        return None
    k = re.sub(r"\b(general|typical|representative|standard|synthetic|experimental|procedure|procedures|"
               r"method|methods|protocol|conditions|gp)\b", " ", str(label).lower())
    k = re.sub(r"[^a-z0-9]+", "", k)
    return k or None


def normalize_component(c) -> Optional[dict]:
    if not isinstance(c, dict):
        return None
    name = px._clean(str(c.get("name") or "").translate(px._SUBS))
    if not name:
        return None
    qty, unit_in_qty = px._parse_qty(c.get("quantity", c.get("amount")))
    unit = px._unit(c.get("unit")) or unit_in_qty
    equiv = px._num(c.get("equivalents", c.get("equiv")))
    mmol = px._num(c.get("mmol"))
    role, stage = _role(c.get("role"), name)
    flags = []
    if unit == "%" and role in ("catalyst", "base", "additive", "reactant", "reagent"):
        unit = "mol%"
    if unit == "mol%" and qty is not None:
        equiv, qty, unit = qty / 100, None, ""
    elif unit in px.EQUIV_UNITS and qty is not None:
        equiv, qty, unit = qty, None, ""
    if qty is None and mmol is not None:
        qty, unit = mmol, "mmol"
    if qty is not None and unit and unit not in px.MASS_UNITS and unit not in px.VOL_UNITS \
            and unit not in px.MOL_UNITS:
        flags.append(f"unit '{unit}' not understood")
    return {"name": name, "role": role, "stage": stage, "quantity": qty, "unit": unit or None,
            "mmol": mmol, "equiv": equiv, "generic": is_generic(name), "flags": flags}


_SUMMABLE = {"reaction_solvent", "workup", "purification", "water"}


def _merge_components(comps: list[dict]) -> list[dict]:
    """Same name + role twice in one reaction: portions of a solvent are summed, conflicts flagged."""
    out: dict[tuple, dict] = {}
    for c in comps:
        k = (px._norm(c["name"]), c["role"], c["stage"])
        if k not in out:
            out[k] = c
            continue
        q = out[k]
        if q["quantity"] is None and q["equiv"] is None:
            q.update(quantity=c["quantity"], unit=c["unit"], mmol=c["mmol"], equiv=c["equiv"])
        elif c["quantity"] is not None and q["quantity"] is not None and c["unit"] == q["unit"] \
                and c["role"] in _SUMMABLE:
            q["quantity"] += c["quantity"]
            q["flags"].append("several portions summed")
        elif (c["quantity"], c["unit"], c["equiv"]) != (q["quantity"], q["unit"], q["equiv"]) and \
                (c["quantity"] is not None or c["equiv"] is not None):
            q["flags"].append("listed twice with different amounts: check")
    return list(out.values())


def _conditions(raw) -> dict:
    cond = raw if isinstance(raw, dict) else {}
    t = cond.get("temperature_c", cond.get("temperature"))
    reflux = cond.get("reflux") is True or (isinstance(t, str) and bool(re.search(r"reflux", t, re.I)))
    return {
        "temperature_c": px._num(t),
        "reflux": bool(reflux),
        "time_h": px._hours(cond.get("time_h", cond.get("time"))),
        "atmosphere": px._text(cond.get("atmosphere")),
        "activation": px._text(cond.get("activation")),
    }


def _steps(x) -> list[str]:
    items = [x] if isinstance(x, str) else x if isinstance(x, list) else []
    return [s for s in (px._text(i) for i in items) if s]


def normalize_reaction(raw: dict) -> Optional[dict]:
    """One reaction object from the model -> record without source info (added by the pipeline)."""
    if not isinstance(raw, dict):
        return None
    comps = _merge_components([c for c in (normalize_component(x) for x in raw.get("components") or []) if c])
    cond_raw = raw.get("conditions")
    workup = _steps(raw.get("workup", (cond_raw or {}).get("workup") if isinstance(cond_raw, dict) else None))
    purification = px._text(raw.get("purification"))
    if purification is None:   # older answers put chromatography in the workup list
        purif = [w for w in workup if re.search(r"chromatograph|column|recrystalli|flash", w, re.I)]
        purification = "; ".join(purif) or None
    y = px._num(raw.get("yield_percent", raw.get("yield")))
    rec = {
        "label": _label(raw.get("label")),
        "procedure_label": _label(raw.get("procedure_label")),
        "follows_procedure": _label(raw.get("follows_procedure")),
        "components": comps,
        "yield_percent": y if y is not None and 0 < y <= 100 else None,
        "conditions": _conditions(cond_raw),
        "workup": workup,
        "purification": purification,
        "flags": [],
    }
    if y is not None and not 0 < y <= 100:
        rec["flags"].append({"code": "yield_out_of_range", "detail": f"yield {y} ignored"})
    if not comps and rec["procedure_label"] is None:
        return None
    return rec


def reactions_from_answer(data) -> list[dict]:
    """Parsed model JSON -> list of normalized reactions. Accepts the old single-reaction shape too."""
    if not isinstance(data, dict):
        return []
    items = data.get("reactions")
    if not isinstance(items, list):
        items = [data] if data.get("components") else []
    return [r for r in (normalize_reaction(x) for x in items) if r]


def tables_from_answer(data) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get("tables"), list):
        return []
    out = []
    for t in data["tables"]:
        if not isinstance(t, dict):
            continue
        entries = []
        for e in t.get("entries") or []:
            if not isinstance(e, dict):
                continue
            y = px._num(e.get("yield_percent"))
            entries.append({
                "entry": _label(e.get("entry")),
                **{k: px._text(e.get(k)) for k in ("solvent", "catalyst", "base", "reagent", "additive")},
                "temperature_c": px._num(e.get("temperature_c")),
                "time_h": px._hours(e.get("time_h")),
                "yield_percent": y if y is not None and 0 <= y <= 100 else None,
                "notes": px._text(e.get("notes")),
            })
        if not entries:
            continue
        out.append({
            "title": px._text(t.get("title")),
            "standard_conditions": px._text(t.get("standard_conditions")),
            "components": _merge_components([c for c in (normalize_component(x)
                                                         for x in t.get("components") or []) if c]),
            "entries": entries,
        })
    return out


# ---------------------------------------------------------------------------
# General procedures: "3b was prepared following GP A from ..." inherits what it does not state
# ---------------------------------------------------------------------------

def _has_amount(c: dict) -> bool:
    return c["quantity"] is not None or c["equiv"] is not None


def apply_general_procedure(rxn: dict, gp: dict) -> dict:
    """New record: rxn completed with the components, conditions and workup of its general procedure.
    The specific entry always wins; everything inherited is flagged."""
    out = copy.deepcopy(rxn)
    gp = copy.deepcopy(gp)
    inherited = []
    own_roles = {c["role"] for c in out["components"]}
    gp_by_role: dict[str, list[dict]] = {}
    for c in gp["components"]:
        gp_by_role.setdefault(c["role"], []).append(c)

    # reactants / reagents: specific ones replace the generic class names of the procedure
    for role in ("reactant", "reagent"):
        gp_list = gp_by_role.pop(role, [])
        if not gp_list:
            continue
        own = [c for c in out["components"] if c["role"] == role]
        generic_with_amount = [g for g in gp_list if g["generic"] and _has_amount(g)]
        for c in own:                               # "from 4-bromoanisole" -> amount of "aryl bromide"
            if not _has_amount(c) and generic_with_amount:
                g = generic_with_amount.pop(0)
                c.update(quantity=g["quantity"], unit=g["unit"], mmol=g["mmol"], equiv=g["equiv"])
                c["flags"].append(f"amount inherited from general procedure ({g['name']})")
                inherited.append(f"amount of {c['name']}")
        own_names = {px._norm(c["name"]) for c in own}
        missing = max(0, len(gp_list) - len(own))
        for g in gp_list:
            if missing <= 0:
                break
            if g["generic"] and own:
                continue        # its specific replacement is listed (or cannot be identified)
            if px._norm(g["name"]) in own_names:
                continue
            g["flags"].append("inherited from general procedure: check")
            out["components"].append(g)
            inherited.append(g["name"])
            missing -= 1

    for role, gp_list in gp_by_role.items():        # solvents, catalysts, workup...: by role
        if role in own_roles or role == "product":
            continue
        for g in gp_list:
            g["flags"].append("inherited from general procedure")
            out["components"].append(g)
            inherited.append(g["name"])

    for k, v in gp["conditions"].items():
        if out["conditions"].get(k) in (None, False) and v not in (None, False):
            out["conditions"][k] = v
            inherited.append(k)
    if not out["workup"] and gp["workup"]:
        out["workup"] = list(gp["workup"])
        inherited.append("workup")
    if not out["purification"] and gp["purification"]:
        out["purification"] = gp["purification"]
        inherited.append("purification")
    if inherited:
        out["flags"].append({"code": "inherited_from_general_procedure",
                             "detail": f"GP {gp['procedure_label']}: " + ", ".join(inherited)})
    return out


def resolve_general_procedures(reactions: list[dict]) -> list[dict]:
    """Apply each paper's general procedures to the reactions that cite them."""
    gps = {procedure_key(r["procedure_label"]): r for r in reactions if r.get("procedure_label")}
    out = []
    for r in reactions:
        ref = procedure_key(r.get("follows_procedure"))
        if r.get("procedure_label") or not ref:
            out.append(r)
        elif ref in gps:
            out.append(apply_general_procedure(r, gps[ref]))
        else:
            r = copy.deepcopy(r)
            r["flags"].append({"code": "general_procedure_not_found",
                               "detail": f"cites procedure '{r['follows_procedure']}', not found in the extracted pages"})
            out.append(r)
    return out


def _amount_sig(r: dict) -> tuple:
    prod = [c for c in r["components"] if c["role"] == "product"]
    p = prod[0] if prod else {}
    return (px._norm(p.get("name") or ""), p.get("quantity"), p.get("unit"), r["yield_percent"])


def dedupe_reactions(reactions: list[dict]) -> list[dict]:
    """The same product with the same amount extracted twice (main text + SI, overlapping chunks):
    keep the most complete copy."""
    best: dict[tuple, dict] = {}
    order = []
    for r in reactions:
        sig = (_label(r.get("label")) or "", r.get("procedure_label") or "") + _amount_sig(r)
        if not any(sig[2:]) and not sig[0] and not sig[1]:
            sig = ("#", id(r))                       # nothing to compare on: keep it
        score = (sum(_has_amount(c) for c in r["components"]), len(r["components"]))
        if sig not in best:
            order.append(sig)
            best[sig] = r
        elif score > (sum(_has_amount(c) for c in best[sig]["components"]), len(best[sig]["components"])):
            r.setdefault("source", {}).setdefault("also_on_pages", []).extend(
                best[sig].get("source", {}).get("pages", []))
            best[sig] = r
    return [best[s] for s in order]
