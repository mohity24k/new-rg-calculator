"""Page 1: enter a reaction by hand, load an example, or extract one from a PDF. Then review it."""

import json

import pandas as pd
import streamlit as st

from gms import examples, forms, settings
from gms.chem import classify, stoich
from gms.extraction import classic, service
from gms.metrics import ecoscale as es
from gms.model import Reaction
from ui import components as ui
from ui import state

state.init()
ss = st.session_state

st.title("Reaction")
st.write("Enter the reaction as it was run: every material with its amount and role, the conditions, and the "
         "workup. Then select **Analyse**. The score card, the diagnosis and the greener alternatives use this record.")

# ---------------------------------------------------------------------------------------------
# Start from an example, a file, or a PDF
# ---------------------------------------------------------------------------------------------
with st.expander("Start from an example, a saved reaction or a PDF", expanded=ss.get("reaction") is None):
    tab_ex, tab_json, tab_pdf = st.tabs(["Example", "Saved reaction (JSON)", "Extract from a PDF"])

    with tab_ex:
        titles = examples.titles()
        ex_id = st.selectbox("Example reaction", list(titles), format_func=titles.get)
        st.caption(examples.description(ex_id))
        c1, c2 = st.columns(2)
        if c1.button("Load example", type="primary"):
            state.load_into_editor(examples.load(ex_id))
            st.rerun()
        if c2.button("Clear the form"):
            state.load_into_editor(None)
            st.rerun()

    with tab_json:
        st.caption("Load a reaction from a JSON report that this app exported (Compare and export page).")
        up = st.file_uploader("JSON report", type="json", key="json_upload")
        if up is not None and st.button("Load reaction from the report"):
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
                state.load_into_editor(Reaction.from_dict(data["reaction"] if "reaction" in data else data))
                st.rerun()
            except (ValueError, KeyError, TypeError) as e:
                st.error(f"This file does not contain a reaction record ({type(e).__name__}).")

    with tab_pdf:
        cfg = state.app_settings()
        status = service.status(cfg)
        if not status.online:
            st.info(status.message)
            if status.configured and st.button("Check again"):
                service.reset_status_cache()
                st.rerun()
        else:
            st.caption(f"Upload a paper or its supporting information (PDF, at most {cfg.max_upload_mb} MB). A "
                       "vision-language model reads the experimental procedures. The file is sent to this "
                       "installation's extraction server and is not stored. Always check the result against the paper.")
            pdf_file = st.file_uploader("PDF", type="pdf", key="pdf_upload")
            if pdf_file is not None:
                pdf = pdf_file.getvalue()
                try:
                    n_pages = service.check_upload(pdf, cfg)
                except service.ExtractionError as e:
                    st.error(str(e))
                    n_pages = 0
                if n_pages:
                    p1, p2 = st.columns(2)
                    first = int(p1.number_input("First page", 1, n_pages, 1))
                    last = int(p2.number_input("Last page", 1, n_pages, min(n_pages, first + cfg.max_pdf_pages - 1)))
                    if st.button("Extract reactions", type="primary"):
                        try:
                            with st.spinner("Reading the pages..."):
                                out = service.extract(pdf, pdf_file.name, first, last, cfg)
                            ss["extraction"] = {**out, "file": pdf_file.name, "pdf": pdf}
                        except service.ExtractionError as e:
                            st.warning(str(e))
            exr = ss.get("extraction")
            if exr:
                rx = exr["reactions"]
                st.markdown(f"**{len(rx)} reaction record(s) found** in {exr['seconds']:.0f} s"
                            + (f"; {exr['failed_requests']} of {exr['requests']} requests failed, so some pages "
                               "are missing" if exr["failed_requests"] else "") + ".")
                if not rx:
                    st.warning("No reaction procedure was found on these pages. Check the page range.")
                else:
                    from gms.extraction import enrich
                    i = st.selectbox("Reaction to review", range(len(rx)), format_func=lambda k: enrich.summary(rx[k]))  # plain text
                    rec = rx[i]
                    st.caption(f"Completeness: {100 * rec.get('completeness', 0):.0f}% of the materials have a mass.")
                    for f in rec.get("flags", []):
                        st.warning(f"Check: {f['code'].replace('_', ' ')}. {ui.md(f['detail'])}")
                    pages = (rec.get("source") or {}).get("pages") or []
                    if pages and st.checkbox("Show the source page"):
                        st.image(classic.render_page(exr["pdf"], pages[0] - 1, dpi=110), width="stretch")
                    if st.button("Put this reaction into the form for review"):
                        state.load_into_editor(service.to_reaction(rec, exr["file"]))
                        st.rerun()

