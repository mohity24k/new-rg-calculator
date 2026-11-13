"""score(reaction): same numbers as the literature formulas, roles kept, estimates marked."""

import json
import math
from pathlib import Path

import pytest

from gms import examples, greenness, scoring
from gms.chem import amounts
from gms.metrics import ecoscale as es
from gms.metrics import registry
from gms.model import Component, Reaction

BASELINE = json.loads((Path(__file__).parent / "golden" / "metrics_baseline.json").read_text("utf-8"))


def _reaction(case: dict) -> Reaction:
    """A golden mass-metric case (reactants / auxiliaries / product) as a reaction record."""
    comps = [Component(name=r["name"], role="reactant", mw=r["mw"], moles=r["moles"], mass_g=r["mass"],
                       carbons=r.get("carbons", 0), coefficient=r.get("stoich_ratio", 1.0),
                       limiting=r.get("is_limiting", False), benign=r.get("benign", False)) for r in case["reactants"]]
    comps += [Component(name=a["name"], role="additive", mass_g=a["mass"], benign=a.get("benign", False))
              for a in case["aux"]]
    if case.get("water_mass"):
        comps.append(Component(name="water", role="water", mass_g=case["water_mass"]))
    comps.append(Component(name="P", role="product", mw=case["product_mw"], mass_g=case["product_actual_mass"],
                           moles=case.get("product_moles"),
                           carbons=case.get("product_carbons"), coefficient=case.get("product_coefficient", 1.0)))
    return Reaction(name="golden", components=comps)


def _dec(v):
    return {"inf": math.inf, "-inf": -math.inf, "nan": math.nan}[v["__float__"]] if isinstance(v, dict) else v


@pytest.mark.parametrize("case", sorted(BASELINE["mass"]))
def test_score_reproduces_the_locked_mass_metrics(case):
    """The new entry point returns exactly the values recorded before the restructuring."""
    rec = BASELINE["mass"][case]
    metrics, totals = scoring.mass_metrics(_reaction(rec["inputs"]))
    for key, m in registry.metrics().items():
        if not m.legacy_label or key == "ecoscale":
            continue
        expected = rec["outputs"].get(m.legacy_label)
        if case == "no_reactants" and key in ("ae", "rme"):
            # Deliberate difference from the formulas: with no reactant in the reaction the
            # formulas return 0; the score reports "not available" (see test_unknown_is_not_zero).
            assert metrics[key] is None
        elif key == "ce" and "product_carbons" not in rec["inputs"]:
            assert metrics[key] is None
        elif expected is None:
            assert metrics[key] is None, key
        else:
            assert metrics[key] == pytest.approx(_dec(expected), rel=1e-12, nan_ok=True), key
    assert totals["total_input_mass_g"] == pytest.approx(rec["outputs"]["Total Mass Input incl. water (g)"])


def test_registry_covers_every_legacy_metric_label():
    labels = {m.legacy_label for m in registry.metrics().values() if m.legacy_label}
    for name in ("Atom Economy (AE, %)", "Process Mass Intensity (PMI, kg/kg)", "E-Factor (kg waste/kg product)",
                 "Carbon Efficiency (CE, %)", "Excess Reactant Factor (ERF)", "Percentage Yield (%)"):
        assert name in labels


def test_thresholds_come_from_config():
    assert registry.rating("pmi", 4) == "good" and registry.rating("pmi", 12) == "moderate"
    assert registry.rating("pmi", 300) == "poor" and registry.rating("ae", None) == "n/a"
    assert registry.rating("ecoscale", 75) == "good" and registry.rating("ecoscale", 49.9) == "poor"
    assert registry.is_worse("pmi", 100, 104) is False          # inside the 5 % tolerance
    assert registry.is_worse("pmi", 100, 106) is True
    assert registry.is_worse("ae", 60, 60.2) is False and registry.change("pmi", 100, 90) == 10


def suzuki() -> Reaction:
    return examples.load("example-suzuki-dioxane")


def test_amounts_are_completed_from_volume_equivalents_and_structure():
    r = suzuki()
    issues = amounts.complete(r, online=False)
    assert issues == []
    c = {x.name: x for x in r.components}
    assert c["1,4-dioxane"].mass_g == pytest.approx(4 * 1.033, rel=0.01) and "volume x density" in c["1,4-dioxane"].note
    assert c["phenylboronic acid"].moles == pytest.approx(1.5 * c["4-bromoanisole"].moles)
    assert c["Pd(PPh3)4"].mass_g == pytest.approx(0.05 * c["4-bromoanisole"].moles * 1155.6, rel=0.01)
    assert c["4-bromoanisole"].limiting and c["K2CO3"].equiv == 2.0
    assert not any(x.estimated for x in r.components)            # conversions are not estimates


