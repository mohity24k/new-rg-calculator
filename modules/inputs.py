"""
inputs.py
---------
Cleaning of the editable tables (st.data_editor) before they reach the calculators:
blank rows, missing columns, empty cells and text typed into number cells.
"""

import pandas as pd

REACTANT_DEFAULTS = {
    "Name": "", "MW (g/mol)": 0.0, "Moles charged (mol)": 0.0, "Mass charged (g)": 0.0,
    "Carbons/molecule": 0, "Stoich. ratio (per limiting reagent)": 1.0,
    "Limiting reagent?": False, "Benign?": False,
}
AUX_DEFAULTS = {"Name": "", "Mass (g)": 0.0, "Benign?": False}


def with_defaults(df: pd.DataFrame, defaults: dict) -> pd.DataFrame:
    """Make sure every expected column exists (e.g. tables pre-filled from a PDF lack 'Benign?')."""
    d = df.copy()
    for col, val in defaults.items():
        if col not in d:
            d[col] = val
    return d[list(defaults) + [c for c in d.columns if c not in defaults]]


def clean_rows(df: pd.DataFrame, defaults: dict) -> list:
    """Editor rows as dicts with typed values. Blank rows (added with + and left empty) are
    dropped; empty cells get the column default; text in number cells becomes 0."""
    rows = []
    for _, row in with_defaults(df, defaults).iterrows():
        rec, blank = {}, True
        for col, dflt in defaults.items():
            val = row.get(col)
            missing = val is None or (not isinstance(val, str) and pd.isna(val))
            if isinstance(dflt, bool):
                rec[col] = bool(val) if not missing else dflt
            elif isinstance(dflt, (int, float)):
                num = pd.to_numeric(val, errors="coerce") if not missing else float("nan")
                rec[col] = type(dflt)(num) if pd.notna(num) else dflt
                if pd.notna(num) and num != 0:
                    blank = False
            else:
                rec[col] = str(val).strip() if not missing else dflt
                if rec[col]:
                    blank = False
        if not blank:
            rec["Name"] = rec["Name"] or f"Unnamed {len(rows) + 1}"
            rows.append(rec)
    return rows
