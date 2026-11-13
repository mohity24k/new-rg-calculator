"""Build gms/data/ghs_seed.json: GHS hazard statements for the compounds the app knows by
name (abbreviation table, CHEM21 solvents, substitution rules, examples), fetched from PubChem.

The app uses this file first, so common compounds need no network. Run it again to refresh:
    python scripts/build_hazard_seed.py
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gms import config  # noqa: E402
from gms.chem import hazard, names, solvents  # noqa: E402
from gms.chem.structure import canon  # noqa: E402


def compounds() -> dict[str, list[str]]:
    """canonical SMILES -> names."""
    out: dict[str, list[str]] = {}

    def add(name, smiles):
        s = canon(smiles)
        frags = [canon(f) for f in (smiles or "").split(".")]
        if len(frags) > 1 and all(f and solvents.lookup(f) for f in frags):
            return                                  # a solvent mixture: its parts are in the solvent table
        if s and name:
            out.setdefault(s, [])
            if name not in out[s]:
                out[s].append(name)

    for s in solvents.table().values():
        add(s.name, s.smiles)
    for name, smi in names.ALIASES.items():
        add(name, smi)
    sub = config.DATA_DIR / "substitutions.yaml"
    if sub.exists():
        for rule in yaml.safe_load(sub.read_text(encoding="utf-8"))["rules"]:
            t = rule.get("target", {})
            add(t.get("name"), t.get("smiles"))
            for rep in rule.get("replacements", []):
                add(rep.get("name"), rep.get("smiles"))
                for extra in (rep.get("adds") or []) + (rep.get("removes") or []):
                    add(extra.get("name"), extra.get("smiles"))
    extra = config.DATA_DIR / "seed_compounds.yaml"
    if extra.exists():
        for c in yaml.safe_load(extra.read_text(encoding="utf-8")):
            add(c["name"], c["smiles"])
    return out


def main():
    comp = compounds()
    old = json.loads(hazard.SEED_FILE.read_text("utf-8")) if hazard.SEED_FILE.exists() and "--refresh" not in sys.argv \
        else {"records": {}}
    records, name_index, failed = {}, {}, []
    for i, (smi, nms) in enumerate(sorted(comp.items()), 1):
        rec = old["records"].get(smi)
        if rec is None or rec["status"] == "no_data":
            try:
                rec = hazard.fetch(max(nms, key=len), smi)
            except names.LookupFailed as e:
                failed.append(f"{nms[0]}: {e}")
                continue
        rec["names"] = sorted(nms)
        records[smi] = rec
        for n in nms:
            name_index[names.norm(names.clean(n))] = smi
        if i % 25 == 0:
            print(f"{i}/{len(comp)}", flush=True)
    seed = {
        "meta": {
            "built": datetime.date.today().isoformat(),
            "source": "PubChem (https://pubchem.ncbi.nlm.nih.gov), GHS Classification section of each compound "
                      "record. EU harmonised classification (Regulation (EC) No 1272/2008) where present, "
                      "otherwise ECHA C&L Inventory aggregated notifications or another listed source.",
            "selection": config.load("hazard")["ghs"],
            "note": "Reference data for screening. Always check the supplier's safety data sheet.",
        },
        "records": records, "names": name_index,
    }
    hazard.SEED_FILE.write_text(json.dumps(seed, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    stat: dict[str, int] = {}
    for r in records.values():
        stat[r["status"]] = stat.get(r["status"], 0) + 1
    print(f"wrote {hazard.SEED_FILE}: {len(records)} compounds {stat}; {len(failed)} failed")
    for f in failed:
        print("  failed:", f)


if __name__ == "__main__":
    main()
