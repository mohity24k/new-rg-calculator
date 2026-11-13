"""Hazard profile of a material from its GHS hazard statements (H-codes).

Data flow:  bundled seed (gms/data/ghs_seed.json)  ->  local cache file  ->  PubChem (if online).
Scores and the EcoScale symbol translation are in config/hazard.yaml.

A profile has a status, so "no data" is never mistaken for "no hazard":
    classified       GHS statements found
    not_classified   GHS data exists and lists no hazard statement
    no_data          the compound, or a GHS classification for it, was not found
    unavailable      the lookup could not be done (offline, PubChem not reachable)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from gms import config
from gms.chem import names, pubchem
from gms.chem.structure import canon

DIMENSIONS = ("health", "safety", "environment")
SEED_FILE = config.DATA_DIR / "ghs_seed.json"


@dataclass
class HazardProfile:
    name: str
    status: str = "unavailable"
    h_codes: list[str] = field(default_factory=list)
    cid: Optional[int] = None
    source: str = ""
    retrieved: str = ""

    @property
    def known(self) -> bool:
        return self.status in ("classified", "not_classified")


def _profile(name: str, rec: dict) -> HazardProfile:
    return HazardProfile(name=name, status=rec["status"], h_codes=list(rec.get("h_codes", [])), cid=rec.get("cid"),
                         source=rec.get("source", ""), retrieved=rec.get("retrieved", ""))


def base_code(code: str) -> str:
    """'H360D' -> 'H360', 'H361fd' -> 'H361' (the scores are defined on the base statement)."""
    m = re.match(r"((?:EU)?H\d{3})", code)
    return m.group(1) if m else code


def select_statements(sources: list[dict]) -> tuple[list[str], str]:
    """Pick one classification from PubChem's list (priority and threshold: config/hazard.yaml)."""
    cfg = config.load("hazard")["ghs"]
    for wanted in cfg["source_priority"]:
        for s in sources:
            if not s["source"].startswith(wanted):
                continue
            if s["statements"]:
                keep = [c for c, share in s["statements"]
                        if share is None or share >= cfg["min_report_share_pct"]]
                return list(dict.fromkeys(keep)), s["source"]
            if s.get("not_classified"):              # the source says: does not meet the GHS criteria
                return [], s["source"]
    for s in sources:                                    # any other source (NITE, HCIS, ...)
        if s["statements"]:
            return list(dict.fromkeys(c for c, _ in s["statements"])), s["source"]
    return [], sources[0]["source"] if sources else ""


def scores(profile: HazardProfile) -> Optional[dict[str, int]]:
    """Health / safety / environment score (1-10) of a material, or None if its hazard is unknown."""
    if not profile.known:
        return None
    table = config.load("hazard")["scores"]
    codes = {base_code(c) for c in profile.h_codes}
    return {d: max([table[d][c] for c in codes if c in table[d]] or [table["default_no_statement"]])
            for d in DIMENSIONS}


def worst_score(profile: HazardProfile) -> Optional[int]:
    s = scores(profile)
    return max(s.values()) if s else None


def is_high_hazard(profile: HazardProfile) -> bool:
    s = scores(profile)
    limits = config.load("hazard")["high_hazard_min"]
    return s is not None and any(s[d] >= limits[d] for d in DIMENSIONS)


def ecoscale_symbols(profile: HazardProfile) -> list[str]:
    """Old EU hazard symbols (T, T+, F, F+, E, N) that the EcoScale penalises, from the H-codes."""
    table = config.load("hazard")["ecoscale_symbols"]
    codes = {base_code(c) for c in profile.h_codes}
    return [sym for sym, hs in table.items() if codes & set(hs)]


class HazardStore:
    """Looks up hazard profiles. Thread-safe. `online=False` (or settings.offline) never calls PubChem."""

    def __init__(self, cache_path: Optional[Path] = None, online: bool = True):
        self.online = online
        self.cache_path = Path(cache_path) if cache_path else None
        self._lock = threading.Lock()
        self._seed: Optional[dict] = None
        self._cache: dict[str, dict] = {}
        self._failed: dict[str, float] = {}          # key -> time of a failed lookup (not retried at once)
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._cache = {}

    # -- keys -----------------------------------------------------------------------------
    @staticmethod
    def key(name: str, smiles: Optional[str]) -> str:
        return canon(smiles) or "name:" + names.norm(names.clean(name))

    def seed(self) -> dict:
        if self._seed is None:
            self._seed = json.loads(SEED_FILE.read_text(encoding="utf-8")) if SEED_FILE.exists() \
                else {"meta": {}, "records": {}, "names": {}}
        return self._seed

    def _stored(self, name: str, smiles: Optional[str]) -> Optional[dict]:
        seed = self.seed()
        k = self.key(name, smiles)
        rec = seed["records"].get(k) or self._cache.get(k)
        if rec is None:
            alias = seed["names"].get(names.norm(names.clean(name)))
            rec = seed["records"].get(alias) if alias else None
        return rec

    # -- lookup ---------------------------------------------------------------------------
    def lookup(self, name: str, smiles: Optional[str] = None, online: Optional[bool] = None) -> HazardProfile:
        online = self.online if online is None else online
        rec = self._stored(name, smiles)
        if rec is not None:
            return _profile(name, rec)
        if not online:
            return HazardProfile(name=name, status="unavailable")
        key = self.key(name, smiles)
        retry_after = config.load("hazard")["ghs"]["retry_failed_lookup_after_s"]
        if time.monotonic() - self._failed.get(key, -1e9) < retry_after:
            return HazardProfile(name=name, status="unavailable")     # failed a moment ago: do not ask again yet
        try:
            rec = fetch(name, smiles)
        except names.LookupFailed:
            self._failed[key] = time.monotonic()
            return HazardProfile(name=name, status="unavailable")
        with self._lock:
            self._cache[key] = rec
            self._save()
        return _profile(name, rec)

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, indent=0, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            pass                                     # a read-only disk must not break scoring


def fetch(name: str, smiles: Optional[str] = None) -> dict:
    """One PubChem lookup -> storable record. Raises LookupFailed on a transient failure."""
    today = _dt.date.today().isoformat()
    cids = pubchem.cids_for(names.clean(name), smiles)
    if not cids:
        return {"status": "no_data", "h_codes": [], "cid": None, "source": "PubChem: compound not found",
                "retrieved": today}
    for cid in cids:
        sources = pubchem.ghs_for_cid(cid)
        if sources:
            codes, source = select_statements(sources)
            return {"status": "classified" if codes else "not_classified", "h_codes": codes, "cid": cid,
                    "source": f"PubChem GHS Classification ({source})", "retrieved": today}
    return {"status": "no_data", "h_codes": [], "cid": cids[0],
            "source": "PubChem: no GHS classification", "retrieved": today}


_STORE: Optional[HazardStore] = None


def store() -> HazardStore:
    """The application's hazard store (cache under GMS_DATA_DIR, offline if GMS_OFFLINE is set)."""
    global _STORE
    if _STORE is None:
        from gms import settings
        s = settings.load()
        _STORE = HazardStore(s.data_dir / "ghs_cache.json", online=not s.offline)
    return _STORE


def set_store(st: Optional[HazardStore]) -> None:
    global _STORE
    _STORE = st


def profile_dict(p: HazardProfile) -> dict:
    return asdict(p)
