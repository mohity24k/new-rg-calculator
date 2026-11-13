"""PubChem client: compound identifier and GHS classification.

The host is fixed (no user-supplied URLs). Calls are rate-limited to stay within PubChem's
usage policy (at most 5 requests per second). A transient failure raises LookupFailed, so the
caller does not cache it.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

import requests

from gms.chem.names import LookupFailed

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest"
TIMEOUT = 10
_MIN_INTERVAL = 0.25
_lock = threading.Lock()
_last = [0.0]

_H_RE = re.compile(r"^\s*((?:EU)?H\d{3}[A-Za-z]{0,2})\b[^(:]*(?:\((\d+(?:\.\d+)?)%\))?")


def _request(method: str, url: str, **kw) -> requests.Response:
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    try:
        r = requests.request(method, url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise LookupFailed(str(e)) from e
    if r.status_code in (429, 500, 502, 503, 504):
        raise LookupFailed(f"HTTP {r.status_code}")
    return r


def cids_for(name: Optional[str] = None, smiles: Optional[str] = None) -> list[int]:
    """PubChem compound identifiers for a SMILES (exact structure) and for a name, in that
    order, without duplicates. Salts and complexes often have their hazard data on the record
    found by name, so the caller tries both."""
    out: list[int] = []
    for namespace, value in (("smiles", smiles), ("name", name)):
        if not value:
            continue
        r = _request("POST", f"{BASE}/pug/compound/{namespace}/cids/JSON", data={namespace: value})
        if r.status_code in (400, 404):
            continue
        if not r.ok:
            raise LookupFailed(f"HTTP {r.status_code}")
        try:
            cids = r.json()["IdentifierList"]["CID"]
        except (ValueError, KeyError):
            continue
        if cids and cids[0] and int(cids[0]) not in out:
            out.append(int(cids[0]))
    return out


def parse_ghs(record: dict) -> list[dict]:
    """PUG View 'GHS Classification' record -> one entry per source:
    {'source': str, 'statements': [(code, share_percent_or_None), ...], 'not_classified': bool}
    in PubChem's order. 'not_classified' means the source states that the compound does not meet
    the GHS hazard criteria."""
    refs = {r.get("ReferenceNumber"): r.get("SourceName", "") for r in record.get("Reference", [])}
    out: dict[int, dict] = {}

    def walk(node):
        for s in node.get("Section", []):
            if s.get("TOCHeading") == "GHS Classification":
                for info in s.get("Information", []):
                    ref = info.get("ReferenceNumber")
                    entry = out.setdefault(ref, {"source": refs.get(ref, ""), "statements": [], "not_classified": False})
                    if info.get("Name") != "GHS Hazard Statements":
                        continue
                    for item in info.get("Value", {}).get("StringWithMarkup", []):
                        text = item.get("String", "")
                        if text.strip().lower().startswith("not classified"):
                            entry["not_classified"] = True
                        m = _H_RE.match(text)
                        if m:
                            share = float(m.group(2)) if m.group(2) else None
                            entry["statements"].append((m.group(1).upper().replace("EUH", "EUH"), share))
            walk(s)

    walk(record)
    return list(out.values())


def ghs_for_cid(cid: int) -> Optional[list[dict]]:
    """GHS classifications of a compound, per source. None = PubChem has no GHS section for it."""
    r = _request("GET", f"{BASE}/pug_view/data/compound/{int(cid)}/JSON", params={"heading": "GHS Classification"})
    if r.status_code in (400, 404):
        return None
    if not r.ok:
        raise LookupFailed(f"HTTP {r.status_code}")
    try:
        return parse_ghs(r.json().get("Record", {}))
    except ValueError as e:
        raise LookupFailed("invalid JSON from PubChem") from e
