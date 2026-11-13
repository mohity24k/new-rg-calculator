"""Diagnosis (computed gains), manual-entry forms, reaction class and coefficient inference."""

import pytest

from gms import diagnosis, edits, examples, forms, scoring
from gms.chem import classify, stoich
from gms.model import Component, Reaction


@pytest.fixture(scope="module")
def suzuki():
    from gms.chem import hazard
    hazard.set_store(hazard.HazardStore(online=False))
    r, s, _ = scoring.prepare_and_score(examples.load("example-suzuki-dioxane"), online=False)
    return r, s, diagnosis.diagnose(r, s)


def test_findings_name_the_cause(suzuki):
    r, s, findings = suzuki
    codes = [f.code for f in findings]
    for code in ("mass_hotspot", "workup_share", "excess_reagent", "hazard", "chromatography", "harsh_conditions"):
        assert code in codes
    hot = next(f for f in findings if f.code == "mass_hotspot")
    assert hot.targets == ["hexane"] and "% of the input mass" in hot.title
    haz = [f.targets[0] for f in findings if f.code == "hazard"]
    assert "1,4-dioxane" in haz and "hexane" in haz
    assert findings == sorted(findings, key=lambda f: (-(f.greenness_gain or 0), -(f.gains.get("pmi") or 0)))


def test_every_gain_is_recomputed_by_the_calculator(suzuki):
    r, s, findings = suzuki
    f = next(x for x in findings if x.code == "excess_reagent")
    again = scoring.score(edits.apply(r, [{"op": "set_equiv", "name": "phenylboronic acid", "equiv": 1.1}]))
    assert f.after["rme"] == pytest.approx(again.metrics["rme"])
    assert f.gains["rme"] == pytest.approx(again.metrics["rme"] - s.metrics["rme"]) and f.gains["rme"] > 0
    assert f.gains["pmi"] == pytest.approx(s.metrics["pmi"] - again.metrics["pmi"])
    c = next(x for x in findings if x.code == "chromatography")
    # workup 10 -> 1 point, and the hexane eluent (T, F, N = 15 safety points) is no longer used
    assert c.gains["ecoscale"] == pytest.approx(9.0 + 15.0)
    assert c.gains["pmi"] == pytest.approx(sum(x.mass for x in r.by_role("purification")) / r.product.mass)
    h = next(x for x in findings if x.code == "hazard" and x.targets == ["1,4-dioxane"])
    assert h.gains["ecoscale"] == pytest.approx(10.0) and h.gains["pmi"] == pytest.approx(0.0)


def test_low_yield_and_wasteful_reagent_findings():
    r, s, _ = scoring.prepare_and_score(examples.load("example-amide-hatu-dmf"), online=False)
    low = scoring.with_yield(r, 0.4)
    codes = {f.code: f for f in diagnosis.diagnose(low)}
    assert codes["low_yield"].after["pmi"] == pytest.approx(scoring.score(scoring.with_yield(r, 0.9)).metrics["pmi"])
    assert codes["wasteful_reagent"].targets == ["HATU"] and codes["wasteful_reagent"].gains["ae"] > 20


def test_stage_breakdown_sums_to_the_pmi(suzuki):
    r, s, _ = suzuki
    bd = diagnosis.stage_breakdown(s)
    assert sum(v["pmi"] for v in bd["by_stage"].values()) == pytest.approx(s.metrics["pmi"])
    assert set(bd["by_stage"]) == {"reaction", "workup", "purification"}
    assert bd["by_role"]["reaction_solvent"]["mass_g"] == pytest.approx(r.find("1,4-dioxane").mass)


# --- forms -----------------------------------------------------------------------------------

def test_rows_round_trip_gives_the_same_scores():
    for ex in examples.titles():
        r0 = examples.load(ex)
        r1, problems = forms.reaction_from_rows(forms.rows_from_reaction(r0), forms.meta_from_reaction(r0))
        assert problems == []
        a = scoring.prepare_and_score(r0, online=False)[1].metrics
        b = scoring.prepare_and_score(r1, online=False)[1].metrics
        assert b == pytest.approx(a, rel=1e-4)


def test_units_are_converted():
    rows = [{"Name": "benzoic acid", "Role": "reactant", "Amount": 122, "Unit": "mg", "Limiting": True},
            {"Name": "benzylamine", "Role": "reactant", "Amount": 1.2, "Unit": "equiv"},
            {"Name": "DMAP", "Role": "catalyst", "Amount": 10, "Unit": "mol%"},
            {"Name": "DCM", "Role": "reaction solvent", "Amount": 5000, "Unit": "µL"},
            {"Name": "N-benzylbenzamide", "Role": "product", "Amount": 0.9, "Unit": "mmol", "SMILES": "O=C(NCc1ccccc1)c1ccccc1"}]
    r, problems = forms.reaction_from_rows(rows, {"name": "t", "workup_steps": ["Simple filtration", "not a step"]})
    assert problems == []
    c = {x.name: x for x in r.components}
    assert c["benzoic acid"].mass_g == pytest.approx(0.122) and c["benzylamine"].equiv == 1.2
    assert c["DMAP"].equiv == pytest.approx(0.10) and c["DCM"].volume_ml == pytest.approx(5.0)
    assert c["N-benzylbenzamide"].moles == pytest.approx(0.0009) and r.workup_steps == ["Simple filtration"]
    assert r.price_category is None and r.reaction_class is None and r.source == {"entry": "manual"}


