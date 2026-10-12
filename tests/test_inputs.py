"""Cleaning of the editable tables before they reach the calculators."""

import pandas as pd

from modules.inputs import AUX_DEFAULTS, REACTANT_DEFAULTS, clean_rows, with_defaults


def test_blank_and_partial_rows():
    df = pd.DataFrame([
        {"Name": "A", "MW (g/mol)": 100.0, "Moles charged (mol)": 0.1, "Mass charged (g)": 10.0,
         "Carbons/molecule": 7, "Stoich. ratio (per limiting reagent)": 1.0, "Limiting reagent?": True},
        {"Name": None, "MW (g/mol)": None, "Moles charged (mol)": None, "Mass charged (g)": None,
         "Carbons/molecule": None, "Stoich. ratio (per limiting reagent)": None, "Limiting reagent?": None},
        {"Name": "", "MW (g/mol)": "abc", "Moles charged (mol)": None, "Mass charged (g)": 5.0,
         "Carbons/molecule": None, "Stoich. ratio (per limiting reagent)": None, "Limiting reagent?": None},
    ])
    rows = clean_rows(df, REACTANT_DEFAULTS)
    assert len(rows) == 2                                     # the empty "+" row is dropped
    assert rows[0]["Benign?"] is False                        # column added with its default
    assert rows[1]["Name"] == "Unnamed 2" and rows[1]["MW (g/mol)"] == 0.0
    assert rows[1]["Stoich. ratio (per limiting reagent)"] == 1.0
    assert isinstance(rows[1]["Carbons/molecule"], int)


def test_with_defaults_keeps_extra_columns_last():
    d = with_defaults(pd.DataFrame({"Name": ["x"], "Mass (g)": [1.0], "Note": ["n"]}), AUX_DEFAULTS)
    assert list(d.columns) == ["Name", "Mass (g)", "Benign?", "Note"]