# ---------------------------------------------------------------------------------------------
# The entry form
# ---------------------------------------------------------------------------------------------
ed = ss["editor"]
meta = ed["meta"]
v = ss["editor_version"]
src = meta.get("source") or {}
if src.get("entry") == "pdf":
    st.warning("This reaction was extracted from a PDF by a model. Check every row against the source "
               f"(file {ui.md(src.get('file') or 'unknown')}, page(s) {', '.join(map(str, src.get('pages') or [])) or 'unknown'}) "
               "before you analyse it.")
    for f in src.get("flags") or []:
        st.caption(f"Extraction check: {ui.md(f)}")

st.subheader("Materials")
advanced = st.toggle("Show advanced columns (structure, molecular weight, coefficient, benign, estimate)",
                     key=f"adv_{v}")
st.caption("One row per material. **Role** decides how the material counts: reactants and reagents are in the "
           "balanced equation; everything else is auxiliary. Give the amount as mass, volume, moles or equivalents "
           "(1 equivalent = the moles of the limiting reactant). "
           "Tick **Limiting** for the limiting reactant. Add rows at the bottom of the table.")
cols = [c for c in forms.COLUMNS if advanced or c not in forms.ADVANCED]
edited = st.data_editor(
    ed["rows"], num_rows="dynamic", hide_index=True, width="stretch", key=f"materials_{v}", column_order=cols,
    column_config={
        "Name": st.column_config.TextColumn(required=True, max_chars=forms.MAX_NAME,
                                            help="Chemical name or common abbreviation (DMF, EtOAc, HATU...)."),
        "Role": st.column_config.SelectboxColumn(options=forms.ROLE_CHOICES, required=True),
        "Amount": st.column_config.NumberColumn(min_value=0.0, format="%.6g"),
        "Unit": st.column_config.SelectboxColumn(options=forms.UNITS, required=True),
        "Limiting": st.column_config.CheckboxColumn(),
        "SMILES": st.column_config.TextColumn(help="Optional. Needed only if the name is not recognised."),
        "MW": st.column_config.NumberColumn("MW (g/mol)", min_value=0.0, format="%.2f",
                                            help="Optional. Computed from the structure if empty."),
        "Coefficient": st.column_config.NumberColumn(min_value=0.0, format="%.3g",
                                                     help="Stoichiometric coefficient in the balanced equation."),
        "Benign": st.column_config.CheckboxColumn(help="No environmental risk (Hudlicky). Used only for the effective mass yield."),
        "Estimated": st.column_config.CheckboxColumn(help="Tick if this amount is your estimate, not a value from the procedure."),
    })

st.subheader("Conditions and workup")
c1, c2, c3 = st.columns(3)
name = c1.text_input("Reaction name", meta.get("name") or "", max_chars=forms.MAX_NAME, key=f"name_{v}")
class_options = ["auto"] + list(classify.CLASSES) + ["other"]
rclass = c2.selectbox("Reaction class", class_options,
                      index=class_options.index(meta["reaction_class"]) if meta.get("reaction_class") in class_options else 0,
                      format_func=lambda k: {"auto": "Detect automatically", "other": "Other / not listed"}.get(k) or classify.CLASSES[k],
                      key=f"class_{v}", help="Suggestions are filtered by reaction class.")
reported = c3.number_input("Reported yield (%), optional", 0.0, 100.0, meta.get("reported_yield_percent"),
                           key=f"ry_{v}", help="Only for cross-checking. The yield is computed from the amounts.")
c1, c2, c3, c4 = st.columns(4)
temp = c1.number_input("Temperature (°C)", -120.0, 400.0, meta.get("temperature_c"), key=f"t_{v}")
time_h = c2.number_input("Time (h)", 0.0, 1000.0, meta.get("time_h"), key=f"h_{v}")
atmosphere = c3.selectbox("Atmosphere", forms.ATMOSPHERES,
                          index=forms.ATMOSPHERES.index(meta["atmosphere"]) if meta.get("atmosphere") in forms.ATMOSPHERES else 0,
                          key=f"atm_{v}")
activation = c4.selectbox("Activation", forms.ACTIVATIONS,
                          index=forms.ACTIVATIONS.index(meta["activation"]) if meta.get("activation") in forms.ACTIVATIONS else 0,
                          key=f"act_{v}")
