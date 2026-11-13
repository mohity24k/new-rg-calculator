"""Break-even yields and the no-regression check."""

import pytest

from gms import breakeven, edits, examples, regression, scoring, suggest
from gms.model import Component


def test_worked_check_from_the_brief():
    """Candidate inputs 40 g, maximum product 8 g, original PMI 15.625 -> break-even yield 0.32."""
    assert breakeven.pmi_breakeven(40.0, 8.0, 15.625) == pytest.approx(0.32)


def test_rme_and_ecoscale_breakeven_formulas():
    # RME(y) = 100 y x 8 / 10 ; original 40 %  ->  y = 0.5
    assert breakeven.rme_breakeven(10.0, 8.0, 40.0) == pytest.approx(0.5)
    # EcoScale(y) = 100 - 50 (1 - y) - 20 ; original 60  ->  y = 0.6
    assert breakeven.ecoscale_breakeven(20.0, 60.0) == pytest.approx(0.6)
    assert breakeven.describe(0.32) == {"status": "at", "yield": 0.32}
    assert breakeven.describe(-0.2)["status"] == "always" and breakeven.describe(1.3)["status"] == "never"
    assert breakeven.describe(None)["status"] == "unknown"


@pytest.fixture(scope="module")
def amide():
    from gms.chem import hazard
    hazard.set_store(hazard.HazardStore(online=False))
    r, s, _ = scoring.prepare_and_score(examples.load("example-amide-hatu-dmf"), online=False)
    return r, s, suggest.suggest(r, s)


def _by_title(cands, text):
    return next(c for c in cands if text in c.title)


def test_breakeven_is_where_the_candidate_equals_the_original(amide):
    r, base, cands = amide
    c = _by_title(cands, "ethyl acetate in place of DMF")
    for key in ("pmi", "rme", "ecoscale"):
        be = c.breakeven[key]
        assert be["status"] == "at"
        at = breakeven.scenario(c.reaction, be["yield"]).metrics[key]
        assert at == pytest.approx(base.metrics[key], rel=1e-6, abs=0.011)   # EcoScale is rounded to 0.01
        worse = breakeven.scenario(c.reaction, be["yield"] - 0.02).metrics[key]
        assert (worse > base.metrics[key]) if key == "pmi" else (worse < base.metrics[key])
    g = c.breakeven["greenness"]
    assert g["status"] in ("always", "at")


def test_candidate_is_scored_at_the_original_yield(amide):
    r, base, cands = amide
    for c in cands:
        if c.reaction is not None:
            assert c.score.metrics["yield"] == pytest.approx(base.metrics["yield"])
            assert c.reaction.product.smiles == r.product.smiles


def test_solvent_swap_passes_with_evidence_and_estimate_marks(amide):
    r, base, cands = amide
    c = _by_title(cands, "ethyl acetate in place of DMF")
    assert c.status == "passed" and c.check["passed"]
    assert c.rule_id == "amide-solvent-dmf" and c.evidence_level == "class_specific_study"
    assert any(s["doi"] == "10.1039/C2GC36900A" for s in c.sources)
    assert c.precedent["available"] and c.precedent["n_sole"] > 20 and c.confidence == "high"
    assert any("same volume" in a for a in c.assumptions)             # the new mass is an estimate
    assert "pmi" in c.score.estimated_metrics
    groups = {row["group"] for row in c.check["rows"]}
    assert groups == {"Product", "Hazard", "Metrics", "Cost"}        # the user sees what was compared
    assert c.check["rows"][0]["result"] == "pass"
    cost = next(row for row in c.check["rows"] if row["group"] == "Cost")
    assert cost["result"] == "not compared"                           # no price data is shipped
    worst = next(row for row in c.check["rows"] if row["item"].startswith("Worst hazard score: new"))
    assert worst["original"] == 9 and worst["candidate"] < 9 and worst["result"] == "better"


def test_a_more_hazardous_replacement_is_rejected_with_the_reason(amide):
    r, base, cands = amide
    c = _by_title(cands, "T3P")
    assert c.status == "rejected" and "Worst hazard score" in c.reason
    assert c.check["passed"] is False
    assert all(x.status != "passed" for x in cands if x.kind == "reagent")


def test_check_fails_on_a_different_product_and_on_unknown_hazard(amide):
    r, base, _ = amide
    other = r.copy()
    other.product.smiles = "CC(=O)NCc1ccccc1"
    res = regression.check(r, base, other, scoring.score(other))
    assert not res["passed"] and any("Same product" in f for f in res["failures"])

    cand = edits.apply(r, [{"op": "replace", "name": "DMF", "amount": "same_moles",
                            "with": {"name": "unlisted solvent Q", "smiles": "CCCCOCCOCCOCCCC"}}])
    res = regression.check(r, base, cand, scoring.score(cand))
    assert not res["passed"] and any("no hazard data" in f for f in res["failures"])


