"""Reference page: global hazard and toxicity ratios (Tamargo et al. 2024, Secs. 10.1.2.2-10.1.2.3).
These need life-cycle data that a single procedure does not contain, so they are entered by hand."""

import pandas as pd
import streamlit as st

from gms.metrics import hazard_ratios as hz
from ui import components as ui
from ui import state

state.init()
st.title("Life-cycle hazard ratios")
st.write("These impact-based ratios need life-cycle data (emissions, targets, toxicity values) that a laboratory "
         "procedure does not contain. Enter the values yourself. The ratios follow the printed equations of "
         "Tamargo et al. (2024): Y = target / actual, so Y of 1 or more is within the target. The table also "
         "gives actual / target, which the chapter's text describes.")

pairs = [("Acidification potential (SO₂-eq)", "ap"), ("Ozone depletion potential (CFC-11-eq)", "odp"),
         ("Smog formation potential (O₃)", "sfp"), ("Global warming potential (CO₂-eq)", "gwp"),
         ("Abiotic depletion potential (Sb-eq)", "adp"), ("Air toxicity (X_AT)", "at"),
         ("Water ecotoxicity (1,4-DCB-eq)", "we")]
st.subheader("Burden and target")
vals = {}
for label, key in pairs:
    c1, c2 = st.columns(2)
    vals[key] = (c1.number_input(f"{label}: actual", min_value=0.0, value=1.0, key=f"{key}_a"),
                 c2.number_input(f"{label}: target", min_value=0.0, value=1.0, key=f"{key}_t"))
st.subheader("Toxicity and fate")
c1, c2 = st.columns(2)
c_water = c1.number_input("Concentration in water, C_w", min_value=0.0, value=1.0)
ld50 = c2.number_input("LD50", min_value=0.0001, value=100.0)
c_air = c1.number_input("Concentration in air, C_a", min_value=0.0, value=1.0)
lc50 = c2.number_input("LC50", min_value=0.0001, value=100.0)
log_kow = c1.number_input("log Kow", value=2.0)
c0 = c2.number_input("Initial concentration, C0", min_value=0.0, value=1.0)
k = c1.number_input("Degradation rate constant, k", min_value=0.0001, value=0.1)

if st.button("Calculate", type="primary"):
    rows = [{"Quantity": label.split(" (")[0], "Y = target / actual": hz.hazard_ratio(*vals[key]),
             "actual / target": hz.burden_ratio(*vals[key]),
             "Reading": "within target" if hz.hazard_ratio(*vals[key]) >= 1 else "exceeds target"}
            for label, key in pairs]
    st.dataframe(pd.DataFrame(rows).assign(**{c: lambda d, c=c: d[c].map(ui.fmt) for c in
                                              ("Y = target / actual", "actual / target")}),
                 hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame([
        {"Quantity": "Ingestion toxicity potential (C_w / LD50)", "Value": ui.fmt(hz.ingestion_toxicity_potential(c_water, ld50))},
        {"Quantity": "Inhalation toxicity potential (C_a / LC50)", "Value": ui.fmt(hz.inhalation_toxicity_potential(c_air, lc50))},
        {"Quantity": "Bioaccumulation potential (from log Kow)", "Value": hz.bioaccumulation_rating(log_kow)},
        {"Quantity": "Environmental persistence (C0 / k)", "Value": ui.fmt(hz.environmental_persistence(c0, k))},
    ]), hide_index=True, width="stretch")