c1, c2 = st.columns(2)
reflux = c1.checkbox("Heated at reflux", bool(meta.get("reflux")), key=f"reflux_{v}")
extra = c2.multiselect("Special equipment", forms.EXTRA_SETUPS, default=meta.get("extra_setups") or [], key=f"setup_{v}")

st.caption("Workup and purification: one row per step that was done. The EcoScale penalties add up.")
steps_df = st.data_editor(
    pd.DataFrame({"Step": pd.Series(meta.get("workup_steps") or [], dtype="object")}),
    num_rows="dynamic", hide_index=True, width="stretch", key=f"steps_{v}",
    column_config={"Step": st.column_config.SelectboxColumn(options=list(es.WORKUP_PENALTIES), required=True)})
price_options = [forms.PRICE_NOT_ASSESSED] + list(es.PRICE_PENALTIES)
price = st.selectbox("Price of the reaction components (EcoScale)", price_options,
                     index=price_options.index(meta["price_category"]) if meta.get("price_category") in price_options else 0,
                     key=f"price_{v}", help="Cost of all components needed for 10 mmol of product. "
                                            "'Not assessed' counts 0 penalty points and is listed as an assumption.")

if st.button("Analyse", type="primary"):
    new_meta = {"name": name, "reaction_class": rclass, "reported_yield_percent": reported, "temperature_c": temp,
                "time_h": time_h, "reflux": reflux, "atmosphere": atmosphere, "activation": activation,
                "extra_setups": extra, "price_category": price, "source": meta.get("source") or {"entry": "manual"},
                "workup_steps": [s for s in steps_df["Step"].tolist() if isinstance(s, str)]}
    reaction, problems = forms.reaction_from_rows(edited.to_dict("records"), new_meta)
    if problems:
        for p in problems:
            st.error(p)
    else:
        ss["editor"] = {"rows": edited.reset_index(drop=True), "meta": new_meta}
        state.set_reaction(reaction)

# ---------------------------------------------------------------------------------------------
# Review of the analysed record
# ---------------------------------------------------------------------------------------------
a = state.analysis()
if a:
    r, score = a["reaction"], a["score"]
    st.divider()
    st.subheader("Review: what the app understood")
    cls, how, why = a["detected_class"]
    label = classify.CLASSES.get(r.reaction_class, "Not one of the supported classes")
    st.markdown(f"**Reaction class:** {label}. " + (ui.md(why) if cls == r.reaction_class else "Set by you."))
    if r.reaction_class is None:
        st.info("Suggestions for reaction solvent, reagents and catalysts exist only for amide coupling, "
                "Suzuki-Miyaura coupling and reductive amination. Workup and purification suggestions, the score "
                "card and the diagnosis work for every reaction.")
    table = pd.DataFrame([{
        "Material": c.name, "Role": c.role.replace("_", " "), "Mass (g)": c.mass_g,
        "Amount (mmol)": c.moles * 1000 if c.moles else None, "Equiv.": c.equiv, "MW": c.mw,
        "Structure": "recognised" if c.smiles else "not recognised",
        "How the mass was obtained": ("ESTIMATE. " if c.estimated else "") + (c.note or "as entered"),
    } for c in r.components])
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Mass (g)": st.column_config.NumberColumn(format="%.4g"),
                                "Amount (mmol)": st.column_config.NumberColumn(format="%.4g"),
                                "Equiv.": st.column_config.NumberColumn(format="%.3g"),
                                "MW": st.column_config.NumberColumn(format="%.2f")})
    for msg in a["issues"]:
        st.warning(ui.md(msg))
    for msg in score.warnings:
        if msg not in a["issues"]:
            st.warning(ui.md(msg))
    inferred = stoich.infer_coefficients([c.smiles for c in r.by_role("reactant")], r.product.smiles if r.product else None)
    current = [c.coefficient for c in r.by_role("reactant")]
    if inferred and not inferred["covered"]:
        st.warning("The product has more carbon atoms than the reactants supply. A reactant may be missing, or it "
                   "has another role in the table (for example 'reagent').")
    elif inferred and inferred["coefficients"] != current and all(x == 1 for x in current):
        pairs = ", ".join(f"{n} x {ui.md(c.name)}" for n, c in zip(inferred["coefficients"], r.by_role("reactant")))
        st.info(f"Atom balance suggests these coefficients: {pairs}. The app does not change them for you. If "
                "this is right, set the coefficients in the advanced columns and analyse again.")
    st.success("Analysed. Continue with the score card.")
    st.page_link("ui/pages/scorecard.py", label="Open the score card", icon=":material/fact_check:")
ui.disclaimer()
