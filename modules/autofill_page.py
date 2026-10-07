"""
autofill_page.py
----------------
Streamlit page: upload PDF -> extract with Qwen2.5-VL -> human review -> apply to calculators.
"""

import pandas as pd
import streamlit as st

from modules import eco_scale as es
from modules import pdf_extraction as px


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def _idx(options, value) -> int:
    options = list(options)
    return options.index(value) if value in options else 0


def render_autofill_page():
    st.title("📄 Upload PDF & Auto-fill")
    st.caption(
        "Upload a paper or SI. Qwen2.5-VL reads the experimental section, the app resolves "
        "structures and computes MW / moles / masses, and you only have to verify the result "
        "before it is sent to the calculators."
    )

    st.caption(f"pipeline version {px.PIPELINE_VERSION}")
    with st.expander("⚙️ Extraction server settings"):
        base_url = st.text_input("OpenAI-compatible endpoint", _secret("VLM_BASE_URL", "http://localhost:8000/v1"))
        model = st.text_input("Model name", _secret("VLM_MODEL", px.DEFAULT_MODEL))
        api_key = st.text_input("API key", _secret("VLM_API_KEY", "EMPTY"), type="password")

    up = st.file_uploader("Upload paper / SI (PDF)", type="pdf")
    if up is None:
        return
    pdf = up.getvalue()
    n = px.page_count(pdf)

    c1, c2, c3 = st.columns([1, 1, 2])
    first = c1.number_input("First page", 1, n, 1)
    last = c2.number_input("Last page", 1, n, n)
    c3.write("")
    if c3.button("🔍 Extract", type="primary"):
        if first > last:
            st.error("First page must be ≤ last page.")
            return
        bar = st.progress(0.0, "Starting…")
        try:
            pages = px.extract_document(
                pdf, range(first - 1, last), base_url, model, api_key,
                on_progress=lambda i, t: bar.progress(i / t, f"Page {i}/{t}"))
        except Exception as e:
            bar.empty()
            st.error(f"Could not reach the extraction server: {e}")
            return
        bar.empty()
        prev = st.session_state.get("extraction", {})
        st.session_state.extraction = {
            "id": prev.get("id", 0) + 1, "pdf": pdf, "pages": pages,
            "df": px.build_review_table(pages), "meta": px.merge_meta(pages)}

    ex = st.session_state.get("extraction")
    if not ex or ex["pdf"] != pdf:
        return

    st.markdown("---")
    bad = [p for p in ex["pages"] if p["data"] is None]
    st.subheader("Extraction status per page")
    if bad:
        st.error("Pages that could not be read: " + ", ".join(str(p["page"]) for p in bad)
                 + ". Their content is NOT in the table below. See 'Raw model output'.")
    st.dataframe(
        pd.DataFrame([{"Page": p["page"], "Status": p["status"], "Compounds": p["n_components"]}
                      for p in ex["pages"]]),
        hide_index=True, use_container_width=True)

    st.subheader("1. Review extracted materials")
    n_flag = int((ex["df"]["Flag"] != "").sum()) if not ex["df"].empty else 0
    st.caption(f"{len(ex['df'])} rows extracted, **{n_flag} need attention** (see the Flag column). "
               "Change a row's Role to *ignore* to drop it. Edit any value; add rows with ＋.")

    left, right = st.columns([3, 2])
    with right:
        shown = [p["page"] for p in ex["pages"]]
        pg = st.selectbox("Source page preview", shown)
        st.image(px.render_page(ex["pdf"], pg - 1, dpi=110), use_container_width=True)
    with left:
        edited = st.data_editor(
            ex["df"], num_rows="dynamic", hide_index=True, use_container_width=True,
            key=f"review_editor_{ex['id']}",
            column_config={
                "Role": st.column_config.SelectboxColumn(options=px.ROLES, required=True),
                "Mass (g)": st.column_config.NumberColumn(format="%.4f"),
                "Moles (mol)": st.column_config.NumberColumn(format="%.5f"),
                "Flag": st.column_config.TextColumn(disabled=True),
                "SMILES": st.column_config.TextColumn(disabled=True),
                "Page": st.column_config.TextColumn(disabled=True),
            })

    st.subheader("2. Review Eco-Scale suggestions")
    meta = ex["meta"]
    est = px.estimate_yield(edited)
    y_guess = meta["yield_percent"] if meta["yield_percent"] is not None else est
    sug = px.suggest_eco_inputs(meta, y_guess)
    if meta["yield_percent"] is not None and est is not None and abs(meta["yield_percent"] - est) > 5:
        st.warning(f"Reported yield ({meta['yield_percent']}%) differs from the yield computed from the "
                   f"table ({est}%). Check the product mass and the limiting reagent.")
    e1, e2 = st.columns(2)
    with e1:
        yield_pct = st.number_input("Yield (%)", 0.0, 100.0, sug["yield_pct"], 0.5, key=f"ey_{ex['id']}")
        temp_time = st.selectbox("Temperature & time", list(es.TEMPERATURE_TIME_PENALTIES),
                                 index=_idx(es.TEMPERATURE_TIME_PENALTIES, sug["temp_time"]), key=f"et_{ex['id']}")
    with e2:
        setups = st.multiselect("Technical setup", list(es.TECHNICAL_SETUP_PENALTIES),
                                default=sug["setups"], key=f"es_{ex['id']}")
        workup = st.selectbox("Workup / purification", list(es.WORKUP_PENALTIES),
                              index=_idx(es.WORKUP_PENALTIES, sug["workup"]), key=f"ew_{ex['id']}")
    st.caption("Price and hazard (GHS) categories are not stated in papers; set them on the Eco-Scale page.")

    with st.expander("Raw model output (debugging)"):
        for p in ex["pages"]:
            st.markdown(f"**Page {p['page']}**")
            st.code(p["raw"], language="json")

    st.markdown("---")
    ok = st.checkbox("I have checked the values above against the source document.")
    if st.button("✅ Apply to calculators", type="primary", disabled=not ok):
        tables = px.to_app_tables(edited)
        tables["eco"] = {"yield_pct": yield_pct, "temp_time": temp_time, "setups": setups, "workup": workup}
        st.session_state.prefill = tables
        st.session_state.prefill_version += 1
        st.success("Applied. Open **Mass-Based Metrics Calculator** and **Reaction Eco-Scale Scoring**: "
                   "the fields are pre-filled.")
        if "product" not in tables:
            st.warning("No product row was selected, so product fields keep their defaults.")
