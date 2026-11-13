"""Reports (JSON, CSV, PDF) and the feedback store."""

import csv
import io
import json

import pytest

import gms
from gms import diagnosis, examples, feedback, report, scoring, suggest


@pytest.fixture(scope="module")
def analysis():
    from gms.chem import hazard
    hazard.set_store(hazard.HazardStore(online=False))
    r, s, _ = scoring.prepare_and_score(examples.load("example-redam-dce"), online=False)
    return r, s, diagnosis.diagnose(r, s), suggest.suggest(r, s)


def test_json_report_has_provenance_assumptions_and_version(analysis):
    r, s, findings, cands = analysis
    shown = next(c for c in cands if c.status == "passed")
    rep = report.build(r, s, findings, cands, {shown.id: {"decision": "accept", "comment": "will try"}},
                       {shown.id: 0.7})
    data = json.loads(report.to_json(rep))                       # valid JSON, no NaN / Infinity
    assert data["app"]["version"] == gms.__version__ and data["app"]["generated_utc"]
    assert data["reaction"]["name"] == r.name and len(data["reaction"]["components"]) == len(r.components)
    assert {m["key"] for m in data["metrics"]} >= {"pmi", "ae", "ecoscale", "hazmat"}
    assert all("estimated" in m and "reference" in m for m in data["metrics"])
    assert data["estimates_and_assumptions"] == s.estimates and data["disclaimer"]
    pv = data["provenance"]
    assert "PubChem" in pv["hazard_data"]["source"] and "CHEM21" in pv["solvent_guide"]
    assert pv["precedent_data"]["license"].startswith("CC BY-SA") and "scoring" in pv["config"]
    sug = next(x for x in data["suggestions"] if x["id"] == shown.id)
    assert sug["decision"] == {"decision": "accept", "comment": "will try"}
    assert sug["sources"] and sug["check"]["passed"] and sug["breakeven"]["pmi"]["status"] in ("at", "always", "never")
    assert "not a claim that the yields are equal" in sug["yield_note"]
    assert sug["scenario"]["yield_fraction"] == 0.7 and sug["assumptions"]
    assert len(data["suggestions"]) + len(data["rejected_candidates"]) == len(cands)
    assert all(x["reason"] for x in data["rejected_candidates"])


def test_csv_and_pdf_reports(analysis):
    r, s, findings, cands = analysis
    rep = report.build(r, s, findings, cands)
    rows = list(csv.reader(io.StringIO(report.to_csv(rep).decode("utf-8"))))
    assert rows[0][0] == "Green Metrics Studio" and rows[0][1] == gms.__version__
    header = next(x for x in rows if x and x[0] == "Metric")
    assert header[3] == "Original is estimated" and any("Candidate at original yield" in h for h in header)
    assert any(x and x[0] == "Process mass intensity" for x in rows)
    assert any(x and x[0] == "Estimates and assumptions" for x in rows)
    pdf = report.to_pdf(rep)
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000


def test_infinite_values_do_not_break_the_exports():
    r = examples.load("example-redam-dce")
    r.product.mass_g = 0.0
    r, s, _ = scoring.prepare_and_score(r, online=False)
    rep = report.build(r, s, diagnosis.diagnose(r, s), suggest.suggest(r, s))
    assert next(m for m in rep["metrics"] if m["key"] == "pmi")["value"] == "infinite"
    json.loads(report.to_json(rep))
    assert report.to_pdf(rep).startswith(b"%PDF") and report.to_csv(rep)


def test_feedback_is_stored_and_exported(analysis, tmp_path):
    r, s, findings, cands = analysis
    db = tmp_path / "sub" / "feedback.db"
    c = next(x for x in cands if x.status == "passed")
    i1 = feedback.record(db, "session-a", r.to_dict(), "accept", " good idea ", c.summary())
    i2 = feedback.record(db, "session-a", r.to_dict(), "reject", "x" * 5000, c.summary())
    feedback.record(db, "session-b", r.to_dict(), "comment", "missing: 2-MeTHF")
    assert i2 == i1 + 1
    rows = feedback.rows(db)
    assert [x["decision"] for x in rows] == ["accept", "reject", "comment"]
    assert rows[0]["comment"] == "good idea" and len(rows[1]["comment"]) == feedback.MAX_COMMENT
    assert rows[0]["rule_id"] == c.rule_id and rows[0]["app_version"] == gms.__version__
    assert rows[0]["reaction_hash"] == feedback.reaction_hash(r.to_dict()) == rows[2]["reaction_hash"]
    assert json.loads(rows[0]["suggestion_json"])["check"]["passed"] and rows[2]["suggestion_json"] is None
    assert json.loads(rows[0]["reaction_json"])["name"] == r.name
    out = list(csv.DictReader(io.StringIO(feedback.to_csv(db).decode("utf-8"))))
    assert len(out) == 3 and out[0]["title"] == c.title
    with pytest.raises(ValueError):
        feedback.record(db, "s", r.to_dict(), "maybe")
    assert feedback.rows(tmp_path / "none.db") == []
