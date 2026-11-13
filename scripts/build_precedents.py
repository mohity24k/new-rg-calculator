"""Build the packaged precedent data from the Open Reaction Database (ORD).

The ORD (https://open-reaction-database.org, CC BY-SA 4.0) includes the reactions that
D. M. Lowe extracted from US patents ("uspto-grants-YYYY" datasets; the source data is CC0).
This script reads those datasets, keeps the reactions of the classes the app supports, and writes

    gms/data/precedents.json            counts and quartiles per class (used by the app)
    gms/data/precedent_reactions.jsonl.gz   one compact record per kept reaction

Steps:
    python scripts/build_precedents.py download --raw data/ord_raw [--files 12]
    python scripts/build_precedents.py build --raw data/ord_raw
    python scripts/build_precedents.py aggregate        (only re-count from the packaged records)

Needs (not needed to run the app):  pip install -r requirements-dev.txt
The same `build` step will accept the records of your own corpus later: see `load_corpus`.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import json
import statistics
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gms import config  # noqa: E402
from gms.chem import classify  # noqa: E402
from gms.chem.structure import canon, mol_props  # noqa: E402

HF_API = "https://huggingface.co/api/datasets/open-reaction-database/ord-data/tree/main?recursive=true"
HF_FILE = "https://huggingface.co/datasets/open-reaction-database/ord-data/resolve/main/"
OUT_JSON = config.DATA_DIR / "precedents.json"
OUT_RECORDS = config.DATA_DIR / "precedent_reactions.jsonl.gz"
MASS = {"KILOGRAM": 1e3, "GRAM": 1.0, "MILLIGRAM": 1e-3, "MICROGRAM": 1e-6}
MOLES = {"MOLE": 1.0, "MILLIMOLE": 1e-3, "MICROMOLE": 1e-6, "NANOMOLE": 1e-9}
VOLUME = {"LITER": 1e3, "MILLILITER": 1.0, "MICROLITER": 1e-3}


def download(raw: Path, n_files: int) -> None:
    import requests
    raw.mkdir(parents=True, exist_ok=True)
    files = requests.get(HF_API, timeout=60).json()
    pq = sorted((f for f in files if f["type"] == "file" and f["path"].endswith(".parquet")), key=lambda f: -f["size"])
    for f in pq[:n_files]:
        dest = raw / Path(f["path"]).name
        if dest.exists() and dest.stat().st_size == f["size"]:
            continue
        with requests.get(HF_FILE + f["path"], timeout=600, stream=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        print("downloaded", dest.name, dest.stat().st_size, flush=True)


def key_of(smiles_list) -> str:
    """Order-independent key of one material or a mixture: canonical fragments, sorted."""
    frags = []
    for s in smiles_list:
        for f in (s or "").split("."):
            c = canon(f)
            if c:
                frags.append(c)
    return ".".join(sorted(frags))


def _amount(a):
    """(mass g, moles, volume mL) of an ORD Amount; None where not given or zero."""
    kind = a.WhichOneof("kind")
    if kind == "mass" and a.mass.value > 0:
        u = MASS.get(type(a.mass).MassUnit.Name(a.mass.units))
        return (a.mass.value * u if u else None), None, None
    if kind == "moles" and a.moles.value > 0:
        u = MOLES.get(type(a.moles).MolesUnit.Name(a.moles.units))
        return None, (a.moles.value * u if u else None), None
    if kind == "volume" and a.volume.value > 0:
        u = VOLUME.get(type(a.volume).VolumeUnit.Name(a.volume.units))
        return None, None, (a.volume.value * u if u else None)
    return None, None, None


def _component(c, pb):
    smiles = next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.SMILES), None)
    name = next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.NAME), "")
    mass, moles, vol = _amount(c.amount)
    return {"name": name, "smiles": smiles, "role": pb.ReactionRole.ReactionRoleType.Name(c.reaction_role),
            "mass_g": mass, "moles": moles, "volume_ml": vol}


def record_from_ord(rx, pb, dataset_name: str):
    """Compact record of one ORD reaction if it belongs to a supported class, else None."""
    comps = [_component(c, pb) for inp in rx.inputs.values() for c in inp.components]
    reactants = [c for c in comps if c["role"] == "REACTANT" and c["smiles"]]
    if not 2 <= len(reactants) <= 6 or not rx.outcomes:
        return None
    prods = [p for o in rx.outcomes for p in o.products]
    if not prods:
        return None
    prod = prods[0]
    psmi = next((i.value for i in prod.identifiers if i.type == pb.CompoundIdentifier.SMILES), None)
    if not psmi:
        return None
    names = " ".join(c["name"].lower() for c in comps)
    cls, how, _ = classify.classify_structures([c["smiles"] for c in reactants], psmi, names,
                                               [c["smiles"] for c in comps if c["smiles"]])
    if cls is None or how != "structure":
        return None
    pcan = canon(psmi)
    if not pcan:
        return None
    yields = [m.percentage.value for m in prod.measurements
              if m.type == pb.ProductMeasurement.YIELD and m.WhichOneof("value") == "percentage"]
    y = yields[0] if yields and 0 < yields[0] <= 100 else None
    pmass = next((m.amount.mass.value * MASS.get(type(m.amount.mass).MassUnit.Name(m.amount.mass.units), 0)
                  for m in prod.measurements
                  if m.type == pb.ProductMeasurement.AMOUNT and m.amount.WhichOneof("kind") == "mass"), None)
    # The patent data tags most reagents as REACTANT. The two coupling partners are the reactants
    # that carry the functional groups of the class; every other non-solvent input is an agent.
    partners = [c for c in reactants if classify.is_partner(cls, c["smiles"])]
    others = [c for c in comps if c["smiles"] and c["role"] != "SOLVENT" and c not in partners]
    for c in partners + others:
        if c["moles"] is None and c["mass_g"]:
            mw = mol_props(c["smiles"])[0]
            c["moles"] = c["mass_g"] / mw if mw else None
    known = [c["moles"] for c in partners if c["moles"]]
    lim = min(known) if len(partners) >= 2 and len(known) == len(partners) else None
    solvents = [c for c in comps if c["role"] == "SOLVENT" and c["smiles"]]
    vol = sum(c["volume_ml"] or 0.0 for c in solvents)
    agent_equiv = {}
    for c in others:
        k = key_of([c["smiles"]])
        if k:
            agent_equiv[k] = round(c["moles"] / lim, 3) if lim and c["moles"] else None
    rec = {
        "id": rx.reaction_id, "source": rx.provenance.patent or rx.provenance.doi or dataset_name,
        "class": cls, "product": pcan, "yield": y,
        "reactants": [canon(c["smiles"]) for c in partners],
        "solvents": sorted({key_of([c["smiles"]]) for c in solvents} - {""}),
        "agents": agent_equiv,
        "ml_per_mmol": round(vol / (lim * 1000), 3) if lim and vol and len(solvents) and all(
            c["volume_ml"] for c in solvents) else None,
        "excess": round(max(known) / lim, 3) if lim and len(known) == 2 else None,
        "temperature_c": (rx.conditions.temperature.setpoint.value
                          if rx.conditions.HasField("temperature") and rx.conditions.temperature.HasField("setpoint")
                          and rx.conditions.temperature.setpoint.units == 1 else None),
    }
    # reaction-stage PMI: only if every input of the reaction stage has a mass (or a volume of a known solvent)
    total, complete = 0.0, True
    for c in comps:
        m = c["mass_g"]
        if m is None and c["moles"] and c["smiles"]:
            mw = mol_props(c["smiles"])[0]
            m = c["moles"] * mw if mw else None
        if m is None and c["volume_ml"] and c["smiles"]:
            from gms.chem import solvents as solv
            s = solv.lookup(c["smiles"])
            m = c["volume_ml"] * s.density_g_ml if s and s.density_g_ml else None
        if m is None:
            complete = False
            break
        total += m
    rec["pmi_reaction_stage"] = round(total / pmass, 2) if complete and pmass and pmass > 0 and total > pmass else None
    return rec


def _process(path: str) -> list[dict]:
    import pyarrow.parquet as pq
    from ord_schema.proto import reaction_pb2 as pb
    f = pq.ParquetFile(path)
    name = (f.schema_arrow.metadata or {}).get(b"ord.name", b"").decode()
    if not name.startswith("uspto-grants"):
        return []
    out = []
    for batch in f.iter_batches(batch_size=2000, columns=["reaction"]):
        for blob in batch.column(0):
            rx = pb.Reaction()
            try:
                rx.ParseFromString(blob.as_py())
                rec = record_from_ord(rx, pb, name)
            except Exception:      # a malformed record must not stop the import
                continue
            if rec:
                out.append(rec)
    print(f"{name}: {len(out)} reactions kept", flush=True)
    return out


def _quartiles(values: list[float]) -> dict:
    v = sorted(values)
    q = statistics.quantiles(v, n=4, method="inclusive") if len(v) >= 2 else [v[0]] * 3
    dec = statistics.quantiles(v, n=10, method="inclusive") if len(v) >= 10 else []
    return {"n": len(v), "q1": round(q[0], 3), "median": round(q[1], 3), "q3": round(q[2], 3),
            "deciles": [round(x, 3) for x in ([v[0]] + dec + [v[-1]])] if dec else []}


def aggregate(records: list[dict], sources: list[str]) -> dict:
    classes = {}
    for cls in classify.CLASSES:
        rs = [r for r in records if r["class"] == cls]
        if not rs:
            continue
        groups = {"solvent": defaultdict(list), "agent": defaultdict(list)}
        for r in rs:
            keys = set(r["solvents"])
            if len(r["solvents"]) > 1:
                keys.add(key_of(r["solvents"]))            # the mixture as a whole
            for k in keys:
                groups["solvent"][k].append(r)
            for k in r["agents"]:
                groups["agent"][k].append(r)
        cls_out = {"n": len(rs), "n_sources": len({r["source"] for r in rs})}
        for g, table in groups.items():
            cls_out[g] = {}
            for k, uses in table.items():
                if len(uses) < 2:
                    continue
                ys = [u["yield"] for u in uses if u["yield"] is not None]
                q = _quartiles(ys) if ys else None
                cls_out[g][k] = {
                    "n": len(uses), "n_sources": len({u["source"] for u in uses}), "n_with_yield": len(ys),
                    "yield_q1": q["q1"] if q else None, "yield_median": q["median"] if q else None,
                    "yield_q3": q["q3"] if q else None,
                    "examples": [f"{u['id']} ({u['source']})" for u in uses[:5]]}
                if g == "solvent":
                    # Extraction solvents are sometimes listed as reaction solvents in the patent data.
                    # "n_sole" counts only the reactions in which this is the complete solvent list.
                    cls_out[g][k]["n_sole"] = sum(1 for u in uses if key_of(u["solvents"]) == k)
                if g == "agent":
                    eq = [u["agents"][k] for u in uses if u["agents"].get(k) and 0 < u["agents"][k] < 50]
                    if len(eq) >= 10:
                        qe = _quartiles(eq)
                        cls_out[g][k].update(equiv_n=qe["n"], equiv_q1=qe["q1"], equiv_median=qe["median"],
                                             equiv_q3=qe["q3"])
        typical = {}
        for field, name in (("yield", "yield"), ("ml_per_mmol", "ml_per_mmol"), ("excess", "equiv:reactant"),
                            ("pmi_reaction_stage", "pmi_reaction_stage"), ("temperature_c", "temperature_c")):
            vals = [r[field] for r in rs if r[field] is not None]
            if field in ("ml_per_mmol", "excess", "pmi_reaction_stage"):
                vals = [v for v in vals if 0 < v < 1e4]
            if len(vals) >= 10:
                typical[name] = _quartiles(vals)
        cls_out["typical"] = typical
        classes[cls] = cls_out
    return {
        "meta": {
            "name": "Open Reaction Database, US patent reactions (uspto-grants datasets)",
            "built": datetime.date.today().isoformat(),
            "license": "CC BY-SA 4.0 (Open Reaction Database). The underlying patent reaction data "
                       "(D. M. Lowe, 2012-2017) is CC0.",
            "citation": "S. M. Kearnes et al., J. Am. Chem. Soc. 2021, 143, 18820; D. M. Lowe, "
                        "Chemical reactions from US patents (1976-Sep2016), figshare, 2017.",
            "datasets": sorted(sources),
            "limits": "Patent procedures. Roles and amounts were extracted by text mining and contain errors. "
                      "Workup and purification amounts are mostly missing, so the PMI values cover the "
                      "reaction stage only. Yields are reported yields of many different substrates.",
        },
        "classes": classes,
    }


def build(raw: Path, workers: int) -> None:
    paths = sorted(str(p) for p in raw.glob("*.parquet"))
    with Pool(workers) as pool:
        parts = pool.map(_process, paths)
    records = [r for part in parts for r in part]
    seen, unique = set(), []
    for r in records:                                   # the same reaction text can appear in several patents
        sig = (r["class"], r["product"], tuple(r["reactants"]), tuple(r["solvents"]), tuple(sorted(r["agents"])), r["yield"])
        if sig not in seen:
            seen.add(sig)
            unique.append(r)
    data = aggregate(unique, sorted({p.split("/")[-1] for p in paths}))
    OUT_JSON.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    with gzip.open(OUT_RECORDS, "wt", encoding="utf-8") as f:
        for r in unique:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    for cls, c in data["classes"].items():
        print(f"{cls}: {c['n']} reactions from {c['n_sources']} patents; {len(c['solvent'])} solvent keys, "
              f"{len(c['agent'])} agent keys; typical: { {k: (v['n'], v['median']) for k, v in c['typical'].items()} }")
    print("wrote", OUT_JSON, OUT_JSON.stat().st_size, "and", OUT_RECORDS, OUT_RECORDS.stat().st_size)


def reaggregate() -> None:
    """Rebuild precedents.json from the packaged per-reaction records (no download needed)."""
    with gzip.open(OUT_RECORDS, "rt", encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    old = json.loads(OUT_JSON.read_text("utf-8")) if OUT_JSON.exists() else {"meta": {}}
    data = aggregate(records, old["meta"].get("datasets", []))
    OUT_JSON.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", OUT_JSON, {k: v["n"] for k, v in data["classes"].items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--raw", default="data/ord_raw")
    d.add_argument("--files", type=int, default=12, help="number of the largest ORD files to fetch")
    b = sub.add_parser("build")
    b.add_argument("--raw", default="data/ord_raw")
    b.add_argument("--workers", type=int, default=4)
    sub.add_parser("aggregate", help="rebuild precedents.json from precedent_reactions.jsonl.gz")
    a = ap.parse_args()
    if a.cmd == "download":
        download(Path(a.raw), a.files)
    elif a.cmd == "aggregate":
        reaggregate()
    else:
        build(Path(a.raw), a.workers)


if __name__ == "__main__":
    main()
