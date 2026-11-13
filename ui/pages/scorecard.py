"""Page 2: is this reaction green? Verdict with reasons, every metric, EcoScale and hazard data."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from gms import greenness, precedents, scoring
from gms.chem import classify
from gms.metrics import registry
from ui import components as ui
from ui import state

state.init()
st.title("Score card")
a = state.need_reaction()
if a is None:
    st.stop()
r, score = a["reaction"], a["score"]
g = score.greenness
st.caption(f"{r.name or 'Unnamed reaction'} · {classify.CLASSES.get(r.reaction_class, 'class not recognised')}")

# --- verdict ---------------------------------------------------------------------------------
left, right = st.columns([1, 2])
with left:
    ui.verdict_banner(g)
    sens = greenness.sensitivity(g)
    if sens:
        st.caption(f"Robustness: the verdict stays the same in {100 * sens['same_verdict_share']:.0f}% of "
                   f"{sens['samples']} random weightings (each weight varied by ±{100 * sens['spread']:.0f}%). "
                   f"Score range: {sens['score_low']:.2f} to {sens['score_high']:.2f}.")
    st.caption("The verdict is computed from the metrics below with weights and limits that are provisional "
               "defaults (see Methods and limitations). No model is involved.")
with right:
    if g["caps"]:
        st.markdown("**Cannot be called green because**")
        for x in g["caps"]:
            st.markdown(f"- {ui.esc(x)}")
    if g["reasons"]:
        st.markdown("**Main reasons against** (largest effect first)")
        for x in g["reasons"]:
            st.markdown(f"- {ui.esc(x)}")
    if g["strengths"]:
        st.markdown("**In favour**")
        for x in g["strengths"]:
            st.markdown(f"- {ui.esc(x)}")
    for x in g["notes"]:
        st.caption(x)

# --- metrics ---------------------------------------------------------------------------------
st.subheader("Metrics")
keys = registry.display_order()
for i in range(0, len(keys), 4):
    for col, key in zip(st.columns(4), keys[i:i + 4]):
        with col:
            ui.metric_card(key, score.metrics.get(key), key in score.estimated_metrics)
with st.expander("What the metrics mean, and the thresholds"):
    st.dataframe(pd.DataFrame([{
        "Metric": f"{m.label} ({m.short})", "Meaning": m.help, "Better when": m.direction,
        "Good": m.good, "Moderate": m.moderate, "Formula source": m.reference} for m in registry.metrics().values()]),
        hide_index=True, width="stretch")
    st.caption("Thresholds are in config/metrics.yaml. The mass metrics of a laboratory procedure with workup and "
               "chromatography are usually far from the 'good' thresholds, which describe an efficient process.")

if score.estimates:
    st.subheader("Estimates and assumptions")
    st.markdown(ui.est_badge() + " marks every metric that depends on one of these.", unsafe_allow_html=True)
    for e in score.estimates:
        st.markdown(f"- {ui.esc(e)}")
    if any(c.estimated and c.role != "product" for c in r.components):
        m_only = scoring.score(scoring.measured_only(r))
        st.caption("Without the estimated materials: " + ", ".join(
            f"{registry.get(k).short} {ui.fmt(m_only.metrics.get(k))}" for k in ("pmi", "e_factor", "mp")) + ".")
for w in score.warnings:
    st.warning(w)

# --- class benchmark --------------------------------------------------------------------------
st.subheader("Compared with similar reactions")
if not precedents.available(r.reaction_class):
    st.info("No comparison data for this reaction class. Comparison data exists for amide coupling, "
            "Suzuki-Miyaura coupling and reductive amination.")
else:
    meta = precedents.meta()
    n_cls = precedents.dataset()["classes"][r.reaction_class]["n"]
    lim = next((c for c in r.by_role("reactant", "reagent") if c.limiting), None)
    vol = sum(c.volume_ml or 0.0 for c in r.by_role("reaction_solvent"))
    mine = {"yield": score.metrics.get("yield"),
            "ml_per_mmol": vol / (lim.moles * 1000) if lim and lim.moles and vol else None}
    rows = []
    for q, label, unit in (("yield", "Yield", "%"), ("ml_per_mmol", "Reaction solvent per mmol", "mL/mmol")):
        t = precedents.typical(r.reaction_class, q)
        if not t:
            continue
        pc = precedents.percentile(r.reaction_class, q, mine[q])
        rows.append({"Quantity": label, "This reaction": mine[q], "Unit": unit,
                     "Typical range (middle half)": f"{t['q1']:.3g} to {t['q3']:.3g}", "Median": t["median"],
                     "Precedents with a value": t["n"],
                     "Position": f"higher than about {pc['percentile']}% of precedents" if pc else "n/a"})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"This reaction": st.column_config.NumberColumn(format="%.3g")})
    st.caption(f"Source: {meta.get('name')}; {n_cls:,} reactions of this class. {meta.get('limits', '')} "
               "No PMI percentile is shown: the patent data has no workup and purification amounts, so a PMI "
               "comparison with a full laboratory procedure would be misleading.")

# --- EcoScale ---------------------------------------------------------------------------------
st.subheader("EcoScale")
eco = score.ecoscale
if eco is None:
    st.info("The EcoScale needs a yield. Give the product amount, or a reported yield.")
else:
    c1, c2 = st.columns([1, 1])
    labels = {"yield": "Yield", "price": "Price", "safety": "Safety (hazard symbols)", "setup": "Technical set-up",
              "temp_time": "Temperature and time", "workup": "Workup and purification"}
    with c1:
        st.dataframe(pd.DataFrame([{"Penalty": labels[k], "Points": v} for k, v in eco["penalties"].items()]
                                  + [{"Penalty": "Total", "Points": eco["total_penalty"]},
                                     {"Penalty": "EcoScale (100 − total)", "Points": eco["score"]}]),
                     hide_index=True, width="stretch")
        st.caption(f"Rating after Van Aken et al.: {eco['rating']}. Conditions: {eco['temp_time']}; "
                   f"set-up: {', '.join(eco['setups'])}."
                   + ("" if eco["price_assessed"] else " Price not assessed (0 points assumed)."))
    with c2:
        fig = go.Figure(go.Bar(x=list(eco["penalties"].values()), y=[labels[k] for k in eco["penalties"]],
                               orientation="h", marker_color="#a86400"))
        fig.update_layout(height=260, margin=dict(t=10, b=30, l=10, r=10), xaxis_title="Penalty points")
        st.plotly_chart(fig, width="stretch")
    if eco["safety_by_material"]:
        st.caption("Safety penalties, derived from the GHS hazard statements: " + "; ".join(
            f"{n}: {', '.join(s.split(' - ')[0] for s in syms)}" for n, syms in eco["safety_by_material"].items()) + ".")

# --- hazards ----------------------------------------------------------------------------------
st.subheader("Hazard data of the materials")
status = {"classified": "GHS statements found", "not_classified": "no hazard statement",
          "no_data": "no GHS data found", "unavailable": "lookup not possible now"}
st.dataframe(pd.DataFrame([{
    "Material": h["name"], "Role": h["role"].replace("_", " "), "Hazard statements": ", ".join(h["h_codes"]),
    "Health": h["health"], "Safety": h["safety"], "Environment": h["environment"],
    "High hazard": "yes" if h["high_hazard"] else "", "CHEM21 solvent ranking": h["chem21_ranking"] or "",
    "Data": status[h["status"]], "Source": h["source"]} for h in score.hazards]), hide_index=True, width="stretch")
st.caption("Scores: 1 (low) to 10 (severe), from the hazard statements (config/hazard.yaml; health and "
           "environment follow the CHEM21 guide). Statements come from PubChem: the EU harmonised classification "
           "where it exists, otherwise company notifications to ECHA. 'No GHS data' means unknown, not safe. "
           "Always read the safety data sheet.")
st.page_link("ui/pages/diagnosis.py", label="Next: diagnosis", icon=":material/troubleshoot:")
ui.disclaimer()
