"""
autofill_page.py
----------------
Streamlit page: upload PDF -> extract with a vision-language model (Qwen2.5-VL by default,
any OpenAI-compatible endpoint works) -> human review -> apply to calculators.
"""

import pandas as pd
import requests
import streamlit as st

from modules import eco_scale as es
from modules import pdf_extraction as px


def _secret(key: str, default: str = "") -> str:
    try:
        value = st.secrets.get(key, default)
    except Exception:  # no secrets.toml
        return default
    return str(value) if value is not None else default


def _idx(options, value) -> int:
    options = list(options)
    return options.index(value) if value in options else 0


@st.cache_data(show_spinner=False, max_entries=64)
def _preview(pdf: bytes, page_idx: int) -> bytes:
    return px.render_page(pdf, page_idx, dpi=110)


def _server_error(e: Exception) -> str:
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return f"Could not reach the extraction server. Check the endpoint URL and that the server is running. ({e})"
    if isinstance(e, requests.HTTPError):
        return f"The extraction server returned an error. Check the model name and API key. ({e})"
    return f"Extraction failed: {type(e).__name__}: {e}"


def render_autofill_page():
    st.title("📄 Upload PDF & Auto-fill")
    st.caption(
        "Upload a paper or SI. A vision-language model reads the experimental section, the app resolves "
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
    try:
        n = px.page_count(pdf)
    except ValueError as e:
        st.error(f"Cannot open this file: {e}")
        return
    if n == 0:
        st.error("This PDF has no pages.")
        return

    c1, c2, c3 = st.columns([1, 1, 2])
    first = int(c1.number_input("First page", 1, n, 1, key=f"first_{up.name}_{n}"))
    last = int(c2.number_input("Last page", 1, n, n, key=f"last_{up.name}_{n}"))
    c3.write("")
    if c3.button("🔍 Extract", type="primary"):
        if first > last:
            st.error("First page must be ≤ last page.")
            return
        if not base_url.strip() or not model.strip():
            st.error("Enter the endpoint URL and model name under ⚙️ Extraction server settings.")
            return
        bar = st.progress(0.0, "Starting…")
        try:
            pages = px.extract_document(
                pdf, range(first - 1, last), base_url.strip(), model.strip(), api_key,
                on_progress=lambda i, t: bar.progress(i / t, f"Page {i}/{t}"))
        except Exception as e:
            bar.empty()
            st.error(_server_error(e))
            return
        bar.empty()
        with st.spinner("Resolving structures and computing amounts…"):
            df = px.build_review_table(pages)
        prev = st.session_state.get("extraction") or {}
        st.session_state.extraction = {
            "id": prev.get("id", 0) + 1, "pdf": pdf, "pages": pages,
            "df": df, "meta": px.merge_meta(pages)}

    ex = st.session_state.get("extraction")
    if not ex or ex["pdf"] != pdf:
        return

    st.markdown("---")
    if not ex["pages"]:
        st.warning("No pages were extracted.")
        return
    bad = [p for p in ex["pages"] if p["data"] is None]
    st.subheader("Extraction status per page")
    if bad:
        st.error("Pages that could not be read: " + ", ".join(str(p["page"]) for p in bad)
                 + ". Their content is NOT in the table below. See 'Raw model output'.")
    st.dataframe(
        pd.DataFrame([{"Page": p["page"], "Status": p["status"], "Compounds": p["n_components"]}
                      for p in ex["pages"]]),
        hide_index=True, width="stretch")

    st.subheader("1. Review extracted materials")
    n_flag = int((ex["df"]["Flag"] != "").sum()) if not ex["df"].empty else 0
    st.caption(f"{len(ex['df'])} rows extracted, **{n_flag} need attention** (see the Flag column). "
               "Change a row's Role to *ignore* to drop it. Edit any value; add rows with ＋.")

    left, right = st.columns([3, 2])
    with right:
        shown = [p["page"] for p in ex["pages"]]
        pg = st.selectbox("Source page preview", shown)
        st.image(_preview(ex["pdf"], pg - 1), width="stretch")
    with left:
        edited = st.data_editor(
            ex["df"], num_rows="dynamic", hide_index=True, width="stretch",
            key=f"review_editor_{ex['id']}",
            column_config={
                "Role": st.column_config.SelectboxColumn(options=px.ROLES, required=True),
                "Mass (g)": st.column_config.NumberColumn(format="%.5f", min_value=0.0),
                "Moles (mol)": st.column_config.NumberColumn(format="%.6f", min_value=0.0),
                "MW (g/mol)": st.column_config.NumberColumn(format="%.2f", min_value=0.0),
                "Carbons": st.column_config.NumberColumn(format="%d", min_value=0, step=1),
                "Flag": st.column_config.TextColumn(disabled=True),
                "SMILES": st.column_config.TextColumn(
                    help="Enter a SMILES for names that could not be resolved: MW and carbons are then computed."),
                "Page": st.column_config.TextColumn(disabled=True),
            })

    st.subheader("2. Review Eco-Scale suggestions")
    meta = ex["meta"]
    est = px.estimate_yield(edited, ex["df"])
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
        workup_steps = st.multiselect(
            "Workup / purification steps", list(es.WORKUP_PENALTIES),
            default=list(dict.fromkeys(sug["workup_steps"])), key=f"ew_{ex['id']}",
            help="Penalties add up. Repeated steps (e.g. two aqueous washes) can be added on the "
                 "Eco-Scale page.")
    st.caption("Price and hazard symbols are not stated in papers; set them on the Eco-Scale page "
               "(the compound list is pre-filled there).")

    with st.expander("Raw model output (debugging)"):
        for p in ex["pages"]:
            st.markdown(f"**Page {p['page']}**")
            st.code(p["raw"] or f"(no output: {p['status']})", language="json")

    st.markdown("---")
    ok = st.checkbox("I have checked the values above against the source document.")
    if st.button("✅ Apply to calculators", type="primary", disabled=not ok):
        try:
            tables = px.to_app_tables(edited, ex["df"])
        except Exception as e:
            st.error(f"Could not convert the table: {type(e).__name__}: {e}")
            return
        tables["eco"] = {"yield_pct": yield_pct, "temp_time": temp_time,
                         "setups": setups or ["Common setup"], "workup_steps": workup_steps}
        st.session_state.prefill = tables
        st.session_state.prefill_version = st.session_state.get("prefill_version", 0) + 1
        st.success("Applied. Open **Mass-Based Metrics Calculator** and **Reaction Eco-Scale Scoring**: "
                   "the fields are pre-filled.")
        if "product" not in tables:
            st.warning("No product row was selected, so product fields keep their defaults.")
        if tables["reactants_df"].empty:
            st.warning("No reactant rows were selected, so the reactant table is empty.")
