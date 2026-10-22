#!/usr/bin/env python
"""
Build the reaction dataset from a folder of PDFs.

    # 1) GPU step: extract (resumable; run it again after a disconnect)
    python scripts/build_dataset.py extract --pdf-dir pdfs/ --out data/run1 \
        --base-url http://localhost:8000/v1 --model Qwen/Qwen2.5-VL-7B-Instruct

    # 2) CPU step: structures, masses, validation flags -> reactions_enriched.jsonl
    python scripts/build_dataset.py enrich --out data/run1

    # after a parser fix: rebuild reactions.jsonl from the saved model answers (no GPU)
    python scripts/build_dataset.py reparse --out data/run1

    python scripts/build_dataset.py stats --out data/run1

The API key is read from the environment variable VLM_API_KEY (or --api-key).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.extraction import config as cfgmod  # noqa: E402
from modules.extraction import enrich, pipeline  # noqa: E402
from modules.extraction.engines import HttpEngine  # noqa: E402


def cmd_extract(a, cfg):
    pdfs = sorted(str(p) for p in Path(a.pdf_dir).rglob("*") if p.suffix.lower() == ".pdf")
    if a.limit:
        pdfs = pdfs[:a.limit]
    if not pdfs:
        sys.exit(f"no PDFs found in {a.pdf_dir}")
    s = cfg["server"]
    eng = HttpEngine(a.base_url, a.model, a.api_key or os.environ.get("VLM_API_KEY", "EMPTY"),
                     concurrency=a.concurrency or s["concurrency"], timeout=s["timeout_s"],
                     retries=s["retries"], use_schema=not a.no_schema)
    stats = pipeline.run_corpus(pdfs, a.out, eng, cfg, batch_papers=a.batch_papers,
                                manifest=pipeline.load_manifest(a.manifest))
    mins = stats["seconds"] / 60
    print(f"\n{stats['papers']} papers, {stats['reactions']} reactions, {stats['tables']} tables in {mins:.1f} min")
    if stats["pages"]:
        print(f"pages sent to the model: {stats['pages_sent']}/{stats['pages']} "
              f"({100 * stats['pages_sent'] / stats['pages']:.0f}%)")
    if stats["seconds"] and stats["papers"]:
        print(f"{stats['seconds'] / stats['papers']:.1f} s per paper, "
              f"{stats['completion_tokens'] / stats['seconds']:.0f} output tokens/s")
    if not eng.use_schema:
        print("note: the server does not support structured output; answers were repaired instead")
    if stats["stopped"]:
        sys.exit(2)


def cmd_enrich(a, cfg):
    out = Path(a.out)
    cache = a.cache or str(out / "smiles_cache.json")
    enrich.load_cache(cache)
    rows = pipeline.read_jsonl(out / "reactions.jsonl")
    tmp = out / "reactions_enriched.jsonl.tmp"
    t0 = time.perf_counter()
    with open(tmp, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(json.dumps(enrich.enrich_reaction(r, cfg, online=not a.offline), ensure_ascii=False) + "\n")
            if i % 50 == 0:
                enrich.save_cache(cache)
                print(f"{i}/{len(rows)} enriched ({time.perf_counter() - t0:.0f} s)")
    os.replace(tmp, out / "reactions_enriched.jsonl")
    enrich.save_cache(cache)
    print(f"{len(rows)} reactions -> {out / 'reactions_enriched.jsonl'}")


def cmd_reparse(a, cfg):
    pipeline.reparse(a.out)


def cmd_stats(a, cfg):
    out = Path(a.out)
    papers = pipeline.read_jsonl(out / "papers.jsonl")
    enriched = out / "reactions_enriched.jsonl"
    rx = pipeline.read_jsonl(enriched if enriched.exists() else out / "reactions.jsonl")
    tables = pipeline.read_jsonl(out / "tables.jsonl")
    status = collections.Counter(p.get("status") for p in papers)
    labels = collections.Counter()
    for p in papers:
        labels.update(p.get("page_labels") or {})
    real = [r for r in rx if r.get("record_type") != "general_procedure"]
    print(f"papers: {len(papers)} {dict(status)}")
    print(f"page labels: {dict(labels)}")
    print(f"reactions: {len(real)} (+ {len(rx) - len(real)} general procedures), "
          f"groups (DOI or paper): {len({r.get('group') for r in real})}")
    print(f"optimization tables: {len(tables)} with {sum(len(t['entries']) for t in tables)} entries")
    flags = collections.Counter(f["code"] for r in real for f in r.get("flags", []))
    for code, n in flags.most_common():
        print(f"  {code:36s} {n:6d}  ({100 * n / max(1, len(real)):.0f}%)")
    comp = [r["completeness"] for r in real if "completeness" in r]
    if comp:
        comp.sort()
        print(f"completeness (share of components with a mass): median {comp[len(comp) // 2]:.2f}, "
              f"fully complete {sum(c == 1 for c in comp)}/{len(comp)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to extraction.yaml (default: config/extraction.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--pdf-dir", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--base-url", default="http://localhost:8000/v1")
    e.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    e.add_argument("--api-key", help="default: $VLM_API_KEY")
    e.add_argument("--concurrency", type=int, help="requests in flight (default: config)")
    e.add_argument("--batch-papers", type=int, default=8, help="papers whose requests are sent together")
    e.add_argument("--manifest", help="CSV with columns file,doi[,group] (links SI files to their paper)")
    e.add_argument("--limit", type=int, help="only the first N PDFs (for a trial run)")
    e.add_argument("--no-schema", action="store_true", help="do not request structured output")
    n = sub.add_parser("enrich")
    n.add_argument("--out", required=True)
    n.add_argument("--offline", action="store_true", help="no PubChem lookups (alias table + OPSIN only)")
    n.add_argument("--cache", help="SMILES cache file (default: <out>/smiles_cache.json)")
    for name in ("reparse", "stats"):
        sub.add_parser(name).add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = cfgmod.load_config(a.config)
    {"extract": cmd_extract, "enrich": cmd_enrich, "reparse": cmd_reparse, "stats": cmd_stats}[a.cmd](a, cfg)


if __name__ == "__main__":
    main()