def test_score_of_the_suzuki_example():
    r, s, _ = scoring.prepare_and_score(suzuki(), online=False)
    assert s.metrics["yield"] == pytest.approx(90.1, abs=0.2)
    assert s.metrics["e_factor"] == pytest.approx(s.metrics["pmi"] - 1)
    assert s.metrics["mp"] == pytest.approx(100 / s.metrics["mi"])
    total = sum(c.mass for c in r.inputs)
    assert s.metrics["pmi"] == pytest.approx(total / 0.166)
    # roles survive: the solvent share of the PMI can be computed
    share = {c["name"]: c["pmi_contribution"] for c in s.contributions}
    assert sum(share.values()) == pytest.approx(s.metrics["pmi"])
    assert share["hexane"] > share["1,4-dioxane"] > share["Pd(PPh3)4"]
    # EcoScale: yield + safety (dioxane T, F; ethyl acetate F; hexane T, F, N) + inert 1 + heating 3 + 3 + 10
    eco = s.ecoscale
    assert eco["penalties"]["setup"] == 1 and eco["penalties"]["temp_time"] == 3 and eco["penalties"]["workup"] == 13
    assert eco["safety_by_material"]["1,4-dioxane"] == ["F - Highly flammable", "T - Toxic"]
    assert eco["penalties"]["safety"] == 10 + 5 + 15
    assert eco["score"] == pytest.approx(100 - (100 - s.metrics["yield"]) / 2 - 30 - 1 - 3 - 13, abs=0.01)
    assert not eco["price_assessed"] and any("price" in e for e in s.estimates)


def test_ecoscale_matches_the_direct_formula():
    r, s, _ = scoring.prepare_and_score(suzuki(), online=False)
    direct = es.compute_eco_scale(es.EcoScaleInputs(
        route_name="x", yield_pct=s.metrics["yield"], price_category="Inexpensive (< $10 for 10 mmol product)",
        safety_hazards=[h for row in s.hazards for h in row["ecoscale_symbols"]],
        technical_setups=["Inert gas atmosphere"], temp_time_category="Heating, > 1 h", workup_steps=r.workup_steps))
    assert s.ecoscale["score"] == direct["Eco-Scale Score"]


def test_estimated_amounts_are_marked_and_never_mixed_silently():
    r = suzuki()
    r.components.insert(-1, Component(name="eluent (assumed)", role="purification", smiles="CCCCCCC",
                                      mass_g=50.0, estimated=True, note="typical volume"))
    r, s, _ = scoring.prepare_and_score(r, online=False)
    assert "pmi" in s.estimated_metrics and "e_factor" in s.estimated_metrics
    assert "ae" not in s.estimated_metrics and "yield" not in s.estimated_metrics
    assert any("eluent (assumed)" in e and "estimate" in e for e in s.estimates)
    without = scoring.score(scoring.measured_only(r))
    assert without.metrics["pmi"] == pytest.approx(s.metrics["pmi"] - 50.0 / 0.166)
    assert without.estimated_metrics == []


def test_missing_amount_is_reported_not_invented():
    r = suzuki()
    r.components.insert(-1, Component(name="dichloromethane", role="workup"))
    r, s, issues = scoring.prepare_and_score(r, online=False)
    assert any("dichloromethane" in i and "no amount" in i for i in issues)
    assert any("lower bound" in w for w in s.warnings)


def test_with_yield_changes_only_the_product():
    r, s, _ = scoring.prepare_and_score(suzuki(), online=False)
    theo = scoring.theoretical_product_mass(r)
    assert theo == pytest.approx(0.001 * 184.24, rel=0.01)
    half = scoring.score(scoring.with_yield(r, 0.5))
    assert half.metrics["yield"] == pytest.approx(50)
    assert half.metrics["pmi"] == pytest.approx(s.metrics["pmi"] * s.metrics["yield"] / 50)
    assert half.metrics["ae"] == s.metrics["ae"]


def test_verdict_reasons_and_caps():
    _, s, _ = scoring.prepare_and_score(suzuki(), online=False)
    g = s.greenness
    assert g["verdict"] == "not_green" and 0 <= g["score"] <= 1
    assert any("1,4-dioxane" in c for c in g["caps"])              # H350: cannot be called green
    assert any("Process mass intensity" in x for x in g["reasons"])
    sens = greenness.sensitivity(g)
    assert sens == greenness.sensitivity(g)                       # fixed seed: reproducible
    assert 0 <= sens["same_verdict_share"] <= 1 and sens["score_low"] <= g["score"] <= sens["score_high"]


def test_desirability_limits():
    assert greenness.desirability(10, worst=500, best=10, scale="log") == 1.0
    assert greenness.desirability(500, worst=500, best=10, scale="log") == 0.0
    assert greenness.desirability(math.inf, worst=500, best=10, scale="log") == 0.0
    assert greenness.desirability(65, worst=40, best=90) == pytest.approx(0.5)
    assert greenness.desirability(None, 0, 1) is None