def test_check_fails_when_a_metric_gets_worse_beyond_tolerance(amide):
    r, base, _ = amide
    cand = edits.apply(r, [{"op": "scale_mass", "name": "ethyl acetate", "factor": 3.0}])
    res = regression.check(r, base, cand, scoring.score(cand))
    assert not res["passed"] and any("Process mass intensity" in f for f in res["failures"])
    # a small loss inside the tolerance is shown as a trade-off, not hidden
    cand = edits.apply(r, [{"op": "scale_mass", "name": "ethyl acetate", "factor": 1.2}])
    res = regression.check(r, base, cand, scoring.score(cand))
    assert res["passed"] and any("Process mass intensity" in t for t in res["trade_offs"])


def test_cost_is_checked_when_prices_are_known(amide, monkeypatch):
    r, base, cands = amide
    prices = {"currency": "EUR", "prices": [{"name": c.name, "smiles": c.smiles, "per_kg": 10.0} for c in r.inputs
                                            if c.role != "water"]
              + [{"name": "ethyl acetate", "smiles": "CCOC(C)=O", "per_kg": 900.0}]}
    from gms import config
    real = config.load
    monkeypatch.setattr(config, "load", lambda name: prices if name == "prices" else real(name))
    c = _by_title(cands, "ethyl acetate in place of DMF")
    res = regression.check(r, base, c.reaction, c.score)
    cost = next(row for row in res["rows"] if row["group"] == "Cost")
    assert cost["result"] == "fail" and not res["passed"]


def test_stoichiometry_candidate_uses_precedents():
    r, s, _ = scoring.prepare_and_score(examples.load("example-suzuki-dioxane"), online=False)
    c = next(x for x in suggest.suggest(r, s) if x.kind == "stoichiometry")
    assert c.status == "passed" and "1.3 equivalents of phenylboronic acid" in c.title
    assert "precedents" in c.precedent["note"] and c.changes["rme"] > 0


def test_mixture_replacement_splits_the_volume():
    r, s, _ = scoring.prepare_and_score(examples.load("example-suzuki-dioxane"), online=False)
    c = next(x for x in suggest.suggest(r, s) if x.title.startswith("isopropanol/water"))
    parts = {x.name: x for x in c.reaction.components if x.estimated}
    assert set(parts) == {"isopropanol", "water (co-solvent)"}        # "water" was already in the reaction
    assert parts["isopropanol"].volume_ml == pytest.approx(3.0)
    assert parts["water (co-solvent)"].volume_ml == pytest.approx(1.0)
    assert parts["water (co-solvent)"].role == "water" and c.status == "passed"
    assert len({x.name for x in c.reaction.components}) == len(c.reaction.components)


def test_edit_that_cannot_be_computed_is_reported():
    r, s, _ = scoring.prepare_and_score(examples.load("example-suzuki-dioxane"), online=False)
    with pytest.raises(edits.EditError):
        edits.apply(r, [{"op": "replace", "name": "1,4-dioxane", "amount": "same_volume",
                         "with": {"name": "solvent without density", "smiles": "CCCCCCCCCCCCCCCC(C)C"}}])
    r.components.insert(-1, Component("x", "additive", mass_g=1.0))
    assert edits.apply(r, [{"op": "remove", "name": "x"}]).find("x") is None


def test_catalytic_replacement_is_not_counted_as_a_reactant(amide):
    r, base, cands = amide
    c = _by_title(cands, "boric acid")
    new = next(x for x in c.reaction.components if x.name.startswith("boric acid"))
    assert new.role == "catalyst" and new.equiv == pytest.approx(0.10)
    assert c.score.metrics["ae"] > 90                                 # acid + amine -> amide + water
    assert c.status == "rejected" and "hazard" in c.reason            # boric acid is H360


def test_candidate_estimates_are_marked_in_the_exports(amide):
    import csv
    import io
    from gms import report
    r, base, cands = amide
    c = _by_title(cands, "ethyl acetate in place of DMF")
    assert "pmi" in c.summary()["estimated_metrics"]
    rep = report.build(r, base, [], cands)
    rows = list(csv.reader(io.StringIO(report.to_csv(rep).decode("utf-8"))))
    head = next(x for x in rows if x and x[0] == "Metric")
    col = head.index(f"Candidate at original yield: {c.title}")
    assert head[col + 1] == "Candidate is estimated"
    pmi = next(x for x in rows if x and x[0] == "Process mass intensity")
    ae = next(x for x in rows if x and x[0] == "Atom economy")
    assert pmi[col + 1] == "estimated" and ae[col + 1] == ""
