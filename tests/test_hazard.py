"""Hazard data: PubChem answers are parsed, one classification is selected, scores follow the
config. PubChem itself is replaced by a stub."""

import json

import pytest

from gms import config
from gms.chem import hazard, names, pubchem


def _view(entries):
    """A PUG View record with 'GHS Classification' information per source."""
    refs, infos = [], []
    for i, (source, strings) in enumerate(entries, 1):
        refs.append({"ReferenceNumber": i, "SourceName": source})
        infos.append({"ReferenceNumber": i, "Name": "GHS Hazard Statements",
                      "Value": {"StringWithMarkup": [{"String": s} for s in strings]}})
        infos.append({"ReferenceNumber": i, "Name": "Signal", "Value": {"StringWithMarkup": [{"String": "Danger"}]}})
    return {"Record": {"Reference": refs, "Section": [{"TOCHeading": "Safety and Hazards", "Section": [
        {"TOCHeading": "Hazards Identification", "Section": [{"TOCHeading": "GHS Classification", "Information": infos}]}]}]}}


DMF = _view([
    ("European Chemicals Agency (ECHA)", ["H226 (33.5%): Flammable liquid and vapor [Warning Flammable liquids]",
                                          "H312 (98.6%): Harmful in contact with skin",
                                          "H360 (93.5%): May damage fertility or the unborn child"]),
    ("Regulation (EC) No 1272/2008 of the European Parliament and of the Council",
     ["H312: Harmful in contact with skin", "H319: Causes serious eye irritation", "H332: Harmful if inhaled",
      "H360D ***: May damage the unborn child [Danger Reproductive toxicity]"]),
    ("NITE-CMC", ["H350: May cause cancer"]),
])
SULFATE = _view([
    ("European Chemicals Agency (ECHA)", ["Not Classified", "Reported as not meeting GHS hazard criteria by 4127 of 4277 companies"]),
    ("NITE-CMC", ["H320: Causes eye irritation", "H370: Causes damage to organs"]),
])


def test_parse_and_prefer_the_harmonised_classification():
    sources = pubchem.parse_ghs(DMF["Record"])
    assert [s["source"].split(" ")[0] for s in sources] == ["European", "Regulation", "NITE-CMC"]
    assert sources[0]["statements"][0] == ("H226", 33.5)
    codes, source = hazard.select_statements(sources)
    assert codes == ["H312", "H319", "H332", "H360D"] and source.startswith("Regulation (EC)")


def test_notification_share_threshold_and_not_classified():
    echa_only = pubchem.parse_ghs(_view([("European Chemicals Agency (ECHA)", [
        "H226 (33.5%): Flammable", "H312 (98.6%): Harmful", "H360 (50%): May damage fertility"])])["Record"])
    assert hazard.select_statements(echa_only)[0] == ["H312", "H360"]       # 33.5 % is below the 50 % threshold
    # "Not Classified" by ECHA wins over a hazard statement from a lower-priority source
    codes, source = hazard.select_statements(pubchem.parse_ghs(SULFATE["Record"]))
    assert codes == [] and source.startswith("European Chemicals Agency")


def test_scores_symbols_and_high_hazard_follow_the_config():
    dmf = hazard.HazardProfile("DMF", "classified", ["H312", "H319", "H332", "H360D"])
    assert hazard.scores(dmf) == {"health": 9, "safety": 1, "environment": 1}
    assert hazard.ecoscale_symbols(dmf) == ["T"] and hazard.is_high_hazard(dmf)
    ether = hazard.HazardProfile("diethyl ether", "classified", ["H224", "H302", "H336"])
    assert hazard.scores(ether)["safety"] == 7 and hazard.ecoscale_symbols(ether) == ["F+"]
    etoac = hazard.HazardProfile("ethyl acetate", "classified", ["H225", "H319", "H336"])
    assert hazard.scores(etoac) == {"health": 2, "safety": 5, "environment": 1} and not hazard.is_high_hazard(etoac)
    unknown = hazard.HazardProfile("x", "no_data")
    assert hazard.scores(unknown) is None and not hazard.is_high_hazard(unknown) and not unknown.known
    # health and environment scores are the CHEM21 values
    chem21 = config.data("chem21_scoring.yaml")
    ours = config.load("hazard")["scores"]
    for code, value in chem21["health"]["h_codes"].items():
        assert ours["health"][code] == value, code
    for code, value in chem21["environment"]["h_codes"].items():
        assert ours["environment"][code] == value, code


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self.ok, self._p = status, status == 200, payload

    def json(self):
        return self._p


def test_store_fetches_once_caches_and_survives_an_outage(tmp_path, monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        if "/cids/" in url:
            return _Resp(200, {"IdentifierList": {"CID": [6228]}})
        return _Resp(200, DMF)

    monkeypatch.setattr(pubchem, "_request", fake)
    path = tmp_path / "cache.json"
    store = hazard.HazardStore(path, online=True)
    p = store.lookup("some new amide solvent", "CN(C)C(=O)CC")
    assert p.status == "classified" and p.cid == 6228 and "H360D" in p.h_codes and "PubChem" in p.source
    n = len(calls)
    assert store.lookup("some new amide solvent", "CN(C)C(=O)CC").h_codes == p.h_codes and len(calls) == n
    assert hazard.HazardStore(path, online=False).lookup("x", "CN(C)C(=O)CC").status == "classified"   # from disk
    assert json.loads(path.read_text())                                # cache file is valid JSON

    def down(*a, **k):
        raise names.LookupFailed("timeout")

    monkeypatch.setattr(pubchem, "_request", down)
    q = store.lookup("another compound", "CCCCCCCCN")
    assert q.status == "unavailable"                                    # not cached: retried later
    assert "CCCCCCCCN" not in json.loads(path.read_text())


def test_compound_without_ghs_data_is_no_data(monkeypatch):
    def fake(method, url, **kw):
        return _Resp(200, {"IdentifierList": {"CID": [1]}}) if "/cids/" in url else _Resp(404, {})

    monkeypatch.setattr(pubchem, "_request", fake)
    p = hazard.HazardStore(online=True).lookup("rare compound", "CCCCCCCCCCCCN")
    assert p.status == "no_data" and not p.known


def test_bundled_seed_is_used_offline_and_names_resolve():
    store = hazard.HazardStore(online=False)
    assert "H351" in store.lookup("DCM", "ClCCl").h_codes
    assert store.lookup("N,N-dimethylformamide").status == "classified"         # by name, no structure
    assert store.lookup("sodium sulfate", "[Na+].[Na+].[O-]S([O-])(=O)=O").status == "not_classified"
    assert store.lookup("never seen before").status == "unavailable"
    meta = store.seed()["meta"]
    assert "PubChem" in meta["source"] and meta["built"]


def test_pubchem_host_is_fixed():
    assert pubchem.BASE == "https://pubchem.ncbi.nlm.nih.gov/rest"
