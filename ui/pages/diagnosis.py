"""Page 3: which material or step causes the problem, and what fixing it would gain."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from gms import diagnosis
from gms.metrics import registry
from ui import components as ui
from ui import state

state.init()
st.title("Diagnosis")
a = state.need_reaction()
if a is None:
    st.stop()
r, score, findings = a["reaction"], a["score"], a["findings"]
st.write("Where the mass, the hazard and the penalty points come from. Each finding shows the gain of an "
         "idealised fix. The gain is computed: the fix is applied to a copy of the reaction and the calculator "
         "runs again. Real alternatives are on the next page.")

# --- where the mass goes ----------------------------------------------------------------------
st.subheader("Where the input mass goes")
pm = score.totals["product_mass_g"]
contrib = sorted((c for c in score.contributions if c["mass_g"] > 0), key=lambda c: c["mass_g"])
if not contrib or pm <= 0:
    st.info("The mass breakdown needs the masses of the inputs and of the product.")
else:
    stage_colour = {"reaction": "#1f6f8b", "workup": "#a86400", "purification": "#7a4fa3"}
    c1, c2 = st.columns([2, 1])
    with c1:
        fig = go.Figure()
        for stage, colour in stage_colour.items():
            part = [c for c in contrib if c["stage"] == stage]
            fig.add_trace(go.Bar(
                y=[c["name"] + (" (estimate)" if c["estimated"] else "") for c in part],
                x=[c["pmi_contribution"] for c in part], orientation="h", name=stage.capitalize(),
                marker_color=colour, hovertemplate="%{y}: %{x:.3g} kg per kg product<extra></extra>"))
        fig.update_layout(height=max(260, 26 * len(contrib) + 90), margin=dict(t=10, b=40, l=10, r=10),
                          xaxis_title="Contribution to PMI (kg per kg of product)", legend_title="Stage",
                          yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig, width="stretch")
    with c2:
        bd = diagnosis.stage_breakdown(score)
        total = score.totals["total_input_mass_g"]
        st.dataframe(pd.DataFrame([{"Stage": k.capitalize(), "Mass (g)": v["mass_g"], "PMI part": v["pmi"],
                                    "Share": f"{100 * v['mass_g'] / total:.0f}%"} for k, v in bd["by_stage"].items()]),
                     hide_index=True, width="stretch",
                     column_config={"Mass (g)": st.column_config.NumberColumn(format="%.3g"),
                                    "PMI part": st.column_config.NumberColumn(format="%.3g")})
        st.dataframe(pd.DataFrame([{"Role": k.replace("_", " "), "Mass (g)": v["mass_g"],
                                    "Share": f"{100 * v['mass_g'] / total:.0f}%"}
                                   for k, v in sorted(bd["by_role"].items(), key=lambda kv: -kv[1]["mass_g"])]),
                     hide_index=True, width="stretch",
                     column_config={"Mass (g)": st.column_config.NumberColumn(format="%.3g")})

# --- findings ---------------------------------------------------------------------------------
st.subheader("Findings")
if not findings:
    st.success("No finding was raised with the thresholds in config/diagnosis.yaml.")
st.caption(f"Now: PMI {ui.fmt(score.metrics.get('pmi'))}, EcoScale {ui.fmt(score.metrics.get('ecoscale'))}, "
           f"combined score {ui.fmt(score.greenness['score'])} ({score.greenness['label']}). "
           "Findings are sorted by the gain in the combined score.")
for i, f in enumerate(findings):
    gain = f"combined score {ui.signed(f.greenness_gain)}" if f.greenness_gain is not None else ""
    with st.expander(f"{i + 1}. {f.title}  ({gain})", expanded=i < 3):
        st.write(f.detail)
        st.markdown(f"**What if:** {ui.esc(f.what_if)}")
        rows = []
        for k, change in f.gains.items():
            if change is None or abs(change) < 1e-9:
                continue
            m = registry.get(k)
            rows.append({"Metric": m.label, "Now": score.metrics.get(k), "After the fix": f.after.get(k),
                         "Gain": ui.signed(change), "Unit": m.unit})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"Now": st.column_config.NumberColumn(format="%.4g"),
                                        "After the fix": st.column_config.NumberColumn(format="%.4g")})
        st.caption(f"Verdict after this fix alone: {f.verdict_after}. The fix is an idealised target, not a suggestion.")
st.page_link("ui/pages/alternatives.py", label="Next: greener alternatives", icon=":material/swap_horiz:")
ui.disclaimer()
