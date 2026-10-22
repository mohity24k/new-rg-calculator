"""
PDF(s) -> reaction records, with checkpoints.

    plan_paper (page filter, chunks)  ->  HttpEngine (many requests at once)
        ->  assemble_paper (parse, normalize, general procedures, dedupe)  ->  JSONL files

Output folder (safe to point at Google Drive):
    papers.jsonl     one line per finished paper: the commit marker used for resume
    reactions.jsonl  one line per reaction (role-tagged; structures/masses added by `enrich`)
    tables.jsonl     optimization / screening tables
    raw/<paper_id>.jsonl   every model answer, so records can be rebuilt without the GPU (`reparse`)

A paper is written only when all its requests reached the server. If the server goes away
(Colab disconnect, tunnel restart) the run stops and the next run continues where it stopped.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from modules import pdf_extraction as px
from modules.extraction import schema as sc
from modules.extraction.engines import TRANSPORT, HttpEngine, Result, ServerUnavailable, build_request
from modules.extraction.pages import paper_id_for, plan_paper

PIPELINE_VERSION = "dataset-2026-10-22.1"


def parse_unit(kind: str, text: str) -> tuple[list[dict], str]:
    """Model answer -> (reactions or tables, parse status)."""
    data, status = px.parse_json_status(text)
    if data is None:
        return [], status
    items = sc.tables_from_answer(data) if kind == "table" else sc.reactions_from_answer(data)
    return items, status


def assemble_paper(meta: dict, units: list[dict], results: list[dict]) -> dict:
    """Pure function: plan + raw answers -> {'reactions', 'tables', 'unit_status'}.

    `units` / `results` are plain dicts (as stored in raw/<paper_id>.jsonl)."""
    pid, doi = meta["paper_id"], meta.get("doi")
    group = doi or pid
    reactions, tables, unit_status = [], [], []
    for u, r in zip(units, results):
        st = {"unit_id": u["unit_id"], "kind": u["kind"], "mode": u["mode"], "pages": u["pages"],
              "ok": r["ok"], "error": r.get("error", "")}
        if r["ok"]:
            items, st["parse"] = parse_unit(u["kind"], r["text"])
            st["n_items"] = len(items)
            src = {"paper_id": pid, "doi": doi, "file": meta.get("file"), "pages": u["pages"],
                   "unit_id": u["unit_id"], "mode": u["mode"]}
            for it in items:
                it["source"] = dict(src)
                (tables if u["kind"] == "table" else reactions).append(it)
        unit_status.append(st)
    reactions = sc.dedupe_reactions(sc.resolve_general_procedures(reactions))
    for i, r in enumerate(reactions):
        r["reaction_id"] = f"{pid}#r{i + 1}"
        r["group"] = group
        r["record_type"] = "general_procedure" if r.get("procedure_label") else "reaction"
        r["schema_version"] = sc.SCHEMA_VERSION
        r["pipeline_version"] = PIPELINE_VERSION
    for i, t in enumerate(tables):
        t["table_id"] = f"{pid}#t{i + 1}"
        t["group"] = group
    return {"reactions": reactions, "tables": tables, "unit_status": unit_status}


def _unit_dict(u) -> dict:
    return {"unit_id": u.unit_id, "kind": u.kind, "mode": u.mode, "pages": u.pages,
            "chars": len(u.text)}


def extract_pdf(pdf_bytes: bytes, engine: HttpEngine, cfg: dict, page_indices: Optional[Iterable[int]] = None,
                file: Optional[str] = None,
                on_progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """One PDF, end to end (used by the app and the benchmark). Raises ServerUnavailable if no
    request reached the server."""
    t0 = time.perf_counter()
    infos, units, meta = plan_paper(pdf_bytes, cfg, page_indices)
    meta["file"] = file
    reqs = [build_request(u, cfg["server"]["max_tokens"]) for u in units]
    done = [0]

    def tick(_):
        done[0] += 1
        if on_progress:
            on_progress(done[0], len(reqs))

    results = engine.run(reqs, tick)
    if results and all(r.error_kind == TRANSPORT for r in results):
        raise ServerUnavailable(results[-1].error)
    res_d = [asdict(r) for r in results]
    out = assemble_paper(meta, [_unit_dict(u) for u in units], res_d)
    out.update(meta=meta, pages=[asdict(i) for i in infos], units=[_unit_dict(u) for u in units],
               results=res_d, seconds=time.perf_counter() - t0)
    return out

# ---------------------------------------------------------------------------
# Corpus runner (checkpointed)
# ---------------------------------------------------------------------------


def _append(path: Path, rows: Iterable[dict]):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:      # a line cut off by a crash
                continue
    return out


def _cleanup_partial(out: Path, done: set[str]):
    """Drop rows of papers whose commit line (papers.jsonl) was never written."""
    for name, key in (("reactions.jsonl", "source"), ("tables.jsonl", "source")):
        p = out / name
        rows = read_jsonl(p)
        keep = [r for r in rows if (r.get(key) or {}).get("paper_id") in done]
        if len(keep) != len(rows):
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            os.replace(tmp, p)


def load_manifest(path: Optional[str]) -> dict:
    """Optional CSV with columns file, doi[, group]: links an SI to its paper for group splits."""
    if not path:
        return {}
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        return {Path(r["file"]).name: r for r in csv.DictReader(f) if r.get("file")}


def run_corpus(pdf_paths: list[str], out_dir: str, engine: HttpEngine, cfg: dict,
               batch_papers: int = 8, manifest: Optional[dict] = None,
               log: Callable[[str], None] = print) -> dict:
    """Extract every PDF not yet in out_dir/papers.jsonl. Requests of `batch_papers` papers are
    sent together so the GPU stays busy even when a paper has only a few chunks."""
    out = Path(out_dir)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    done = {p["paper_id"] for p in read_jsonl(out / "papers.jsonl")}
    _cleanup_partial(out, done)
    manifest = manifest or {}
    todo, seen = [], set(done)
    for path in pdf_paths:
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            log(f"skip {path}: {e}")
            continue
        pid = paper_id_for(data)
        if pid in seen:
            continue
        seen.add(pid)
        todo.append((path, pid))
    log(f"{len(done)} papers already done, {len(todo)} to extract")
    stats = {"papers": 0, "reactions": 0, "tables": 0, "pages": 0, "pages_sent": 0, "units": 0,
             "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "stopped": False}
    t_start = time.perf_counter()

    for b in range(0, len(todo), batch_papers):
        batch = []
        for path, pid in todo[b:b + batch_papers]:
            try:
                data = Path(path).read_bytes()
                infos, units, meta = plan_paper(data, cfg, paper_id=pid)
            except Exception as e:  # unreadable PDF: record it so it is not retried forever
                _append(out / "papers.jsonl", [{"paper_id": pid, "file": Path(path).name,
                                                "status": f"unreadable: {e}", "pipeline_version": PIPELINE_VERSION}])
                log(f"unreadable {path}: {e}")
                continue
            meta["file"] = Path(path).name
            m = manifest.get(Path(path).name) or {}
            if m.get("doi"):
                meta["doi"] = m["doi"]
            if m.get("group"):
                meta["group"] = m["group"]
            batch.append((meta, infos, units))
        reqs, owner = [], []
        for k, (meta, infos, units) in enumerate(batch):
            for u in units:
                reqs.append(build_request(u, cfg["server"]["max_tokens"]))
                owner.append(k)
        t0 = time.perf_counter()
        results: list[Result] = engine.run(reqs)
        dt = time.perf_counter() - t0
        if results and all(r.error_kind == TRANSPORT for r in results):
            log(f"server unavailable ({results[-1].error}); stopping. Run again to resume.")
            stats["stopped"] = True
            break
        for k, (meta, infos, units) in enumerate(batch):
            res = [r for r, o in zip(results, owner) if o == k]
            if any(r.error_kind == TRANSPORT for r in res):
                log(f"{meta['file']}: some requests did not reach the server; it will be retried next run")
                continue
            res_d = [asdict(r) for r in res]
            unit_d = [_unit_dict(u) for u in units]
            asm = assemble_paper(meta, unit_d, res_d)
            if meta.get("group"):
                for r in asm["reactions"] + asm["tables"]:
                    r["group"] = meta["group"]
            pid = meta["paper_id"]
            raw_path = out / "raw" / f"{pid}.jsonl"
            if raw_path.exists():
                raw_path.unlink()
            _append(raw_path, [{"meta": meta, "units": unit_d, "results": res_d,
                                "pages": [asdict(i) for i in infos]}])
            _append(out / "reactions.jsonl", asm["reactions"])
            _append(out / "tables.jsonl", asm["tables"])
            labels: dict[str, int] = {}
            for i in infos:
                labels[i.label] = labels.get(i.label, 0) + 1
            failed = [s["unit_id"] for s in asm["unit_status"] if not s["ok"]]
            sent_pages = sorted({p for u in units for p in u.pages})
            _append(out / "papers.jsonl", [{
                "paper_id": pid, "file": meta["file"], "doi": meta.get("doi"), "group": meta.get("group") or meta.get("doi") or pid,
                "n_pages": meta["n_pages"], "pages_sent": len(sent_pages), "page_labels": labels,
                "units": len(units), "failed_units": failed,
                "reactions": len(asm["reactions"]), "tables": len(asm["tables"]),
                "prompt_tokens": sum(r.prompt_tokens for r in res),
                "completion_tokens": sum(r.completion_tokens for r in res),
                "batch_seconds": round(dt, 2),
                "status": "done" if not failed else "done_with_errors",
                "pipeline_version": PIPELINE_VERSION,
            }])
            stats["papers"] += 1
            stats["reactions"] += len(asm["reactions"])
            stats["tables"] += len(asm["tables"])
            stats["pages"] += meta["n_pages"]
            stats["pages_sent"] += len(sent_pages)
            stats["units"] += len(units)
            stats["prompt_tokens"] += sum(r.prompt_tokens for r in res)
            stats["completion_tokens"] += sum(r.completion_tokens for r in res)
        stats["seconds"] = time.perf_counter() - t_start
        log(f"[{min(b + batch_papers, len(todo))}/{len(todo)}] papers {stats['papers']}, "
            f"reactions {stats['reactions']}, {stats['pages_sent']}/{stats['pages']} pages sent, "
            f"{stats['seconds'] / 60:.1f} min")
    stats["seconds"] = time.perf_counter() - t_start
    return stats


def reparse(out_dir: str, log: Callable[[str], None] = print) -> dict:
    """Rebuild reactions.jsonl / tables.jsonl from raw/ (after a parser fix) without the GPU."""
    out = Path(out_dir)
    reactions, tables = [], []
    for raw in sorted((out / "raw").glob("*.jsonl")):
        for row in read_jsonl(raw):
            asm = assemble_paper(row["meta"], row["units"], row["results"])
            g = row["meta"].get("group")
            if g:
                for r in asm["reactions"] + asm["tables"]:
                    r["group"] = g
            reactions += asm["reactions"]
            tables += asm["tables"]
    for name, rows in (("reactions.jsonl", reactions), ("tables.jsonl", tables)):
        tmp = out / (name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, out / name)
    log(f"rebuilt {len(reactions)} reactions and {len(tables)} tables")
    return {"reactions": len(reactions), "tables": len(tables)}
