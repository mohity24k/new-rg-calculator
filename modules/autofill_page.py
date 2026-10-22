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
from modules.extraction import config as xcfg
from modules.extraction import enrich as xen
from modules.extraction import pipeline as xpl
from modules.extraction.engines import HttpEngine

CLASSIC = "One reaction (classic)"
MULTI = "Several reactions per document (new)"


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

    mode = st.radio(
        "Extraction mode", [CLASSIC, MULTI], horizontal=True,
        help="Classic: every page is read as an image and merged into one reaction. New: pages without "
             "a procedure are skipped, text pages are sent as text, requests run in parallel, and each "
             "reaction (substrate-scope entry, general procedure) is kept separately. Settings: "
             "config/extraction.yaml.")
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
        if mode == MULTI:
            _extract_multi(pdf, up.name, first, last, base_url.strip(), model.strip(), api_key)
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
    _render_review(ex)


def _extract_multi(pdf: bytes, file: str, first: int, last: int, base_url: str, model: str, api_key: str):
    cfg = xcfg.load_config()
    s = cfg["server"]
    eng = HttpEngine(base_url, model, api_key, concurrency=s["concurrency"], timeout=s["timeout_s"],
                     retries=s["retries"])
    try:
        with st.spinner("Reading the pages (in parallel)…"):
            out = xpl.extract_pdf(pdf, eng, cfg, range(first - 1, last), file=file)
    except Exception as e:
        st.error(_server_error(e))
        return
    with st.spinner("Resolving structures and computing amounts…"):
        reactions = [xen.enrich_reaction(r, cfg) for r in out["reactions"]]
    reactions.sort(key=lambda r: r.get("record_type") == "general_procedure")   # specific reactions first
    raw_by_page: dict[int, list[str]] = {}
    failed_pages = set()
    for u, r in zip(out["units"], out["results"]):
        for p in u["pages"]:
            raw_by_page.setdefault(p, []).append(f"[{u['kind']}, {u['mode']}] " + (r["text"] or r["error"]))
            if not r["ok"]:
                failed_pages.add(p)
    pages = []
    for info in out["pages"]:
        n = sum(info["page"] in (r.get("source") or {}).get("pages", []) for r in reactions)
        sent = info["page"] in raw_by_page
        pages.append({"page": info["page"], "data": None if info["page"] in failed_pages else {},
                      "raw": "\n\n".join(raw_by_page.get(info["page"], [])),
                      "status": f"{info['label']}" + (" (request failed)" if info["page"] in failed_pages
                                                      else "" if sent else " (skipped)"),
                      "n_components": n})
    prev = st.session_state.get("extraction") or {}
    ex = {"id": prev.get("id", 0) + 1, "pdf": pdf, "pages": pages, "mode": "multi",
          "reactions": reactions, "tables": out["tables"], "seconds": out["seconds"], "sel": None,
          "df": px._typed(pd.DataFrame(columns=px.COLUMNS)), "meta": px.merge_meta([])}
    st.session_state.extraction = ex
    _render_review(ex)


def _select_reaction(ex: dict) -> bool:
    """Multi-reaction mode: pick the reaction to review. False if there is nothing to review."""
    rx = ex["reactions"]
    n_real = sum(r.get("record_type") != "general_procedure" for r in rx)
    st.subheader("Reactions found")
    st.caption(f"{n_real} reaction(s), {len(rx) - n_real} general procedure(s) and {len(ex['tables'])} "
               f"optimization table(s) in {ex['seconds']:.0f} s. Pick one to review and send to the calculators.")
    if not rx:
        st.warning("No reactions were found on the selected pages. Check the page range, or try the classic mode.")
        return False
    sel = st.selectbox("Reaction to review", range(len(rx)), format_func=lambda i: xen.summary(rx[i]),
                       key=f"rxn_sel_{ex['id']}")
    if sel != ex.get("sel"):
        ex["sel"] = sel
        ex["df"] = xen.to_review_table(rx[sel])
        ex["meta"] = xen.review_meta(rx[sel])
    for f in rx[sel].get("flags", []):
        (st.info if f["code"] == "inherited_from_general_procedure" else st.warning)(f"{f['code']}: {f['detail']}")
    if ex["tables"]:
        with st.expander(f"Optimization tables ({len(ex['tables'])})"):
            for t in ex["tables"]:
                st.markdown(f"**{t.get('title') or 'Table'}** (page {', '.join(map(str, t['source']['pages']))})")
                if t.get("standard_conditions"):
                    st.caption(t["standard_conditions"])
                st.dataframe(pd.DataFrame(t["entries"]), hide_index=True, width="stretch")
    return True


def _render_review(ex: dict):

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
        pd.DataFrame([{"Page": p["page"], "Status": p["status"],
                       ("Reactions" if ex.get("mode") == "multi" else "Compounds"): p["n_components"]}
                      for p in ex["pages"]]),
        hide_index=True, width="stretch")

    if ex.get("mode") == "multi" and not _select_reaction(ex):
        return
    kid = f"{ex['id']}_{ex.get('sel') or 0}"     # new widgets when another reaction is selected

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
            key=f"review_editor_{kid}",
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
        yield_pct = st.number_input("Yield (%)", 0.0, 100.0, sug["yield_pct"], 0.5, key=f"ey_{kid}")
        temp_time = st.selectbox("Temperature & time", list(es.TEMPERATURE_TIME_PENALTIES),
                                 index=_idx(es.TEMPERATURE_TIME_PENALTIES, sug["temp_time"]), key=f"et_{kid}")
    with e2:
        setups = st.multiselect("Technical setup", list(es.TECHNICAL_SETUP_PENALTIES),
                                default=sug["setups"], key=f"es_{kid}")
        workup_steps = st.multiselect(
            "Workup / purification steps", list(es.WORKUP_PENALTIES),
            default=list(dict.fromkeys(sug["workup_steps"])), key=f"ew_{kid}",
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