@pytest.mark.parametrize("rows, text", [
    ([{"Name": "A", "Role": "reactant", "Amount": 1, "Unit": "g"}], "exactly one product"),
    ([{"Name": "P", "Role": "product", "Amount": 1, "Unit": "g"}], "at least one reactant"),
    ([{"Name": "A", "Role": "reactant", "Amount": -1, "Unit": "g"}, {"Name": "P", "Role": "product"}], "negative"),
    ([{"Name": "A", "Role": "wizard", "Amount": 1, "Unit": "g"}, {"Name": "P", "Role": "product"}], "choose a role"),
    ([{"Name": "A", "Role": "reactant", "Amount": 1, "Unit": "parsec"}, {"Name": "P", "Role": "product"}], "choose a unit"),
    ([{"Name": "A", "Role": "reactant"}, {"Name": "A", "Role": "reagent"}, {"Name": "P", "Role": "product"}], "more than once"),
    ([{"Name": "A", "Role": "reactant", "Limiting": True}, {"Name": "B", "Role": "reactant", "Limiting": True},
      {"Name": "P", "Role": "product"}], "only one limiting"),
    ([{"Name": "x" * 300, "Role": "reactant"}, {"Name": "P", "Role": "product"}], "longer than"),
    ([{"Name": "", "Role": "reactant", "Amount": 2, "Unit": "g"}, {"Name": "P", "Role": "product"}], "name is missing"),
    ([{"Name": f"m{i}", "Role": "additive"} for i in range(70)], "At most"),
])
def test_invalid_input_is_rejected_with_a_clear_message(rows, text):
    r, problems = forms.reaction_from_rows(rows, {})
    assert r is None and any(text in p for p in problems), problems


def test_blank_rows_and_text_in_number_cells_are_ignored():
    rows = [{"Name": "A", "Role": "reactant", "Amount": "abc", "Unit": "g"}, {"Name": None, "Amount": None},
            {"Name": "  ", "Amount": float("nan")}, {"Name": "P", "Role": "product", "Amount": 1, "Unit": "g"}]
    r, problems = forms.reaction_from_rows(rows, {})
    assert problems == [] and [c.name for c in r.components] == ["A", "P"] and r.components[0].mass_g is None


# --- reaction class and coefficients ---------------------------------------------------------

@pytest.mark.parametrize("example, cls", [("example-amide-hatu-dmf", "amide_coupling"),
                                          ("example-suzuki-dioxane", "suzuki_miyaura"),
                                          ("example-redam-dce", "reductive_amination")])
def test_examples_are_classified_from_structure(example, cls):
    r = examples.load(example)
    r.reaction_class = None
    r, _, _ = scoring.prepare_and_score(r, online=False)
    assert classify.classify(r)[:2] == (cls, "structure")


def test_classifier_does_not_overreach():
    C = classify.classify_structures
    assert C(["CC(=O)Cl", "NCc1ccccc1"], "CC(=O)NCc1ccccc1")[0] is None            # acid chloride: not a coupling
    assert C(["OC(=O)[O-].[Na+]", "Nc1ccccc1", "O=C(Cl)OCc1ccccc1"], "O=C(Nc1ccccc1)OCc1ccccc1")[0] is None  # carbamate
    assert C(["CCO", "CC(=O)O"], "CCOC(C)=O")[0] is None                            # esterification
    assert C(["CC(=O)c1ccccc1", "CN"], "CNC(C)c1ccccc1", "nabh3cn methanol")[0] == "reductive_amination"
    assert C(["Brc1ccccc1", "CC1(C)OB(c2ccncc2)OC1(C)C"], "c1ccc(-c2ccncc2)cc1", "pd(dppf)cl2")[0] == "suzuki_miyaura"
    # names only (structures unknown): weaker evidence, and "dic" inside "dichloroethane" is not a reagent
    assert C([], None, "some acid, some amine, hatu, dipea")[:2] == ("amide_coupling", "reagents")
    assert C([], None, "benzaldehyde morpholine 1,2-dichloroethane")[0] is None
    assert classify.is_partner("amide_coupling", "NCc1ccccc1") and not classify.is_partner("amide_coupling", "CCN(CC)CC")


def test_coefficients_from_atom_balance():
    assert stoich.infer_coefficients(["COc1ccc(Br)cc1", "OB(O)c1ccccc1"], "COc1ccc(cc1)-c1ccccc1") == \
        {"coefficients": [1, 1], "covered": True}
    # double coupling on a dibromide needs two boronic acids
    assert stoich.infer_coefficients(["Brc1ccc(Br)cc1", "OB(O)c1ccccc1"], "c1ccc(cc1)-c1ccc(cc1)-c1ccccc1") == \
        {"coefficients": [1, 2], "covered": True}
    assert stoich.infer_coefficients(["CO"], "CCCCCCCCCCCC")["covered"] is False      # a reactant is missing
    assert stoich.infer_coefficients(["CCO", None], "CCOC(C)=O") is None


def test_roles_are_validated_in_the_model():
    with pytest.raises(ValueError):
        Component("x", "solvent")
    r = Reaction(components=[Component("a", "reactant", mass_g=1.0), Component("p", "product")])
    assert Reaction.from_dict(r.to_dict()) == r and r.product.name == "p" and [c.name for c in r.inputs] == ["a"]
