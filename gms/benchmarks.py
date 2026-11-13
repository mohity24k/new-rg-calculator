"""Benchmark: reactions for which a greener alternative is known from the literature
(benchmarks/cases/*.yaml). For each known alternative the tool's result is one of

    suggested       generated and passed the no-regression check (with its rank)
    rejected        generated, but the no-regression check rejected it (with the reason)
    not generated   the tool has no rule that produces it
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from gms import config, examples, scoring, suggest
from gms.chem.structure import canon, mol_props
from gms.metrics import mass as mm
from gms.model import Reaction

CASES_DIR = config.ROOT / "benchmarks" / "cases"


def load_cases(directory: Optional[Path] = None) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((directory or CASES_DIR).glob("*.yaml"))]


def _key(smiles: str) -> str:
    return ".".join(sorted(filter(None, (canon(f) for f in smiles.split(".")))))


def _evaluate(reaction: Reaction, known: list[dict], store=None, online: bool = False) -> tuple[object, list[dict]]:
    r, score, _ = scoring.prepare_and_score(reaction, store, online=online)
    cands = suggest.suggest(r, score, store)
    passed = [c for c in cands if c.status == "passed"]
    out = []
    for k in known:
        want = {_key(s) for s in k["replacement_smiles"]}
        hits = [c for c in cands if c.kind == k["kind"] and c.target == k["target"]
                and c.replacement_smiles and _key(c.replacement_smiles) in want]
        ok = [c for c in hits if c.status == "passed"]
        if ok:
            best = min(ok, key=passed.index)
            res = {"outcome": "suggested", "rank": passed.index(best) + 1, "of": len(passed), "title": best.title,
                   "confidence": best.confidence, "detail": ""}
        elif hits:
            res = {"outcome": "rejected", "title": hits[0].title, "confidence": hits[0].confidence,
                   "detail": "; ".join(dict.fromkeys(h.reason for h in hits))}
        else:
            res = {"outcome": "not generated", "title": "", "confidence": "", "detail": "no rule produces this alternative"}
        out.append({**k, **res})
    return score, out


def atom_economy_of_route(route: dict, product: dict) -> float:
    reactants = [mm.Reactant(name=c["name"], mw=mol_props(c["smiles"])[0], moles=0.0, mass=0.0,
                             stoich_ratio=float(c.get("coefficient", 1))) for c in route["reactants"]]
    return mm.atom_economy(mol_props(product["smiles"])[0], reactants)


def run(directory: Optional[Path] = None, store=None, online: bool = False) -> list[dict]:
    """Run every case. Each result: {'id', 'title', 'kind', 'summary', 'results': [...]}."""
    out = []
    for case in load_cases(directory):
        if case.get("kind") == "calculator_check":
            rows = []
            for route in case["routes"]:
                ae = atom_economy_of_route(route, case["product"])
                pub = route["published_atom_economy_percent"]
                rows.append({"description": f"{route['name']}: atom economy", "outcome":
                             "matches" if abs(ae - pub) <= 1.0 else "differs", "computed": round(ae, 1), "published": pub})
            out.append({"id": case["id"], "title": case["title"], "kind": "calculator_check", "results": rows})
            continue
        if "reaction" in case:
            score, rows = _evaluate(Reaction.from_dict(case["reaction"]), case["known_greener"], store, online)
            summary = {"verdict": score.greenness["label"], "pmi": score.metrics["pmi"],
                       "ecoscale": score.metrics["ecoscale"], "unknown_hazard": score.hazard_summary["unknown"]}
        else:                                           # known alternatives tested on packaged examples
            rows, summary = [], {}
            for k in case["known_greener"]:
                _, res = _evaluate(examples.load(k["example"]), [k], store, online)
                rows += res
        out.append({"id": case["id"], "title": case["title"], "kind": "suggestions", "summary": summary, "results": rows})
    return out


def counts(results: list[dict]) -> dict:
    c = {"suggested": 0, "rejected": 0, "not generated": 0, "top3": 0, "total": 0}
    for case in results:
        if case["kind"] != "suggestions":
            continue
        for r in case["results"]:
            c["total"] += 1
            c[r["outcome"]] += 1
            c["top3"] += r["outcome"] == "suggested" and r["rank"] <= 3
    return c


def to_markdown(results: list[dict], version: str) -> str:
    c = counts(results)
    lines = [
        "# Benchmark results", "",
        f"Produced by `python scripts/run_benchmarks.py` with Green Metrics Studio {version}. Do not edit by hand.", "",
        "Each case is a reaction for which a greener alternative is known from the literature. The table says "
        "whether the tool finds that alternative. Cases and sources: `benchmarks/cases/`.", "",
        f"**Summary:** {c['total']} known greener alternatives. The tool suggests {c['suggested']} "
        f"({c['top3']} of them among its first three suggestions). It generates {c['rejected']} more but its own "
        f"no-regression check rejects them. It does not generate {c['not generated']}.", "",
        "A rejection is not always a miss. The no-regression check is strict by design: it rejects a "
        "literature-backed swap when the GHS hazard data or a metric gets worse. The reason is given for each one.", "",
    ]
    for case in results:
        lines += [f"## {case['title']}", "", f"Case file: `benchmarks/cases/{case['id']}.yaml`", ""]
        if case["kind"] == "calculator_check":
            lines += ["| Check | Computed | Published | Result |", "|---|---|---|---|"]
            lines += [f"| {r['description']} | {r['computed']}% | {r['published']}% | {r['outcome']} |" for r in case["results"]]
            lines.append("")
            continue
        s = case.get("summary") or {}
        if s:
            lines.append(f"Original reaction: verdict {s['verdict']}, PMI {s['pmi']:.3g} (stated amounts only), "
                         f"EcoScale {s['ecoscale']:.3g}."
                         + (f" No hazard data for: {', '.join(s['unknown_hazard'])}." if s["unknown_hazard"] else ""))
            lines.append("")
        lines += ["| Known greener alternative | Source | Result | Detail |", "|---|---|---|---|"]
        for r in case["results"]:
            src = r["source"]["citation"] + (f", doi:{r['source']['doi']}" if r["source"].get("doi") else "")
            if r["outcome"] == "suggested":
                res = f"**suggested** (number {r['rank']} of {r['of']}, confidence {r['confidence']})"
                detail = r["title"]
            elif r["outcome"] == "rejected":
                res, detail = "generated, **rejected by the check**", r["detail"]
            else:
                res, detail = "**not generated**", r["detail"]
            lines.append(f"| {r['description']} | {src} | {res} | {detail} |")
        lines.append("")
    return "\n".join(lines)
