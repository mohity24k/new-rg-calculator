"""PDF -> review table -> calculator inputs, with model answers typical of real SI procedures.

The vision model is not called: each case feeds the JSON a model returns for one procedure
and checks the chemistry done by the app (structures, MW, mL -> g, equiv -> mol, yield).
Structure lookups use the built-in alias table / OPSIN, so the tests run offline."""

import pytest

from modules import mass_metrics as mm
from modules import pdf_extraction as px


def table(components, **extra):
    return px.build_review_table([{"page": 1, "data": {"components": components, **extra}}])


def row(df, name):
    hit = df[df["Name"].str.lower() == name.lower()]
    assert not hit.empty, f"{name} missing from\n{df}"
    return hit.iloc[0]


def test_suzuki_coupling_with_equiv_mol_percent_and_volumes():
    df = table([
        {"name": "4-bromoanisole", "role": "reactant", "quantity": 187, "unit": "mg", "equivalents": 1.0},
        {"name": "phenylboronic acid", "role": "reagent", "quantity": 1.5, "unit": "equiv"},
        {"name": "Pd(PPh3)4", "role": "catalyst", "quantity": 5, "unit": "mol %"},
        {"name": "K2CO3", "role": "base", "quantity": "2.0 equiv"},
        {"name": "1,4-dioxane", "role": "solvent", "quantity": 4, "unit": "mL"},
        {"name": "H2O", "role": "solvent", "quantity": 1, "unit": "mL"},
        {"name": "EtOAc", "role": "workup", "quantity": "3 x 10", "unit": "mL"},
        {"name": "4-methoxybiphenyl (3a)", "role": "product", "quantity": 166, "unit": "mg"},
    ])
    assert row(df, "4-bromoanisole")["Moles (mol)"] == pytest.approx(1.0e-3, rel=1e-3)
    assert row(df, "phenylboronic acid")["Moles (mol)"] == pytest.approx(1.5e-3, rel=1e-3)
    assert row(df, "Pd(PPh3)4")["Mass (g)"] == pytest.approx(0.05 * 1e-3 * 1155.59, rel=1e-3)
    assert row(df, "K2CO3")["Mass (g)"] == pytest.approx(2e-3 * 138.2, rel=1e-3)
    assert row(df, "1,4-dioxane")["Mass (g)"] == pytest.approx(4 * 1.03, rel=1e-3)
    assert row(df, "EtOAc")["Mass (g)"] == pytest.approx(30 * 0.902, rel=1e-3)
    assert row(df, "H2O")["Role"] == "water"
    assert px.estimate_yield(df) == pytest.approx(90.1, abs=0.2)

    t = px.to_app_tables(df)
    assert t["water_mass"] == pytest.approx(1.0)
    assert t["reactants_df"]["Limiting reagent?"].sum() == 1
    assert t["product"]["carbons"] == 13 and t["product"]["mw"] == pytest.approx(184.24, abs=0.01)
    # the pre-filled tables must run through the calculator without manual fixes
    rx = [mm.Reactant(r["Name"], r["MW (g/mol)"], r["Moles charged (mol)"], r["Mass charged (g)"],
                      int(r["Carbons/molecule"]), is_limiting=r["Limiting reagent?"])
          for _, r in t["reactants_df"].iterrows()]
    res = mm.compute_all_mass_metrics(rx, t["product"]["mw"], t["product"]["actual_mass"], None,
                                      [mm.AuxiliaryMaterial(n, m) for n, m in
                                       zip(t["aux_df"]["Name"], t["aux_df"]["Mass (g)"])],
                                      water_mass=t["water_mass"], product_carbons=t["product"]["carbons"])
    assert res["Percentage Yield (%)"] == pytest.approx(90.1, abs=0.2)
    assert not [w for w in mm.validate_inputs(rx, t["product"]["mw"], t["product"]["actual_mass"])
                if "does not match" in w]


def test_amide_coupling_edc_hobt():
    df = table([
        {"name": "benzoic acid", "role": "reactant", "quantity": 1.22, "unit": "g"},
        {"name": "benzylamine", "role": "reactant", "quantity": 1.1, "unit": "equiv"},
        {"name": "EDC·HCl", "role": "reagent", "quantity": 1.2, "unit": "equiv"},
        {"name": "HOBt", "role": "additive", "quantity": 0.2, "unit": "equiv"},
        {"name": "DIPEA", "role": "base", "quantity": 3.5, "unit": "mL"},
        {"name": "DMF", "role": "solvent", "quantity": 20, "unit": "mL"},
        {"name": "N-benzylbenzamide", "role": "product", "quantity": 1.9, "unit": "g"},
    ])
    acid = row(df, "benzoic acid")
    assert acid["Moles (mol)"] == pytest.approx(1.22 / 122.12, rel=1e-3)
    assert row(df, "benzylamine")["Moles (mol)"] == pytest.approx(1.1 * acid["Moles (mol)"], rel=1e-3)
    assert row(df, "DIPEA")["Mass (g)"] == pytest.approx(3.5 * 0.742, rel=1e-3)
    assert row(df, "DMF")["Mass (g)"] == pytest.approx(20 * 0.944, rel=1e-3)


