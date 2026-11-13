"""PDF extraction as a server-side service for the app.

The extraction model (Qwen2.5-VL behind an OpenAI-compatible endpoint) is configured only
through server-side settings (gms.settings: GMS_VLM_BASE_URL, GMS_VLM_MODEL, GMS_VLM_API_KEY).
A visitor cannot see or change the endpoint or the key, so the app cannot be made to call
other hosts. The server being offline is a normal state: `status()` reports it, and the rest of
the app works without it.

Uploaded PDFs are processed in memory and are not written to disk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

from gms import settings as settings_mod
from gms.chem import conditions, units
from gms.extraction import classic
from gms.extraction import config as xcfg
from gms.extraction import enrich, pipeline
from gms.extraction.engines import HttpEngine, ServerUnavailable
from gms.model import Component, Reaction

ROLE_MAP = {"reactant": "reactant", "reagent": "reagent", "catalyst": "catalyst", "base": "base",
            "additive": "additive", "reaction_solvent": "reaction_solvent", "workup": "workup",
            "purification": "purification", "water": "water", "product": "product"}


class ExtractionError(Exception):
    """A message that can be shown to the user (no server details, no key)."""


@dataclass
class Status:
    configured: bool
    online: bool
    message: str


_cache = {"at": 0.0, "status": None, "key": None}


def status(s: Optional[settings_mod.Settings] = None, max_age_s: float = 30.0) -> Status:
    """Is extraction available right now? The answer is cached for a short time."""
    s = s or settings_mod.load()
    if not s.vlm_base_url:
        return Status(False, False, "PDF extraction is not set up on this server. "
                                    "Enter the reaction by hand; every other function works.")
    key = (s.vlm_base_url, s.vlm_model)
    if _cache["status"] is not None and _cache["key"] == key and time.monotonic() - _cache["at"] < max_age_s:
        return _cache["status"]
    base = s.vlm_base_url.rstrip("/")
    base = base[:-len("/chat/completions")] if base.endswith("/chat/completions") else base
    offline = ("The extraction server is offline at the moment. This is normal: it runs only part of the "
               "time. Enter the reaction by hand, or try again later.")
    try:
        r = requests.get(base + "/models", headers={"Authorization": f"Bearer {s.vlm_api_key}"}, timeout=4)
    except requests.RequestException:
        online, msg = False, offline
    else:
        if r.status_code in (401, 403):
            online, msg = False, ("The extraction server refused the request. Tell the administrator of this "
                                  "installation (the access key may be wrong).")
        elif r.status_code >= 500:          # for example a tunnel that is up while the model server is down
            online, msg = False, offline
        else:
            online, msg = True, "The extraction server is online."
    st = Status(True, online, msg)
    _cache.update(at=time.monotonic(), status=st, key=key)
    return st


def reset_status_cache() -> None:
    _cache.update(at=0.0, status=None, key=None)


def check_upload(pdf: bytes, s: Optional[settings_mod.Settings] = None) -> int:
    """Validate an uploaded file. Returns the page count. Raises ExtractionError with a clear message."""
    s = s or settings_mod.load()
    if len(pdf) > s.max_upload_mb * 1024 * 1024:
        raise ExtractionError(f"The file is larger than {s.max_upload_mb} MB.")
    if not pdf.startswith(b"%PDF"):
        raise ExtractionError("The file is not a PDF.")
    try:
        n = classic.page_count(pdf)
    except ValueError as e:
        raise ExtractionError(f"The PDF cannot be opened: {e}.") from e
    if n == 0:
        raise ExtractionError("The PDF has no pages.")
    return n


def to_reaction(rec: dict, file: str = "") -> Reaction:
    """Enriched extraction record -> the application's reaction record. Roles are kept."""
    comps = []
    for c in rec["components"]:
        role = ROLE_MAP.get(c["role"])
        if role is None:                       # "ignore": not part of the mass balance
            continue
        u = c.get("unit") or ""
        q = c.get("quantity")
        comp = Component(
            name=c["name"], role=role, stage=c.get("stage") if c.get("stage") in ("reaction", "workup", "purification") else None,
            mass_g=q * units.MASS_UNITS[u] if q is not None and u in units.MASS_UNITS else None,
            volume_ml=q * units.VOL_UNITS[u] if q is not None and u in units.VOL_UNITS else None,
            moles=(q * units.MOL_UNITS[u] if q is not None and u in units.MOL_UNITS
                   else c["mmol"] / 1000 if c.get("mmol") is not None and u not in units.MASS_UNITS else None),
            equiv=c.get("equiv"), smiles=c.get("smiles"), note="; ".join(c.get("flags") or []))
        comps.append(comp)
    cond = rec.get("conditions") or {}
    steps = conditions.workup_steps_from_text(list(rec.get("workup") or []) + ([rec["purification"]] if rec.get("purification") else []))
    src = rec.get("source") or {}
    return Reaction(
        name=enrich.summary(rec).split(" · ")[0], components=comps,
        reported_yield_percent=rec.get("yield_percent"), temperature_c=cond.get("temperature_c"),
        time_h=cond.get("time_h"), reflux=bool(cond.get("reflux")), atmosphere=cond.get("atmosphere"),
        activation=cond.get("activation"), workup_steps=steps,
        source={"entry": "pdf", "file": file, "pages": src.get("pages", []), "doi": src.get("doi"),
                "flags": [f"{f['code']}: {f['detail']}" for f in rec.get("flags", [])],
                "pipeline_version": rec.get("pipeline_version")})


def extract(pdf: bytes, file: str, first_page: int, last_page: int,
            s: Optional[settings_mod.Settings] = None, on_progress=None) -> dict:
    """Extract the reactions on pages first_page..last_page (1-based, inclusive).

    Returns {'reactions': [records], 'tables': [...], 'pages': [...], 'seconds': float}.
    Raises ExtractionError with a message that is safe to show."""
    s = s or settings_mod.load()
    n = check_upload(pdf, s)
    first_page, last_page = max(1, first_page), min(n, last_page)
    if first_page > last_page:
        raise ExtractionError("The first page must not be after the last page.")
    if last_page - first_page + 1 > s.max_pdf_pages:
        raise ExtractionError(f"At most {s.max_pdf_pages} pages can be read at once. Choose a smaller page range.")
    if not s.vlm_base_url:
        raise ExtractionError(status(s).message)
    cfg = xcfg.load_config()
    sv = cfg["server"]
    eng = HttpEngine(s.vlm_base_url, s.vlm_model, s.vlm_api_key, concurrency=sv["concurrency"],
                     timeout=sv["timeout_s"], retries=sv["retries"])
    try:
        out = pipeline.extract_pdf(pdf, eng, cfg, range(first_page - 1, last_page), file=file, on_progress=on_progress)
    except ServerUnavailable:
        reset_status_cache()
        raise ExtractionError("The extraction server did not answer. It may have gone offline. "
                              "Enter the reaction by hand, or try again later.") from None
    reactions = [enrich.enrich_reaction(r, cfg) for r in out["reactions"]]
    reactions.sort(key=lambda r: r.get("record_type") == "general_procedure")
    failed = sum(1 for u in out["results"] if not u["ok"])
    return {"reactions": reactions, "tables": out["tables"], "pages": out["pages"], "seconds": out["seconds"],
            "failed_requests": failed, "requests": len(out["results"])}
