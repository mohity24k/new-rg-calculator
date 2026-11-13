"""Page 4: greener alternatives. Each one shows its evidence, the no-regression check, the
yield scenarios and the break-even yield, and can be accepted or rejected with a comment."""

import pandas as pd
import streamlit as st

from gms import breakeven, feedback, scoring, suggest
from gms.metrics import registry
from ui import components as ui
from ui import state

state.init()
ss = st.session_state
st.title("Greener alternatives")
a = state.need_reaction()
if a is None:
    st.stop()
r, score, cands = a["reaction"], a["score"], a["candidates"]
passed = [c for c in cands if c.status == "passed"]
rejected = [c for c in cands if c.status == "rejected"]
skipped = [c for c in cands if c.status == "not_applicable"]
y0 = score.metrics.get("yield")

st.write("Each alternative below is an edited copy of your reaction, scored by the same calculator. "
         "It is shown only if it passed the no-regression check: same product, hazard not worse, no metric worse "
         "beyond its tolerance, and cost not worse where prices are known.")
st.info("**About the yield.** The app does not predict the yield of an untested change. The changes are computed "
        f"at the original yield ({ui.fmt(y0)}%), which is **not a claim that the yields are equal**. Use the yield "
        "slider to see other outcomes. The break-even yield is the lowest yield at which the alternative is still "
        "at least as good as the original.")
if r.reaction_class is None:
    st.warning("The reaction class was not recognised, so only workup and purification alternatives can be offered.")


def _precedent_text(p: dict) -> str:
    if not p:
        return "not searched"
    if not p.get("available"):
        return p.get("reason", "not available")
    if p.get("note"):
        return p["note"]
    if "n_sole" in p:
        txt = (f"the only listed solvent in {p['n_sole']:,} of {p['n_class']:,} reactions of this class in the "
               f"dataset (listed together with other solvents in {p['n'] - p['n_sole']:,} more, where it can be an "
               "extraction solvent)")
    else:
        txt = f"used in {p['n']:,} of {p['n_class']:,} reactions of this class in the dataset"
    if p.get("yield_median") is not None:
        txt += (f"; reported yields of those reactions: middle half {p['yield_q1']:.0f}% to {p['yield_q3']:.0f}% "
                "(other substrates; not a prediction for yours)")
    return txt + "."


def _breakeven_text(b: dict) -> str:
    return {"always": "better at any yield", "never": "not reachable, even at 100% yield",
            "at": f"{100 * (b.get('yield') or 0):.0f}%", "unknown": "cannot be computed"}[b["status"]]


def _record(c, decision: str, comment: str) -> None:
    ss["decisions"][c.id] = {"decision": decision, "comment": comment.strip()}
    try:
        feedback.record(state.app_settings().data_dir / "feedback.db", ss["session_id"], r.to_dict(), decision,
                        comment, c.summary())
    except Exception:                       # the decision stays in the session and in the report
        state.log.exception("feedback could not be stored")
        st.warning("Your decision is kept for this session and for the report, but it could not be saved on the server.")