def test_reductive_amination_with_unicode_minus_and_overnight():
    pages = [{"page": 1, "data": {"components": [
        {"name": "benzaldehyde", "role": "reactant", "quantity": 1.06, "unit": "g"},
        {"name": "morpholine", "role": "reactant", "quantity": 1.0, "unit": "mL"},
        {"name": "NaBH(OAc)3", "role": "reagent", "quantity": 1.5, "unit": "equiv"},
        {"name": "DCE", "role": "solvent", "quantity": 30, "unit": "mL"},
        {"name": "sat. aq. NaHCO3", "role": "workup", "quantity": 20, "unit": "mL"},
    ], "conditions": {"temperature_c": "−10 to 0", "time_h": "overnight", "atmosphere": "N2",
                      "workup": "extraction with DCM, column chromatography"}}}]
    df = px.build_review_table(pages)
    assert row(df, "morpholine")["Mass (g)"] == pytest.approx(1.0, rel=1e-3)  # density 1.00
    assert row(df, "sat. aq. NaHCO3")["Role"] == "water"
    meta = px.merge_meta(pages)
    sug = px.suggest_eco_inputs(meta, None)
    assert meta["temperature_c"] == -5 and meta["time_h"] == 16
    assert sug["temp_time"] == "Cooling, < 0 degC"
    assert "Inert gas atmosphere" in sug["setups"]
    assert sug["workup_steps"] == ["Liquid-liquid extraction (incl. drying/filtration of desiccant)",
                                   "Classical chromatography"]


def test_photoredox_reaction_suggests_unconventional_activation():
    meta = px.merge_meta([{"page": 1, "data": {"components": [], "conditions": {
        "activation": "blue LEDs (450 nm)", "temperature_c": "rt", "time_h": "24 h",
        "workup": ["filtration through Celite", "recrystallization from EtOH"]}}}])
    sug = px.suggest_eco_inputs(meta, 75)
    assert "Unconventional activation (microwave / ultrasound / photochemical)" in sug["setups"]
    assert sug["temp_time"] == "Room temperature, < 24 h"
    assert sug["workup_steps"] == ["Simple filtration", "Crystallization and filtration"]


def test_reflux_is_heating():
    meta = px.merge_meta([{"page": 1, "data": {"conditions": {"temperature_c": "reflux", "time_h": "3 h"}}}])
    assert px.suggest_eco_inputs(meta, 80)["temp_time"] == "Heating, > 1 h"


def test_procedure_split_over_two_pages_is_merged_without_false_conflict():
    pages = [
        {"page": 4, "data": {"components": [{"name": "benzyl alcohol", "role": "reactant",
                                              "quantity": 21.6, "unit": "mg"}]}},
        {"page": 5, "data": [{"name": "benzyl alcohol", "role": "reactant", "quantity": "0.200 mmol"}]},
    ]
    r = row(px.build_review_table(pages), "benzyl alcohol")
    assert r["Page"] == "4, 5" and "different amounts" not in r["Flag"]


def test_unparsable_page_and_empty_document():
    assert px.build_review_table([{"page": 1, "data": None}]).empty
    assert px.build_review_table([]).empty
    assert px.to_app_tables(px.build_review_table([]))["reactants_df"].empty


def test_unknown_compound_is_flagged_not_dropped():
    df = table([{"name": "compound X-17", "role": "reactant", "quantity": 50, "unit": "mg"},
                {"name": "my product", "role": "product", "quantity": 40, "unit": "mg"}])
    assert "MW unknown" in row(df, "compound X-17")["Flag"]


def test_user_supplied_smiles_fills_mw_and_carbons():
    df = table([{"name": "compound X-17", "role": "reactant", "quantity": 78.11, "unit": "mg"}])
    ed = df.copy()
    ed.loc[0, "SMILES"] = "c1ccccc1"
    t = px.to_app_tables(ed, df)
    assert t["reactants_df"].loc[0, "MW (g/mol)"] == pytest.approx(78.11, abs=0.01)
    assert t["reactants_df"].loc[0, "Moles charged (mol)"] == pytest.approx(1e-3, rel=1e-3)
    assert t["reactants_df"].loc[0, "Carbons/molecule"] == 6


@pytest.mark.parametrize("text,status", [
    ('```json\n{"components": []}\n```', "ok"),
    ("{'components': [{'name': 'x', 'quantity': None}]}", "ok"),
    ('{"components":[{"name":"A","quantity":2 x 20,"unit":"mL"},]}', "repaired JSON"),
    ('{"components":[{"name":"A","quantity":1},{"name":"B","quan', "repaired (answer cut off"),
    ("The page contains no experimental section.", "no JSON in answer"),
])
def test_model_answer_shapes(text, status):
    data, st = px.parse_json_status(text)
    assert st.startswith(status)
    assert (data is None) == (status == "no JSON in answer")


@pytest.mark.parametrize("value,expected", [
    ("−78", -78.0), ("20 mg", 20.0), ("2 × 20 mL", 40.0), ("1,5", 1.5), ("1,250", 1250.0),
    ("20-25", 22.5), ("-10 to 0", -5.0), ([20, 20], 40.0), ("rt", None), (None, None),
])
def test_number_parsing(value, expected):
    assert px._num(value) == (pytest.approx(expected) if expected is not None else None)
