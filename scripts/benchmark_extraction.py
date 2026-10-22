#!/usr/bin/env python
"""
Measure extraction speed (and, with a gold file, accuracy) on your own server before a big run.

    export VLM_API_KEY=...
    python scripts/benchmark_extraction.py --pdf si1.pdf --pdf si2.pdf \
        --base-url http://localhost:8000/v1 --concurrency 1,8,32 --classic-pages 5 \
        --gold benchmarks/gold.json --report benchmark_report.json

--concurrency   one run of the new pipeline per value (1 = one request at a time)
--classic-pages also time the current app pipeline (every page as a 2000 px image, one at a
                time) on the first N pages, and extrapolate it to all pages
--gold          hand-checked answers (see benchmarks/gold_template.json) -> recall / accuracy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import pdf_extraction as px  # noqa: E402
from modules.extraction import config as cfgmod  # noqa: E402
from modules.extraction import enrich, pipeline  # noqa: E402
from modules.extraction.engines import HttpEngine  # noqa: E402

# ---------------------------------------------------------------------------
# Accuracy against a gold file
# ---------------------------------------------------------------------------


def _key(name: str) -> str:
    """Same compound written two ways ("EtOAc" / "ethyl acetate") -> same key, offline."""
    s = enrich.resolve(name, online=False)
    return s or px._norm(px._clean(name))


def _base(q, unit):
    """(value in g / mL / mol, dimension) or (None, None)."""
    if q is None:
        return None, None
    u = px._unit(unit)
    for table, dim in ((px.MASS_UNITS, "mass"), (px.VOL_UNITS, "vol"), (px.MOL_UNITS, "mol")):
        if u in table:
            return q * table[u], dim
    return None, None


def _match(gold: dict, preds: list[dict]):
    lab = px._norm(str(gold.get("label") or ""))
    if lab:
        hit = next((p for p in preds if px._norm(str(p.get("label") or "")) == lab), None)
        if hit:
            return hit
    prod = gold.get("product")
    if prod:
        k = _key(prod)
        return next((p for p in preds if any(c["role"] == "product" and _key(c["name"]) == k
                                             for c in p["components"])), None)
    return None


def score(gold_rows: list[dict], preds_by_file: dict[str, list[dict]], rel_tol: float = 0.02) -> dict:
    found = comp_total = comp_found = role_ok = qty_total = qty_ok = y_total = y_ok = 0
    matched_ids = set()
    misses = []
    for g in gold_rows:
        preds = [p for p in preds_by_file.get(g["file"], []) if p.get("record_type") != "general_procedure"]
        p = _match(g, preds)
        if p is None:
            misses.append(f"{g['file']}: {g.get('label') or g.get('product')}")
            comp_total += len(g.get("components", []))
            continue
        found += 1
        matched_ids.add(p["reaction_id"])
        pc = {_key(c["name"]): c for c in p["components"]}
        for gc in g.get("components", []):
            comp_total += 1
            c = pc.get(_key(gc["name"]))
            if c is None:
                continue
            comp_found += 1
            role_ok += c["role"] == gc.get("role")
            gv, gd = _base(gc.get("quantity"), gc.get("unit"))
            if gv is not None:
                qty_total += 1
                pv, pd = _base(c.get("quantity"), c.get("unit"))
                qty_ok += pd == gd and pv is not None and abs(pv - gv) <= rel_tol * abs(gv)
        if g.get("yield_percent") is not None:
            y_total += 1
            y_ok += p.get("yield_percent") is not None and abs(p["yield_percent"] - g["yield_percent"]) <= 1
    n_pred = sum(1 for ps in preds_by_file.values() for p in ps if p.get("record_type") != "general_procedure")
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    return {"reactions_in_gold": len(gold_rows), "reaction_recall_pct": pct(found, len(gold_rows)),
            "reaction_precision_pct": pct(len(matched_ids), n_pred),
            "component_recall_pct": pct(comp_found, comp_total),
            "role_accuracy_pct": pct(role_ok, comp_found),
            "quantity_accuracy_pct": pct(qty_ok, qty_total), "yield_accuracy_pct": pct(y_ok, y_total),
            "missed": misses}

# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", action="append", required=True)
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    ap.add_argument("--api-key", help="default: $VLM_API_KEY")
    ap.add_argument("--concurrency", default="1,8,32")
    ap.add_argument("--classic-pages", type=int, default=0)
    ap.add_argument("--gold")
    ap.add_argument("--config")
    ap.add_argument("--report")
    a = ap.parse_args()
    cfg = cfgmod.load_config(a.config)
    key = a.api_key or os.environ.get("VLM_API_KEY", "EMPTY")
    docs = [(Path(p).name, Path(p).read_bytes()) for p in a.pdf]
    total_pages = sum(px.page_count(d) for _, d in docs)
    report = {"pdfs": [n for n, _ in docs], "pages": total_pages, "runs": []}

    if a.classic_pages:
        name, data = docs[0]
        n = min(a.classic_pages, px.page_count(data))
        t0 = time.perf_counter()
        px.extract_document(data, range(n), a.base_url, a.model, key)
        per_page = (time.perf_counter() - t0) / n
        report["classic"] = {"pages_timed": n, "seconds_per_page": round(per_page, 2),
                             "extrapolated_seconds_all_pages": round(per_page * total_pages, 1)}
        print(f"classic app pipeline: {per_page:.1f} s/page -> ~{per_page * total_pages / 60:.1f} min "
              f"for all {total_pages} pages")

    preds_last = {}
    for conc in [int(x) for x in a.concurrency.split(",") if x.strip()]:
        eng = HttpEngine(a.base_url, a.model, key, concurrency=conc, timeout=cfg["server"]["timeout_s"],
                         retries=cfg["server"]["retries"])
        t0 = time.perf_counter()
        sent = units = ptok = ctok = n_rx = 0
        preds = {}
        for name, data in docs:
            out = pipeline.extract_pdf(data, eng, cfg, file=name)
            sent += len({p for u in out["units"] for p in u["pages"]})
            units += len(out["units"])
            ptok += sum(r["prompt_tokens"] for r in out["results"])
            ctok += sum(r["completion_tokens"] for r in out["results"])
            preds[name] = [enrich.enrich_reaction(r, cfg, online=False) for r in out["reactions"]]
            n_rx += len([r for r in out["reactions"] if r.get("record_type") != "general_procedure"])
        dt = time.perf_counter() - t0
        run = {"concurrency": conc, "seconds": round(dt, 1), "pages_sent": sent, "units": units,
               "reactions": n_rx, "prompt_tokens": ptok, "completion_tokens": ctok,
               "seconds_per_input_page": round(dt / total_pages, 3),
               "output_tokens_per_s": round(ctok / dt, 1) if dt else None,
               "structured_output": eng.use_schema}
        report["runs"].append(run)
        preds_last = preds
        print(f"concurrency {conc:3d}: {dt:7.1f} s, {sent}/{total_pages} pages sent, {units} requests, "
              f"{n_rx} reactions, {run['output_tokens_per_s']} output tok/s")

    if report.get("classic") and report["runs"]:
        best = min(r["seconds"] for r in report["runs"])
        report["speedup_vs_classic"] = round(report["classic"]["extrapolated_seconds_all_pages"] / best, 1)
        print(f"speed-up vs the current app pipeline: ~{report['speedup_vs_classic']}x")

    if a.gold:
        gold = json.loads(Path(a.gold).read_text(encoding="utf-8"))["reactions"]
        report["accuracy"] = score(gold, preds_last)
        print(json.dumps(report["accuracy"], indent=2))

    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"report written to {a.report}")


if __name__ == "__main__":
    main()