def _candidate(c, shown: bool) -> None:
    conf = ui.badge(f"confidence: {c.confidence}", ui.CONFIDENCE_COLOUR[c.confidence])
    kind = ui.badge(suggest.KIND_LABEL.get(c.kind, c.kind), "#374151")
    est = ui.est_badge() if c.assumptions else ""
    st.markdown(kind + conf + est, unsafe_allow_html=True)
    if c.problem:
        st.markdown(f"**Why change it:** {ui.md(c.problem)}")

    st.markdown("**Evidence**")
    st.markdown(f"- Origin: rule `{ui.md(c.rule_id)}`; {suggest.EVIDENCE_LABEL.get(c.evidence_level, c.evidence_level)}.")
    for s in c.sources:
        link = f" [doi:{s['doi']}](https://doi.org/{s['doi']})" if s.get("doi") else ""
        st.markdown(f"- Reference: {ui.md(s.get('citation', ''))}{link}"
                    + ("" if s.get("verified") else " (the content of this reference was not verified)"))
    st.markdown(f"- Precedents: {ui.md(_precedent_text(c.precedent))}")
    for ex in (c.precedent.get("examples") or [])[:3]:
        st.caption(f"Example: {ui.md(ex)}")
    st.markdown(f"- Confidence: **{c.confidence}** ({ui.md(c.confidence_reason)}).")
    if c.scope:
        st.markdown(f"**Where it has worked:** {ui.md(c.scope)}")
    if c.caveats:
        st.markdown(f"**Caveats:** {ui.md(c.caveats)}")
    if c.assumptions:
        st.markdown("**Estimated amounts in this alternative** (" + ui.est_badge() + "):", unsafe_allow_html=True)
        for x in c.assumptions:
            st.markdown(f"- {ui.md(x)}")

    if c.check is None:
        return
    st.markdown("**No-regression check: what was compared**")
    st.dataframe(pd.DataFrame([{
        "Group": row["group"], "Compared": row["item"],
        "Original": ui.fmt(row["original"]) if row["original"] not in (None, "") else "",
        "Alternative": ui.fmt(row["candidate"]) if row["candidate"] not in (None, "") else "",
        "Result": row["result"], "Detail": row["detail"]} for row in c.check["rows"]]),
        hide_index=True, width="stretch")
    st.caption("Values for the alternative are at the original yield. 'tolerated' = worse, but inside the "
               "tolerance of config/metrics.yaml. 'trade-off' = this hazard dimension gets worse while the overall "
               "worst hazard score does not.")
    if not shown:
        return

    st.markdown("**Yield scenarios**")
    if scoring.theoretical_product_mass(c.reaction) is None:
        st.info("Yield scenarios and break-even yields need the molecular weight of the product and the amount "
                "of the limiting reactant. Enter a SMILES or the molecular weight of the product on the Reaction page.")
        _decision(c)
        return
    be = c.breakeven
    st.dataframe(pd.DataFrame([{"Break-even yield for": label, "Lowest yield at which the alternative is not worse": _breakeven_text(be[k])}
                               for k, label in (("pmi", "Process mass intensity"), ("rme", "Reaction mass efficiency"),
                                                ("ecoscale", "EcoScale"), ("greenness", "Combined score"))]),
                 hide_index=True, width="stretch")
    default = int(round(min(100.0, y0))) if y0 is not None else 80
    y = st.slider("Yield of the alternative (%)", 1, 100, int(round(100 * ss["scenario_yields"].get(c.id, default / 100))),
                  key=f"yield_{c.id}", help="Your assumption. The app does not predict this value.")
    ss["scenario_yields"][c.id] = y / 100
    sc = breakeven.scenario(c.reaction, y / 100)
    rows = []
    for k in registry.display_order():
        m = registry.get(k)
        if not m.yield_dependent and k != "ae":
            continue
        o, v = score.metrics.get(k), sc.metrics.get(k)
        ch = registry.change(k, o, v)
        rows.append({"Metric": m.label, "Original": ui.fmt(o) + (" (est.)" if k in score.estimated_metrics else ""),
                     f"Alternative at {y}% yield": ui.fmt(v) + (" (est.)" if k in sc.estimated_metrics else ""),
                     "Change": ("better " if ch and ch > 1e-9 else "worse " if ch and ch < -1e-9 else "") + ui.signed(ch),
                     "Unit": m.unit})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"Verdict of the alternative at {y}% yield: {sc.greenness['label']} "
               f"(combined score {ui.fmt(sc.greenness['score'])}; original {ui.fmt(score.greenness['score'])}, "
               f"{score.greenness['label']}). Scenario values are hypothetical. '(est.)' marks a value that depends "
               "on an estimated amount.")
    _decision(c)


def _decision(c) -> None:
    st.markdown("**Your decision**")
    prev = ss["decisions"].get(c.id)
    if prev:
        st.success(f"Recorded: {prev['decision']}" + (f". Comment: {prev['comment']}" if prev["comment"] else ""))
    comment = st.text_area("Comment (optional): would you try this? Why, or why not?", key=f"comment_{c.id}",
                           max_chars=feedback.MAX_COMMENT, height=80)
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Accept", key=f"accept_{c.id}", type="primary"):
        _record(c, "accept", comment)
        st.rerun()
    if b2.button("Reject", key=f"reject_{c.id}"):
        _record(c, "reject", comment)
        st.rerun()


st.subheader(f"Alternatives that passed the check ({len(passed)})")
if not passed:
    st.info("No alternative passed the no-regression check for this reaction. The candidates that were "
            "considered are listed below with the reason.")
for i, c in enumerate(passed):
    d = ss["decisions"].get(c.id)
    mark = {"accept": " · accepted", "reject": " · rejected by you"}.get(d["decision"], "") if d else ""
    gain = f"combined score {ui.signed(c.greenness_gain)} at the original yield" if c.greenness_gain is not None else ""
    with st.expander(f"{i + 1}. {ui.md(c.title)}  ({gain}){mark}", expanded=i == 0):
        _candidate(c, True)

st.subheader(f"Considered, but not suggested ({len(rejected) + len(skipped)})")
st.caption("These candidates failed the no-regression check or could not be computed. They are not suggestions. "
           "They are listed so that you can see what was considered and why it was dropped.")
for c in rejected:
    with st.expander(f"{ui.md(c.title)}: rejected"):
        st.error("Rejected: " + ui.md(c.reason))
        _candidate(c, False)
for c in skipped:
    with st.expander(f"{ui.md(c.title)}: not computed"):
        st.warning(ui.md(c.reason))

st.subheader("General comment")
general = st.text_area("Is an alternative missing that you know to work? Is something here wrong?",
                       max_chars=feedback.MAX_COMMENT, key="general_comment", height=80)
if st.button("Send comment") and general.strip():
    try:
        feedback.record(state.app_settings().data_dir / "feedback.db", ss["session_id"], r.to_dict(), "comment", general)
        st.success("Thank you. The comment was saved.")
    except Exception:
        state.log.exception("feedback could not be stored")
        st.warning("The comment could not be saved on the server.")
st.page_link("ui/pages/compare.py", label="Next: compare and export", icon=":material/compare_arrows:")
ui.disclaimer()
