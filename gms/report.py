"""Reports: one analysis as JSON, CSV and PDF, with provenance, assumptions and the app version.

Every value that depends on an estimate carries an "estimated" marker in all three formats.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import math
from typing import Optional

import gms
from gms import breakeven, config, precedents
from gms.chem import hazard
from gms.metrics import registry

DISCLAIMER = (
    "Green Metrics Studio is a decision-support tool for trained chemists. It does not replace a risk "
    "assessment, the supplier's safety data sheet, or laboratory validation. Suggested changes are untested "
    "for your substrates: the tool does not predict their yield. Hazard data comes from public GHS "
    "classifications and can be incomplete or out of date.")


def _clean(v):
    """JSON-safe copy: inf / NaN become text, tuples become lists."""
    if isinstance(v, float) and not math.isfinite(v):
        return "undefined" if math.isnan(v) else ("infinite" if v > 0 else "-infinite")
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_clean(x) for x in v]
    return v


def _metric_rows(score) -> list[dict]:
    out = []
    for key in registry.display_order():
        m = registry.get(key)
        out.append({"key": key, "metric": m.label, "value": score.metrics.get(key), "unit": m.unit,
                    "rating": registry.rating(key, score.metrics.get(key)),
                    "estimated": key in score.estimated_metrics, "reference": m.reference})
    return out


def build(reaction, score, findings=None, candidates=None, decisions: Optional[dict] = None,
          scenario_yields: Optional[dict] = None, store=None) -> dict:
    """The full report as a plain dict (the JSON export). `decisions`: suggestion id ->
    {'decision', 'comment'}; `scenario_yields`: suggestion id -> yield fraction chosen by the user."""
    decisions, scenario_yields = decisions or {}, scenario_yields or {}
    seed_meta = hazard.store().seed().get("meta", {}) if store is None else store.seed().get("meta", {})
    rep = {
        "app": {"name": "Green Metrics Studio", "version": gms.__version__,
                "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")},
        "disclaimer": DISCLAIMER,
        "reaction": reaction.to_dict(),
        "metrics": _metric_rows(score),
        "totals": score.totals,
        "greenness": {**score.greenness, "note": "Provisional weights and limits (config/scoring.yaml)."},
        "ecoscale": score.ecoscale,
        "hazards": score.hazards,
        "hazard_summary": score.hazard_summary,
        "mass_contributions": score.contributions,
        "warnings": score.warnings,
        "estimates_and_assumptions": score.estimates,
        "diagnosis": [f.to_dict() for f in (findings or [])],
        "suggestions": [], "rejected_candidates": [],
        "provenance": {
            "formulas": "Tamargo et al. 2024 (Metrics of Green Chemistry and Sustainability); Van Aken et al. 2006 "
                        "(EcoScale). Metrics are computed from these formulas, never by a model.",
            "hazard_data": seed_meta,
            "solvent_guide": "CHEM21 selection guide, Prat et al., Green Chem. 2016, 18, 288 (CC BY 3.0)",
            "precedent_data": precedents.meta(),
            "substitution_rules": "gms/data/substitutions.yaml (each rule lists its references)",
            "config": {name: config.load(name) for name in ("metrics", "scoring", "hazard", "diagnosis", "suggestions")},
        },
    }
    for c in candidates or []:
        s = c.summary()
        s["decision"] = decisions.get(c.id)
        if c.status == "passed":
            s["yield_note"] = ("Changes are computed at the original yield. This is not a claim that the yields "
                               "are equal. The break-even yield is the lowest yield at which the candidate is "
                               "still at least as good as the original.")
            y = scenario_yields.get(c.id)
            if y is not None and c.reaction is not None:
                sc = breakeven.scenario(c.reaction, y, store)
                s["scenario"] = {"yield_fraction": y, "metrics": sc.metrics, "verdict": sc.greenness["label"]}
            rep["suggestions"].append(s)
        else:
            rep["rejected_candidates"].append(s)
    return _clean(rep)


def to_json(report: dict) -> bytes:
    return json.dumps(report, indent=2, default=str, allow_nan=False).encode("utf-8")


def to_csv(report: dict) -> bytes:
    """Flat table: one row per metric, one column per variant (original and each suggestion)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Green Metrics Studio", report["app"]["version"], report["app"]["generated_utc"]])
    w.writerow(["Reaction", report["reaction"].get("name", "")])
    w.writerow(["Verdict", report["greenness"]["label"], "score", report["greenness"]["score"], "provisional weights"])
    w.writerow([])
    sugg = report["suggestions"]
    w.writerow(["Metric", "Unit", "Original", "Original is estimated"]
               + [f"Candidate at original yield: {s['title']}" for s in sugg])
    for m in report["metrics"]:
        w.writerow([m["metric"], m["unit"], m["value"], "estimated" if m["estimated"] else ""]
                   + [(s["metrics"] or {}).get(m["key"]) for s in sugg])
    w.writerow([])
    w.writerow(["Suggestion", "Kind", "Confidence", "Evidence", "Break-even yield (PMI)", "Break-even yield (RME)",
                "Break-even yield (EcoScale)", "Decision", "Comment", "Assumptions (estimates)"])
    for s in sugg:
        be = s.get("breakeven") or {}
        fmt = lambda k: (be.get(k) or {}).get("status", "") + (                              # noqa: E731
            f" {100 * be[k]['yield']:.0f}%" if (be.get(k) or {}).get("status") == "at" else "")
        d = s.get("decision") or {}
        w.writerow([s["title"], s["kind"], s["confidence"], s["evidence_level"], fmt("pmi"), fmt("rme"),
                    fmt("ecoscale"), d.get("decision", ""), d.get("comment", ""), " | ".join(s.get("assumptions") or [])])
    w.writerow([])
    w.writerow(["Rejected candidate", "Reason"])
    for s in report["rejected_candidates"]:
        w.writerow([s["title"], s["reason"]])
    w.writerow([])
    w.writerow(["Estimates and assumptions"])
    for e in report["estimates_and_assumptions"]:
        w.writerow([e])
    w.writerow([])
    w.writerow(["Disclaimer", report["disclaimer"]])
    return buf.getvalue().encode("utf-8")


