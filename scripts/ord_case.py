"""Turn one Open Reaction Database record into a reaction record (YAML) for a benchmark case.

    python scripts/ord_case.py --raw data/ord_raw ord-<reaction id> [more ids]

The output is a draft: check the roles and the workup steps against the procedure text
(printed as a comment) before you use it. Needs requirements-dev.txt.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gms.chem import classify  # noqa: E402
from gms.chem.structure import canon  # noqa: E402
from scripts.build_precedents import _amount  # noqa: E402

BASE_RE = re.compile(r"amine|carbonate|hydroxide|phosphate|pyridine|dipea|morpholine|bicarbonate|acetate\b", re.I)
WORKUP_STEP = {"EXTRACTION": "Liquid-liquid extraction (incl. drying/filtration of desiccant)",
               "WASH": "Liquid-liquid extraction (incl. drying/filtration of desiccant)",
               "FILTRATION": "Simple filtration", "DISTILLATION": "Distillation"}


def _comp(c, pb, role):
    smiles = next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.SMILES), None)
    name = next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.NAME), "") or smiles or "unnamed"
    mass, moles, vol = _amount(c.amount) if hasattr(c, "amount") else (None, None, None)
    d = {"name": name, "role": role}
    if smiles and canon(smiles):
        d["smiles"] = canon(smiles)
    for k, v in (("mass_g", mass), ("moles", moles), ("volume_ml", vol)):
        if v is not None:
            d[k] = float(f"{v:.6g}")
    return d


def convert(rx, pb) -> dict:
    prod = rx.outcomes[0].products[0]
    psmi = next(i.value for i in prod.identifiers if i.type == pb.CompoundIdentifier.SMILES)
    inputs = [c for inp in rx.inputs.values() for c in inp.components]
    smi = lambda c: next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.SMILES), None)   # noqa: E731
    role_name = lambda c: pb.ReactionRole.ReactionRoleType.Name(c.reaction_role)                           # noqa: E731
    cls = classify.classify_structures([smi(c) for c in inputs if role_name(c) == "REACTANT" and smi(c)], psmi)[0]
    comps = []
    for c in inputs:
        s, rn = smi(c), role_name(c)
        name = next((i.value for i in c.identifiers if i.type == pb.CompoundIdentifier.NAME), "")
        if rn == "SOLVENT":
            role = "water" if canon(s) == "O" else "reaction_solvent"
        elif rn == "REACTANT" and classify.is_partner(cls, s):
            role = "reactant"
        elif rn == "CATALYST" or (s and re.search(r"Pd|Ni\b", s)):
            role = "catalyst"
        elif BASE_RE.search(name):
            role = "base"
        else:
            role = "reagent"
        d = _comp(c, pb, role)
        if role == "water":
            d["stage"] = "reaction"
        comps.append(d)
    steps = []
    for w in rx.workups:
        t = pb.ReactionWorkup.ReactionWorkupType.Name(w.type)
        if re.search(r"chromatograph|column", w.details, re.I):
            steps.append("Classical chromatography")
        elif re.search(r"recrystalli|crystalli", w.details, re.I):
            steps.append("Crystallization and filtration")
        elif t in WORKUP_STEP:
            steps.append(WORKUP_STEP[t])
        for c in w.input.components:
            d = _comp(c, pb, "workup")
            if canon(d.get("smiles")) == "O" or re.search(r"aqueous|brine|water|sat\.", d["name"], re.I):
                d["role"] = "water"
            if any(k in d for k in ("mass_g", "moles", "volume_ml")):
                comps.append(d)
    pd = _comp(prod, pb, "product")
    pd.pop("moles", None)
    for m in prod.measurements:
        if m.type == pb.ProductMeasurement.AMOUNT and m.amount.WhichOneof("kind") == "mass":
            pd["mass_g"] = float(f"{_amount(m.amount)[0]:.6g}")
    y = next((m.percentage.value for m in prod.measurements if m.type == pb.ProductMeasurement.YIELD
              and m.WhichOneof("value") == "percentage"), None)
    comps.append(pd)
    seen, uniq = {}, []
    for d in comps:                                    # the same name twice: number the later ones
        n = seen.get(d["name"], 0) + 1
        seen[d["name"]] = n
        if n > 1:
            d["name"] = f"{d['name']} ({d['role']} {n})"
        uniq.append(d)
    t = rx.conditions.temperature
    out = {"name": f"{pd['name']} ({rx.provenance.patent})", "reaction_class": cls,
           "reported_yield_percent": round(y, 1) if y else None,
           "temperature_c": t.setpoint.value if t.HasField("setpoint") and t.setpoint.units == 1 else None,
           "time_h": rx.outcomes[0].reaction_time.value if rx.outcomes[0].HasField("reaction_time")
           and rx.outcomes[0].reaction_time.units == 1 else None,
           "workup_steps": steps,
           "source": {"entry": "benchmark", "ord_reaction_id": rx.reaction_id, "patent": rx.provenance.patent},
           "components": uniq}
    return out, rx.notes.procedure_details


def main():
    import pyarrow.parquet as pq
    from ord_schema.proto import reaction_pb2 as pb
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/ord_raw")
    ap.add_argument("ids", nargs="+")
    a = ap.parse_args()
    want = set(a.ids)
    for path in sorted(Path(a.raw).glob("*.parquet")):
        if not want:
            break
        f = pq.ParquetFile(path)
        for batch in f.iter_batches(batch_size=5000):
            ids = batch.column(0).to_pylist()
            for i, rid in enumerate(ids):
                if rid in want:
                    rx = pb.Reaction()
                    rx.ParseFromString(batch.column(1)[i].as_py())
                    rec, text = convert(rx, pb)
                    print(f"# ===== {rid}\n# " + "\n# ".join(re.findall(r".{1,150}(?:\s|$)", text)))
                    print(yaml.safe_dump({"reaction": rec}, sort_keys=False, allow_unicode=True, width=150))
                    want.discard(rid)


if __name__ == "__main__":
    main()
