"""Page 5: the original and chosen alternatives side by side, and the report export."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from gms import breakeven, greenness, report
from gms.metrics import registry
from ui import components as ui
from ui import state

state.init()
ss = st.session_state
st.title("Compare and export")
a = state.need_reaction()
if a is None:
    st.stop()
r, score, cands = a["reaction"], a["score"], a["candidates"]
passed = [c for c in cands if c.status == "passed"]
y0 = score.metrics.get("yield")

st.subheader("Side by side")
accepted = [c.id for c in passed if ss["decisions"].get(c.id, {}).get("decision") == "accept"]
titles = {c.id: c.title for c in passed}
chosen = st.multiselect("Alternatives to compare with the original", list(titles), format_func=titles.get,
                        default=accepted or [c.id for c in passed[:2]], max_selections=4)
if not passed:
    st.info("No alternative passed the no-regression check, so there is nothing to compare. "
            "The report below still contains the full analysis.")
cols = {"Original": score}
yields = {"Original": y0}
for cid in chosen:
    c = next(x for x in passed if x.id == cid)
    y = ss["scenario_yields"].get(cid, (y0 or 80) / 100)
    cols[c.title] = breakeven.scenario(c.reaction, y)
    yields[c.title] = 100 * y
rows = [{"Metric": "Yield assumed (%)", **{k: ui.fmt(v) for k, v in yields.items()}},
        {"Metric": "Verdict", **{k: s.greenness["label"] for k, s in cols.items()}},
        {"Metric": "Combined score (0-1)", **{k: ui.fmt(s.greenness["score"]) for k, s in cols.items()}}]
for key in registry.display_order():
    m = registry.get(key)
    rows.append({"Metric": f"{m.label} ({m.unit})" if m.unit else m.label,
                 **{k: ui.fmt(s.metrics.get(key)) + (" (est.)" if key in s.estimated_metrics else "")
                    for k, s in cols.items()}})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
if chosen:
    st.caption("The alternatives are hypothetical. Their yield is the value you set on the Greener alternatives "
               "page (default: the original yield, which is not a claim that the yields are equal). "
               "'(est.)' marks a value that depends on an estimated amount.")

if len(cols) > 1:
    axes = list(score.greenness["desirabilities"])
    names = {"ae": "Atom economy", "rme": "RME", "pmi": "PMI", "ecoscale": "EcoScale", "hazard": "Hazard",
             "solvent": "Solvent class"}
    fig = go.Figure()
    for label, s in cols.items():
        d = s.greenness["desirabilities"]
        vals = [100 * d.get(k, 0.0) for k in axes]
        fig.add_trace(go.Scatterpolar(r=vals + vals[:1], theta=[names[k] for k in axes] + [names[axes[0]]],
                                      name=label[:40], fill="toself", opacity=0.55))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), height=460,
                      margin=dict(t=30, b=30, l=40, r=40))
    st.plotly_chart(fig, width="stretch")
    st.caption("Each axis is the desirability of one part of the combined score: 100 = at or beyond the target, "
               "0 = at or beyond the poor limit (config/scoring.yaml).")

st.subheader("Export the report")
st.write("The report contains the reaction, all metrics, the hazard data and its sources, the diagnosis, every "
         "alternative with its evidence, check and break-even yields, your decisions, the list of estimates and "
         "assumptions, the configuration used and the app version.")
rep = report.build(r, score, a["findings"], cands, ss["decisions"],
                   {cid: ss["scenario_yields"][cid] for cid in chosen if cid in ss["scenario_yields"]})
stem = "green_metrics_report"
c1, c2, c3 = st.columns(3)
c1.download_button("Download PDF", report.to_pdf(rep), f"{stem}.pdf", "application/pdf")
c2.download_button("Download CSV", report.to_csv(rep), f"{stem}.csv", "text/csv")
c3.download_button("Download JSON", report.to_json(rep), f"{stem}.json", "application/json")
sens = greenness.sensitivity(score.greenness)
if sens:
    st.caption(f"Verdict robustness: same verdict in {100 * sens['same_verdict_share']:.0f}% of random weightings.")
ui.disclaimer()