def _num(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "yes" if v else "no"
    if abs(v) >= 1000 or (v != 0 and abs(v) < 0.01):
        return f"{v:.3g}"
    return f"{v:.2f}"


def to_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
    h1, h2 = styles["Title"], ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=10)
    P = lambda text, st=body: Paragraph(escape(str(text)), st)          # noqa: E731

    def table(rows, widths=None, header=True):
        t = Table([[c if hasattr(c, "wrap") else P(c, small) for c in r] for r in rows], colWidths=widths, repeatRows=1)
        style = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f1ec")))
        t.setStyle(TableStyle(style))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title="Green Metrics Studio report")
    rx, g = report["reaction"], report["greenness"]
    el = [P("Green Metrics Studio report", h1),
          P(f"Reaction: {rx.get('name') or 'unnamed'}"),
          P(f"Version {report['app']['version']}, generated {report['app']['generated_utc']} (UTC)", small),
          Spacer(1, 6),
          P(f"Verdict: {g['label']}" + (f" (score {g['score']:.2f} of 1)" if isinstance(g["score"], (int, float)) else ""), h2),
          P("The verdict uses provisional weights and limits (config/scoring.yaml). It is computed from the "
            "deterministic metrics below, not by a model.", small)]
    for title, items in (("Main reasons", g["reasons"]), ("Limits on the verdict", g["caps"]), ("Notes", g["notes"])):
        if items:
            el.append(P(title + ": " + " ".join(f"({i + 1}) {x}" for i, x in enumerate(items))))

    el.append(P("Metrics", h2))
    el.append(table([["Metric", "Value", "Unit", "Rating", "Estimated?"]] + [
        [m["metric"], _num(m["value"]), m["unit"], m["rating"], "estimated" if m["estimated"] else ""]
        for m in report["metrics"]], [62 * mm, 26 * mm, 20 * mm, 26 * mm, 26 * mm]))
    if report["estimates_and_assumptions"]:
        el.append(P("Estimates and assumptions", h2))
        for e in report["estimates_and_assumptions"]:
            el.append(P("- " + e))
    if report["warnings"]:
        el.append(P("Input checks", h2))
        for w in report["warnings"]:
            el.append(P("- " + w))

    el.append(P("Materials", h2))
    haz = {h["name"]: h for h in report["hazards"]}
    rows = [["Material", "Role", "Mass (g)", "Hazard statements", "Scores H/S/E", "Source of mass"]]
    for c in rx["components"]:
        h = haz.get(c["name"], {})
        rows.append([c["name"], c["role"].replace("_", " "), _num(c.get("mass_g")),
                     ", ".join(h.get("h_codes", [])) or (h.get("status", "") if h else ""),
                     "/".join(str(h.get(d, "?")) for d in ("health", "safety", "environment")) if h else "",
                     ("ESTIMATE: " if c.get("estimated") else "") + (c.get("note") or "as entered")])
    el.append(table(rows, [38 * mm, 24 * mm, 16 * mm, 40 * mm, 18 * mm, 42 * mm]))

    if report["ecoscale"]:
        e = report["ecoscale"]
        el.append(P("EcoScale", h2))
        el.append(table([["Yield", "Price", "Safety", "Set-up", "Temperature/time", "Workup", "Total penalty", "Score"],
                         [_num(e["penalties"][k]) for k in ("yield", "price", "safety", "setup", "temp_time", "workup")]
                         + [_num(e["total_penalty"]), _num(e["score"])]]))
    if report["diagnosis"]:
        el.append(P("Diagnosis (gain of an idealised fix, computed by the calculator)", h2))
        el.append(table([["Finding", "What if", "PMI gain", "EcoScale gain", "Score gain"]] + [
            [f["title"], f["what_if"], _num(f["gains"].get("pmi")), _num(f["gains"].get("ecoscale")),
             _num(f.get("greenness_gain"))] for f in report["diagnosis"]],
            [56 * mm, 62 * mm, 20 * mm, 22 * mm, 18 * mm]))

    el.append(P("Greener alternatives that passed the no-regression check", h2))
    if not report["suggestions"]:
        el.append(P("None."))
    for s in report["suggestions"]:
        el.append(P(f"{s['title']}  [{s['kind'].replace('_', ' ')}; confidence: {s['confidence']}]",
                    ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", spaceBefore=6)))
        el.append(P(f"Evidence: {s['evidence_level'].replace('_', ' ')} ({s['confidence_reason']}). Rule: {s['rule_id']}."))
        for src in s.get("sources") or []:
            el.append(P(f"Reference: {src.get('citation', '')}" + (f", doi:{src['doi']}" if src.get("doi") else "")
                        + ("" if src.get("verified") else " (content not verified)"), small))
        p = s.get("precedent") or {}
        el.append(P("Precedents: " + (f"{p.get('n')} of {p.get('n_class')} reactions of this class in the dataset"
                                      if p.get("available") and "n" in p else p.get("reason", "not available")), small))
        if s.get("caveats"):
            el.append(P("Caveats: " + s["caveats"], small))
        if s.get("assumptions"):
            el.append(P("ESTIMATES in this candidate: " + "; ".join(s["assumptions"]), small))
        ch = s["changes_at_original_yield"]
        el.append(P("Change at the original yield (not a claim that the yields are equal): " + ", ".join(
            f"{registry.get(k).short} {v:+.3g}" for k, v in ch.items() if isinstance(v, (int, float)) and abs(v) > 1e-9)
            or "Change at the original yield: none", small))
        be = s.get("breakeven") or {}
        txt = []
        for k, label in (("pmi", "PMI"), ("rme", "RME"), ("ecoscale", "EcoScale")):
            b = be.get(k) or {}
            txt.append(f"{label}: " + {"at": f"{100 * (b.get('yield') or 0):.0f}%", "always": "better at any yield",
                                       "never": "not reachable"}.get(b.get("status"), "n/a"))
        el.append(P("Break-even yield: " + "; ".join(txt), small))
        if s.get("scenario"):
            sc = s["scenario"]
            el.append(P(f"Scenario chosen by the user: {100 * sc['yield_fraction']:.0f}% yield gives PMI "
                        f"{_num(sc['metrics'].get('pmi'))}, EcoScale {_num(sc['metrics'].get('ecoscale'))}, "
                        f"verdict {sc['verdict']}.", small))
        d = s.get("decision")
        if d:
            el.append(P(f"Chemist's decision: {d.get('decision')}" + (f". Comment: {d['comment']}" if d.get("comment") else ""), small))
    if report["rejected_candidates"]:
        el.append(P("Candidates rejected by the no-regression check", h2))
        el.append(table([["Candidate", "Reason"]] + [[s["title"], s["reason"]] for s in report["rejected_candidates"]],
                        [70 * mm, 108 * mm]))
    el.append(P("Provenance", h2))
    pv = report["provenance"]
    el.append(P(pv["formulas"], small))
    hz = pv["hazard_data"] or {}
    el.append(P(f"Hazard data: {hz.get('source', 'PubChem')} Bundled data built {hz.get('built', 'n/a')}; other "
                "compounds are looked up at run time.", small))
    el.append(P(f"Solvent guide: {pv['solvent_guide']}.", small))
    pm = pv["precedent_data"] or {}
    el.append(P(f"Precedent data: {pm.get('name', 'none')} ({pm.get('license', '')}), built {pm.get('built', 'n/a')}.", small))
    el.append(Spacer(1, 6))
    el.append(P(report["disclaimer"], small))
    doc.build(el)
    return buf.getvalue()