def test_a_clean_reaction_can_be_green():
    r = Reaction(name="clean", temperature_c=22, time_h=0.5, workup_steps=["Simple filtration"], components=[
        Component("benzoic acid", "reactant", mass_g=12.2, limiting=True),
        Component("benzylamine", "reactant", equiv=1.0),
        Component("water", "water", stage="reaction", volume_ml=20),
        Component("N-benzylbenzamide", "product", smiles="O=C(NCc1ccccc1)c1ccccc1", mass_g=20.0)])
    _, s, _ = scoring.prepare_and_score(r, online=False)
    assert s.greenness["verdict"] == "green" and s.greenness["caps"] == []


def test_unknown_hazard_is_not_treated_as_safe():
    r = suzuki()
    r.components.insert(-1, Component(name="mystery additive XZ-1", role="additive", mass_g=0.1))
    _, s, _ = scoring.prepare_and_score(r, online=False)
    assert "mystery additive XZ-1" in s.hazard_summary["unknown"]
    assert any("No hazard data" in e for e in s.estimates)
    assert any("Hazard unknown" in n for n in s.greenness["notes"])


# --- unknown is "not available", never 0 ----------------------------------------------------------

def test_unknown_is_not_zero():
    """A missing molecular weight or mass must not turn into a 0 that reads as a result."""
    # product without structure (a compound number)
    r = examples.load("example-amide-hatu-dmf")
    r.product.name, r.product.smiles = "compound 3a", None
    r, s, issues = scoring.prepare_and_score(r, online=False)
    assert s.metrics["yield"] is None and s.metrics["ae"] is None and s.metrics["ce"] is None
    assert s.metrics["pmi"] == pytest.approx(sum(c.mass for c in r.inputs) / r.product.mass)    # masses are known
    assert s.ecoscale is None and any("no structure or molecular weight" in w for w in s.warnings)
    assert scoring.theoretical_product_mass(r) is None

    # a stoichiometric reagent without structure: AE must not rise because the reagent is left out
    base = scoring.prepare_and_score(examples.load("example-amide-hatu-dmf"), online=False)[1]
    r = examples.load("example-amide-hatu-dmf")
    hatu = r.find("HATU")
    hatu.name, hatu.equiv, hatu.mass_g = "reagent Q-17", None, 0.4
    r, s, _ = scoring.prepare_and_score(r, online=False)
    assert s.metrics["ae"] is None and s.metrics["ce"] is None and base.metrics["ae"] == pytest.approx(37.7, abs=0.1)
    assert s.metrics["rme"] is not None and s.metrics["yield"] == pytest.approx(base.metrics["yield"])
    assert any("reagent Q-17" in w and "molecular weight" in w for w in s.warnings)

    # no absolute amount anywhere: no impossible PMI of 0 or E-factor of -1
    r = Reaction(components=[Component("benzoic acid", "reactant", equiv=1.0, limiting=True),
                             Component("benzylamine", "reactant", equiv=1.2),
                             Component("N-benzylbenzamide", "product", smiles="O=C(NCc1ccccc1)c1ccccc1")])
    r, s, _ = scoring.prepare_and_score(r, online=False)
    for key in ("pmi", "e_factor", "mi", "mp", "emy", "rme", "yield", "hazmat"):
        assert s.metrics[key] is None, key
    assert s.greenness["verdict"] == "unknown" and s.greenness["score"] is None


def test_product_mass_from_the_reported_yield_is_an_estimate():
    r = examples.load("example-suzuki-dioxane")
    r.product.mass_g = None
    r.reported_yield_percent = 90.0
    r, s, _ = scoring.prepare_and_score(r, online=False)
    assert r.product.estimated and "reported yield of 90%" in r.product.note
    assert s.metrics["yield"] == pytest.approx(90.0)
    assert {"pmi", "rme", "yield", "ecoscale"} <= set(s.estimated_metrics)
    assert any("reported yield" in e for e in s.estimates)
    # without a reported yield nothing is invented
    r = examples.load("example-suzuki-dioxane")
    r.product.mass_g = None
    r, s, _ = scoring.prepare_and_score(r, online=False)
    assert s.metrics["yield"] is None and s.metrics["pmi"] is None and s.ecoscale is None
    assert any("product has no mass" in w for w in s.warnings)


def test_one_equivalent_is_the_moles_of_the_limiting_reactant():
    """Also when the limiting reactant has a coefficient other than 1."""
    r = Reaction(components=[
        Component("bromobenzene", "reactant", moles=0.002, coefficient=2, limiting=True),
        Component("benzene-1,4-diboronic acid", "reactant", smiles="OB(O)c1ccc(B(O)O)cc1", equiv=0.75),
        Component("p-terphenyl", "product", smiles="c1ccc(cc1)-c1ccc(cc1)-c1ccccc1", mass_g=0.2)])
    amounts.complete(r, online=False)
    b = r.find("benzene-1,4-diboronic acid")
    assert b.moles == pytest.approx(0.0015) and r.find("bromobenzene").equiv == 1.0
    lim = amounts.limiting(r)
    assert amounts.excess_factor(b, lim) == pytest.approx(1.5)        # 0.75 equiv, 0.5 equiv needed
