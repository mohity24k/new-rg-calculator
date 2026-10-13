"""
app.py
------
Green Chemistry Metrics Calculator & Visualizer
Built on the metrics framework of:
Tamargo, R.J.I. et al. (2024). "Metrics of Green Chemistry and Sustainability."
In: Sen, M. (Ed.), Sustainable Green Catalytic Processes, Ch. 10, pp. 225-258.
Scrivener Publishing.

Run with:  streamlit run app.py
"""

import pandas as pd
import streamlit as st

from modules import mass_metrics as mm
from modules import eco_scale as es
from modules import hazard_metrics as hz
from modules import visualization as viz
from modules import data_export as dx
from modules.inputs import AUX_DEFAULTS, REACTANT_DEFAULTS, clean_rows, with_defaults
from modules.autofill_page import render_autofill_page

# ---------------------------------------------------------------------------
# Page config & session state
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Green Chemistry Metrics Studio", layout="wide", page_icon="🌱")

if "routes" not in st.session_state:
    # Stores computed metrics for up to two comparison routes: "Route A", "Route B"
    st.session_state.routes = {"Route A": {}, "Route B": {}}

if "prefill" not in st.session_state:
    st.session_state.prefill = {}
    st.session_state.prefill_version = 0

CARBON_MW = 12.011





# ---------------------------------------------------------------------------
# Sample data: benzyl alcohol + p-toluenesulfonyl chloride esterification
# (Tamargo et al. 2024, Section 10.1.1.2, adapted from Curzons et al.)
# ---------------------------------------------------------------------------

def default_reactants():
    return pd.DataFrame([
        {"Name": "Benzyl alcohol", "MW (g/mol)": 108.14, "Moles charged (mol)": 0.10,
         "Mass charged (g)": 10.81, "Carbons/molecule": 7,
         "Stoich. ratio (per limiting reagent)": 1.0, "Limiting reagent?": True, "Benign?": False},
        {"Name": "p-Toluenesulfonyl chloride", "MW (g/mol)": 190.64, "Moles charged (mol)": 0.115,
         "Mass charged (g)": 21.90, "Carbons/molecule": 7,
         "Stoich. ratio (per limiting reagent)": 1.0, "Limiting reagent?": False, "Benign?": False},
    ])


def default_auxiliaries():
    return pd.DataFrame([
        {"Name": "Triethylamine (base)", "Mass (g)": 15.0, "Benign?": False},
        {"Name": "Toluene (solvent)", "Mass (g)": 500.0, "Benign?": False},
    ])


DEFAULT_PRODUCT = {
    "name": "Benzyl 4-toluenesulfonate ester",
    "mw": 262.32,
    "actual_mass": 23.6,
    "moles": 0.09,
    "carbons": 14,
}

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("🌱 Green Metrics Studio")
page = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard Overview", 
        "Upload PDF & Auto-fill",
        "Mass-Based Metrics Calculator",
        "Reaction Eco-Scale Scoring",
        "Global Hazard & Toxicity Impact",
        "Process Comparison & Export",
    ],
)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Based on: Tamargo, R.J.I. *et al.* (2024). *Metrics of Green Chemistry and "
    "Sustainability.* In *Sustainable Green Catalytic Processes* "  
    "  https://doi.org/10.1002/9781394212767.ch10"
)

# ===========================================================================
# PAGE 1: DASHBOARD OVERVIEW
# ===========================================================================

if page == "Dashboard Overview":
    st.title("Green Chemistry Metrics Studio")
    st.markdown(
        "A modular calculator for **mass-based**, **impact-based (Eco-Scale)**, "
        "and **global hazard/toxicity** green chemistry metrics, as classified in "
        "Tamargo *et al.* (2024)."
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("Why Green Metrics?")
        st.write(
            "Green chemistry metrics quantify the 12 Principles of Green Chemistry "
            "so that competing synthetic routes can be objectively compared. "
            "Mass-based metrics (E-factor, Atom Economy, PMI, RME, Carbon "
            "Efficiency) are simple and require minimal assumptions, but treat all "
            "waste as equally harmful. Impact-based metrics (Eco-Scale, "
            "toxicological and global hazard indicators) combine mass with actual "
            "environmental/health consequences, offering a more complete "
            "sustainability picture at the cost of added complexity."
        )
        st.subheader("How to use this app")
        st.markdown(
            """
            1. **Mass-Based Metrics Calculator** — enter reactant/product data
               (or use the pre-loaded benzyl tosylate esterification example) to
               compute AE, RME, PMI, MI, E-factor, CE, MP, EMY, ERF and yield.
            2. **Reaction Eco-Scale Scoring** — score a reaction's overall
               "greenness" (0–100) based on yield, cost, safety, setup, conditions,
               and workup.
            3. **Global Hazard & Toxicity Impact** — assess acidification, ozone
               depletion, smog formation, global warming, abiotic depletion,
               air toxicity, water ecotoxicity and human toxicity ratios.
            0. **Upload PDF & Auto-fill** (optional) — extract the quantities from a
               paper's experimental section and pre-fill the pages above.
            4. **Process Comparison & Export** — save two routes (A/B), compare
               them on a radar chart, and export a combined CSV/JSON report.
            """
        )
    with col2:
        st.subheader("Metric Cheat-Sheet")
        cheat = pd.DataFrame([
            ["Atom Economy (AE)", "% reactant mass retained in product (theoretical)"],
            ["Reaction Mass Efficiency (RME)", "% reactant mass retained (actual yield)"],
            ["Process Mass Intensity (PMI)", "kg input / kg product (lower = better)"],
            ["E-Factor", "kg waste / kg product (0 = ideal)"],
            ["Carbon Efficiency (CE)", "% reactant carbon retained in product"],
            ["Mass Productivity (MP)", "Reciprocal of MI, as %"],
            ["Effective Mass Yield (EMY)", "% product per mass of non-benign inputs"],
            ["Excess Reactant Factor (ERF)", "Reactant mass charged / stoichiometric (1 = ideal)"],
            ["Percentage Yield", "Actual / theoretical product mass"],
            ["Eco-Scale", "100 - penalties; 100 = ideal reaction"],
        ], columns=["Metric", "Meaning"])
        st.dataframe(cheat, hide_index=True, width="stretch")

    st.markdown("---")
    st.subheader("Saved Comparison Routes")
    rcol1, rcol2 = st.columns(2)
    for rc, rname in zip([rcol1, rcol2], ["Route A", "Route B"]):
        with rc:
            st.markdown(f"**{rname}**")
            data = st.session_state.routes[rname]
            if data:
                st.json(dx._finite(data), expanded=False)
            else:
                st.info("No data saved yet. Visit the calculator pages and click "
                        "'Save to Route' to populate this comparison slot.")

# ===========================================================================
# PAGE: UPLOAD PDF & AUTO-FILL
# ===========================================================================

elif page == "Upload PDF & Auto-fill":
    render_autofill_page()

# ===========================================================================
# PAGE 2: MASS-BASED METRICS CALCULATOR
# ===========================================================================

elif page == "Mass-Based Metrics Calculator":
    st.title("⚖️ Mass-Based Metrics Calculator")
    st.caption(
        "Univariate mass-based metrics simplify greenness assessment to a single "
        "mass ratio. They are fast, require minimal assumptions, but cannot "
        "distinguish between more or less hazardous waste streams (Tamargo et "
        "al., 2024, Sec. 10.1.1)."
    )

    save_target = st.selectbox("Save results to comparison slot:", ["Route A", "Route B"])

    pf = st.session_state.prefill
    v = st.session_state.prefill_version
    prod = {**DEFAULT_PRODUCT, **pf.get("product", {})}

    st.subheader("1. Reactants")
    st.caption(
        "Enter every stoichiometric reactant (everything in the balanced equation). Tick exactly one "
        "**limiting reagent**. *Stoich. ratio* is the coefficient in the balanced equation relative to "
        "the limiting reagent (2 A + B → P with B limiting: A = 2, B = 1); it is used for AE, yield and "
        "ERF. Mass and moles must agree (moles × MW = mass): fill one and leave the other 0 to have it "
        "computed."
    )
    reactants_df = st.data_editor(
        with_defaults(pf.get("reactants_df", default_reactants()), REACTANT_DEFAULTS),
        num_rows="dynamic", width="stretch", key=f"reactants_editor_{v}",
        column_config={
            "MW (g/mol)": st.column_config.NumberColumn(min_value=0.0, format="%.2f"),
            "Moles charged (mol)": st.column_config.NumberColumn("Moles (mol)", min_value=0.0, format="%.5f"),
            "Mass charged (g)": st.column_config.NumberColumn("Mass (g)", min_value=0.0, format="%.4f"),
            "Carbons/molecule": st.column_config.NumberColumn("Carbons", min_value=0, step=1, format="%d",
                                                              help="Carbon atoms per molecule (for CE)."),
            "Stoich. ratio (per limiting reagent)": st.column_config.NumberColumn(
                "Stoich. ratio", min_value=0.0, format="%.3g",
                help="Coefficient in the balanced equation relative to the limiting reagent."),
            "Limiting reagent?": st.column_config.CheckboxColumn("Limiting?"),
            "Benign?": st.column_config.CheckboxColumn(
                help="Hudlicky's 'benign' (no environmental risk: water, NaCl, dilute ethanol...). "
                     "Only used for Effective Mass Yield."),
        },
    )

    st.subheader("2. Auxiliary Materials (solvents, bases, catalysts, workup reagents)")
    st.caption(
        "These materials are NOT incorporated into the product but still count "
        "toward PMI, E-factor, and Mass Intensity. Enter water separately below."
    )
    aux_df = st.data_editor(
        with_defaults(pf.get("aux_df", default_auxiliaries()), AUX_DEFAULTS),
        num_rows="dynamic", width="stretch", key=f"aux_editor_{v}",
        column_config={
            "Mass (g)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Benign?": st.column_config.CheckboxColumn(help="Only used for Effective Mass Yield."),
        },
    )

    st.subheader("3. Product")
    pcol1, pcol2, pcol3, pcol4, pcol5 = st.columns(5)
    with pcol1:
        product_name = st.text_input("Product name", prod["name"], key=f"pn_{v}")
    with pcol2:
        product_mw = st.number_input("Product MW (g/mol)", min_value=0.0,
                                      value=float(prod["mw"]), step=0.01,
                                      key=f"pmw_{v}",
                                      help="Used for Atom Economy, theoretical yield and product moles.")
    with pcol3:
        product_actual_mass = st.number_input("Actual isolated mass (g)", min_value=0.0,
                                                value=float(prod["actual_mass"]), step=0.01,
                                                format="%.4f", key=f"pm_{v}",
                                                help="Used for RME, PMI, E-factor, MP, CE, EMY and yield.")
    with pcol4:
        product_carbons = st.number_input("Carbons per product molecule", min_value=0,
                                           value=int(prod["carbons"]), step=1,
                                           key=f"pc_{v}",
                                           help="Used for Carbon Efficiency (CE).")
    with pcol5:
        product_coef = st.number_input("Product coefficient [P/A]", min_value=0.0, value=1.0, step=0.5,
                                        key=f"pcoef_{v}",
                                        help="Moles of product per mole of limiting reagent in the balanced "
                                             "equation (Eq. 10.2). 1 for A + B → P.")
    product_moles = product_actual_mass / product_mw if product_mw > 0 else 0.0
    st.caption(f"Product moles obtained = mass / MW = **{product_moles:.5g} mol** (used for Carbon Efficiency).")

    water_mass = st.number_input(
        "Water mass in reaction vessel (g)", min_value=0.0,
        value=float(pf.get("water_mass", 0.0)), step=1.0, key=f"w_{v}",
        help="Included in PMI (per ACS GCI convention) but excluded from Mass Intensity (MI)."
    )

    if st.button("🧮 Calculate Mass-Based Metrics", type="primary"):
        reactants = [
            mm.Reactant(name=r["Name"], mw=r["MW (g/mol)"], moles=r["Moles charged (mol)"],
                        mass=r["Mass charged (g)"], carbons=int(r["Carbons/molecule"]),
                        stoich_ratio=r["Stoich. ratio (per limiting reagent)"],
                        is_limiting=r["Limiting reagent?"], benign=r["Benign?"])
            for r in clean_rows(reactants_df, REACTANT_DEFAULTS)
        ]
        for r in reactants:   # fill the missing one of mass / moles
            if r.mw > 0 and r.mass <= 0 < r.moles:
                r.mass = r.moles * r.mw
            elif r.mw > 0 and r.moles <= 0 < r.mass:
                r.moles = r.mass / r.mw
        auxiliaries = [mm.AuxiliaryMaterial(name=r["Name"], mass=r["Mass (g)"], benign=r["Benign?"])
                       for r in clean_rows(aux_df, AUX_DEFAULTS)]

        results = mm.compute_all_mass_metrics(
            reactants=reactants, product_mw=product_mw, product_actual_mass=product_actual_mass,
            product_moles=product_moles, auxiliaries=auxiliaries, water_mass=water_mass,
            product_coefficient=product_coef, product_carbons=int(product_carbons),
        )
        st.session_state.routes[save_target]["mass_metrics"] = results
        st.session_state.routes[save_target]["product_name"] = product_name
        st.session_state.mass_last = {
            "results": results, "route": save_target,
            "warnings": mm.validate_inputs(reactants, product_mw, product_actual_mass),
            "compounds": [r.name for r in reactants] + [a.name for a in auxiliaries],
        }

    last = st.session_state.get("mass_last")
    if last:
        results = last["results"]
        st.success(f"Showing the last calculation (saved to **{last['route']}**). "
                   "Click Calculate again after changing any input.")
        for w in last["warnings"]:
            st.warning(w)

        st.markdown("---")
        st.subheader("Results")

        r1, r2, r3, r4 = st.columns(4)
        with r1:
            color = viz.metric_color(results["Atom Economy (AE, %)"], 80, 60)
            viz.render_colored_metric("Atom Economy (AE)", results["Atom Economy (AE, %)"], "%",
                                       color, "Theoretical % of reactant mass retained in product "
                                              "at 100% yield. Higher = less atom waste by design.")
        with r2:
            color = viz.metric_color(results["Reaction Mass Efficiency (RME, %)"], 70, 50)
            viz.render_colored_metric("Reaction Mass Efficiency (RME)",
                                       results["Reaction Mass Efficiency (RME, %)"], "%", color,
                                       "Actual % of charged reactant mass recovered as product "
                                       "-- captures real yield & stoichiometric excess, unlike AE.")
        with r3:
            color = viz.metric_color(results["Process Mass Intensity (PMI, kg/kg)"], 5, 20,
                                      higher_is_better=False)
            viz.render_colored_metric("Process Mass Intensity (PMI)",
                                       results["Process Mass Intensity (PMI, kg/kg)"], "kg/kg", color,
                                       "Total mass in (incl. water) per unit product mass. "
                                       "PMI = 1 is the theoretical ideal (zero waste).")
        with r4:
            color = viz.metric_color(results["E-Factor (kg waste/kg product)"], 5, 20,
                                      higher_is_better=False)
            viz.render_colored_metric("E-Factor",
                                       results["E-Factor (kg waste/kg product)"], "kg/kg", color,
                                       "kg waste per kg product (PMI - 1, Eq. 10.27). 0 = waste-free "
                                       "process (Sheldon's ideal).")

        r5, r6, r7, r8 = st.columns(4)
        with r5:
            ce = results.get("Carbon Efficiency (CE, %)")
            viz.render_colored_metric("Carbon Efficiency (CE)", ce, "%", viz.metric_color(ce, 80, 60),
                                       "% of reactant carbon atoms retained in the isolated product.")
        with r6:
            color = viz.metric_color(results["Mass Productivity (MP, %)"], 20, 5)
            viz.render_colored_metric("Mass Productivity (MP)",
                                       results["Mass Productivity (MP, %)"], "%", color,
                                       "Reciprocal of MI as % (Eq. 10.28) -- share of the input mass "
                                       "that ends up as product; higher is better.")
        with r7:
            erf = results.get("Excess Reactant Factor (ERF)")
            viz.render_colored_metric("Excess Reactant Factor (ERF)", erf, "",
                                       viz.metric_color(erf, 1.1, 1.5, higher_is_better=False),
                                       "= stoichiometric factor SF. 1 -> no excess reactant used. "
                                       "Values >> 1 signal large reagent excess and waste.")
        with r8:
            color = viz.metric_color(results["Mass Intensity excl. water (MI, kg/kg)"], 5, 20,
                                      higher_is_better=False)
            viz.render_colored_metric("Mass Intensity (MI, excl. water)",
                                       results["Mass Intensity excl. water (MI, kg/kg)"], "kg/kg",
                                       color, "Like PMI but excludes water, avoiding skew in "
                                              "aqueous-heavy processes.")

        r9, r10, r11, _ = st.columns(4)
        with r9:
            y = results.get("Percentage Yield (%)")
            viz.render_colored_metric("Percentage Yield", y, "%", viz.metric_color(y, 80, 50),
                                       "Actual / theoretical product mass (Eqs. 10.1-10.2).")
        with r10:
            viz.render_colored_metric("Theoretical Yield", results.get("Theoretical Yield (g)"), "g",
                                       viz.GREEN, "Limiting reagent moles x [P/A] x product MW.")
        with r11:
            emy = results["Effective Mass Yield (EMY, %)"]
            viz.render_colored_metric("Effective Mass Yield (EMY)", emy, "%", viz.metric_color(emy, 50, 20),
                                       "Product mass / mass of non-benign materials (Eq. 10.9). Tick "
                                       "'Benign?' for harmless inputs; water is always benign.")

        with st.expander("Full results table"):
            st.dataframe(dx.build_summary_dataframe(results), hide_index=True, width="stretch")

# ===========================================================================
# PAGE 3: REACTION ECO-SCALE SCORING
# ===========================================================================

elif page == "Reaction Eco-Scale Scoring":
    st.title("🏷️ Reaction Eco-Scale Scoring")
    st.caption(
        "The Eco-Scale (Van Aken et al., 2006) starts at 100 points and subtracts "
        "penalties for deviations from an ideal reaction: 100% yield, minimal "
        "operator risk, minimal environmental impact (Tamargo et al., 2024, Sec. "
        "10.1.2.1)."
    )

    save_target = st.selectbox("Save results to comparison slot:", ["Route A", "Route B"], key="eco_save")
    route_name = st.text_input("Route / reaction label", "Reaction Route", key="eco_route_name")

    pf = st.session_state.prefill
    eco = pf.get("eco", {})
    v = st.session_state.prefill_version

    def _idx(options, value):
        options = list(options)
        return options.index(value) if value in options else 0

    col1, col2 = st.columns(2)
    with col1:
        yield_pct = st.slider("Product yield (%)", 0.0, 100.0,
                               float(eco.get("yield_pct", 90.0)), 0.5,
                               key=f"ecoy_{v}",
                               help="Penalty = (100 - %yield) / 2")
        price_category = st.selectbox(
            "Price of reaction components", list(es.PRICE_PENALTIES.keys()), key="eco_price",
            help="Cost of all reaction components needed to make 10 mmol of product (Van Aken et al.).")
    with col2:
        technical_setups = st.multiselect(
            "Technical setup requirements", list(es.TECHNICAL_SETUP_PENALTIES.keys()),
            default=eco.get("setups", ["Common setup"]),
            key=f"ecos_{v}",
            help="Select all equipment/conditions used beyond standard glassware."
        )
        temp_time_category = st.selectbox(
            "Temperature & time conditions", list(es.TEMPERATURE_TIME_PENALTIES.keys()),
            index=_idx(es.TEMPERATURE_TIME_PENALTIES, eco.get("temp_time")),
            key=f"ecot_{v}")

    st.subheader("Safety: hazard symbols of each compound")
    st.caption(
        "Penalties are charged **per compound** (Table 10.4: nitrobenzene T + N = 10, methanol T + F = 10). "
        "Tick the symbols on each reagent/solvent/catalyst label. GHS mapping: N = GHS09 (environment); "
        "T = GHS06 acute tox. cat. 3 or GHS08 (CMR); T+ = GHS06 cat. 1-2; F = GHS02 flammable cat. 2-3; "
        "F+ = GHS02 cat. 1 (e.g. diethyl ether, pentane); E = GHS01. F+ replaces F and T+ replaces T."
    )
    seed = pf.get("compounds") or (st.session_state.get("mass_last") or {}).get("compounds") \
        or ["Benzyl alcohol", "p-Toluenesulfonyl chloride", "Triethylamine", "Toluene"]
    hazard_cols = list(es.SAFETY_SYMBOLS)
    safety_df = st.data_editor(
        pd.DataFrame([{"Compound": c, **{h: False for h in hazard_cols}} for c in dict.fromkeys(seed)]),
        num_rows="dynamic", width="stretch", hide_index=True, key=f"eco_safety_{v}",
        column_config={h: st.column_config.CheckboxColumn(h, help=es.SAFETY_SYMBOLS[h], width="small")
                       for h in hazard_cols},
    )

    st.subheader("Workup & purification steps")
    st.caption(
        "Add **one row per step** actually performed; penalties add up (Table 10.4: extraction + "
        "washing + crystallization = 3 + 3 + 1). Steps with 0 penalty may be listed for completeness."
    )
    steps_seed = eco.get("workup_steps") or ([eco["workup"]] if eco.get("workup") else [])
    workup_df = st.data_editor(
        pd.DataFrame({"Step": pd.Series(steps_seed, dtype="object")}),
        num_rows="dynamic", width="stretch", hide_index=True, key=f"eco_workup_{v}",
        column_config={"Step": st.column_config.SelectboxColumn(
            options=list(es.WORKUP_PENALTIES), required=True,
            help="Use 'Liquid-liquid extraction' for every extraction or aqueous wash.")},
    )

    if st.button("🧮 Calculate Eco-Scale", type="primary"):
        hazards = []
        for _, row in safety_df.iterrows():
            hazards += es.compound_hazards([h for h in hazard_cols if row.get(h) is True])
        steps = [str(s) for s in workup_df["Step"].tolist() if isinstance(s, str) and s in es.WORKUP_PENALTIES]
        inputs = es.EcoScaleInputs(
            route_name=route_name, yield_pct=yield_pct, price_category=price_category,
            safety_hazards=hazards, technical_setups=technical_setups,
            temp_time_category=temp_time_category, workup_steps=steps,
        )
        results = es.compute_eco_scale(inputs)
        st.session_state.routes[save_target]["eco_scale"] = results
        st.session_state.eco_last = {"results": results, "route": save_target}

    last = st.session_state.get("eco_last")
    if last:
        results = last["results"]
        rating = es.eco_scale_rating(results["Eco-Scale Score"])
        st.success(f"Showing the last calculation (saved to **{last['route']}**). "
                   "Click Calculate again after changing any input.")

        st.markdown("---")
        gcol, bcol = st.columns([1, 1.4])
        with gcol:
            st.plotly_chart(viz.eco_scale_gauge(results["Eco-Scale Score"],
                                                f"{results['Route']} Eco-Scale"), width="stretch")
            st.metric("Rating", rating)
        with bcol:
            st.plotly_chart(viz.penalty_breakdown_bar(results), width="stretch")

        st.dataframe(dx.build_summary_dataframe(results), hide_index=True, width="stretch")

# ===========================================================================
# PAGE 4: GLOBAL HAZARD & TOXICITY IMPACT
# ===========================================================================

elif page == "Global Hazard & Toxicity Impact":
    st.title("🌍 Global Hazard & Toxicity Impact")
    st.caption(
        "Impact-based metrics that go beyond mass to capture environmental and "
        "human-health consequences (Tamargo et al., 2024, Secs. 10.1.2.2 - "
        "10.1.2.3). Hazard scores follow the chapter's printed equations, Y = X(target) / X(actual): "
        "Y ≥ 1 is within the target threshold; Y < 1 exceeds it. (The chapter's prose describes the "
        "reciprocal; the results table also lists X(actual) / X(target) so either reading is available.) "
        "Use the same units for actual and target values."
    )

    save_target = st.selectbox("Save results to comparison slot:", ["Route A", "Route B"], key="hz_save")

    st.subheader("Global Hazard Ratios")
    st.caption("Enter the calculated life-cycle burden (actual) and the regulatory/sustainable "
               "target for each category.")
    g1, g2, g3, g4, g5 = st.columns(5)
    with g1:
        ap_actual = st.number_input("AP actual (SO₂-eq)", value=1.0, min_value=0.0)
        ap_target = st.number_input("AP target (SO₂-eq)", value=1.0, min_value=0.0)
    with g2:
        odp_actual = st.number_input("ODP actual (CFC-11-eq)", value=1.0, min_value=0.0)
        odp_target = st.number_input("ODP target (CFC-11-eq)", value=1.0, min_value=0.0)
    with g3:
        sfp_actual = st.number_input("SFP actual (O₃)", value=1.0, min_value=0.0)
        sfp_target = st.number_input("SFP target (O₃)", value=1.0, min_value=0.0)
    with g4:
        gwp_actual = st.number_input("GWP actual (CO₂-eq)", value=1.0, min_value=0.0)
        gwp_target = st.number_input("GWP target (CO₂-eq)", value=1.0, min_value=0.0)
    with g5:
        adp_actual = st.number_input("ADP actual (Sb-eq)", value=1.0, min_value=0.0)
        adp_target = st.number_input("ADP target (Sb-eq)", value=1.0, min_value=0.0)

    st.markdown("---")
    st.subheader("Toxicological Hazard Metrics")
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Human Toxicity by Ingestion Potential (INGTP)**")
        c_water = st.number_input("Concentration in water, C_w", value=1.0, min_value=0.0)
        ld50 = st.number_input("LD50 (lethal dose, 50%)", value=100.0, min_value=0.0001)
        st.markdown("**Human Toxicity by Inhalation Potential (INHTP)**")
        c_air = st.number_input("Concentration in air, C_a", value=1.0, min_value=0.0)
        lc50 = st.number_input("LC50 (lethal concentration, 50%)", value=100.0, min_value=0.0001)
        st.markdown("**Air Toxicity (Y_AT, Eq. 10.37)**")
        x_at = st.number_input("Air toxicity from life-cycle emissions, X_AT", value=1.0, min_value=0.0)
        x_at_t = st.number_input("Target emission value, X_AT(T)", value=1.0, min_value=0.0)
    with t2:
        st.markdown("**Water Ecotoxicity (Y_WE, Eq. 10.38)**")
        x_we = st.number_input("1,4-DCB-eq emissions per capita per year, X_WE", value=1.0, min_value=0.0)
        x_we_t = st.number_input("Target emissions to freshwater, X_WE(T)", value=1.0, min_value=0.0)
        st.markdown("**Bioaccumulation Potential**")
        log_kow = st.number_input("log Kow (octanol-water partition coefficient)", value=2.0)
        st.markdown("**Environmental Persistence (EP)**")
        c0 = st.number_input("Initial concentration, C0", value=1.0, min_value=0.0)
        k = st.number_input("Degradation rate constant, k", value=0.1, min_value=0.0001)

    if st.button("🧮 Calculate Hazard & Toxicity Metrics", type="primary"):
        gh_inputs = hz.GlobalHazardInputs(
            ap_actual=ap_actual, ap_target=ap_target, odp_actual=odp_actual, odp_target=odp_target,
            sfp_actual=sfp_actual, sfp_target=sfp_target, gwp_actual=gwp_actual, gwp_target=gwp_target,
            adp_actual=adp_actual, adp_target=adp_target,
        )
        global_results = hz.compute_global_hazard_suite(gh_inputs)

        ingtp = hz.ingestion_toxicity_potential(c_water, ld50)
        inhtp = hz.inhalation_toxicity_potential(c_air, lc50)
        bap = hz.bioaccumulation_rating(log_kow)
        ep = hz.environmental_persistence(c0, k)

        y_at = hz.air_toxicity_score(x_at, x_at_t)
        y_we = hz.water_ecotoxicity_score(x_we, x_we_t)
        tox_results = {
            "Air Toxicity (Y_AT)": y_at,
            "Water Ecotoxicity (Y_WE)": y_we,
            "Ingestion Toxicity Potential (INGTP)": ingtp,
            "Inhalation Toxicity Potential (INHTP)": inhtp,
            "Bioaccumulation Potential": bap,
            "Environmental Persistence (EP)": ep,
        }

        combined = {**global_results, **tox_results}
        burden = {
            f"{k.split('(')[0].strip()} burden X/X(T)": hz.burden_ratio(a, t)
            for k, (a, t) in {
                "Air Toxicity": (x_at, x_at_t), "Water Ecotoxicity": (x_we, x_we_t),
                "Acidification Potential": (ap_actual, ap_target),
                "Ozone Depletion Potential": (odp_actual, odp_target),
                "Smog Formation Potential": (sfp_actual, sfp_target),
                "Global Warming Potential": (gwp_actual, gwp_target),
                "Abiotic Depletion Potential": (adp_actual, adp_target)}.items()}
        st.session_state.routes[save_target]["hazard_metrics"] = combined
        st.session_state.hz_last = {"global": global_results, "tox": tox_results,
                                    "burden": burden, "route": save_target}

    last = st.session_state.get("hz_last")
    if last:
        global_results, tox_results = last["global"], last["tox"]
        ingtp = tox_results["Ingestion Toxicity Potential (INGTP)"]
        inhtp = tox_results["Inhalation Toxicity Potential (INHTP)"]
        bap = tox_results["Bioaccumulation Potential"]
        ep = tox_results["Environmental Persistence (EP)"]
        st.success(f"Showing the last calculation (saved to **{last['route']}**). "
                   "Click Calculate again after changing any input.")

        st.markdown("---")
        st.subheader("Global Hazard Ratios (Y ≥ 1 = within target)")
        cols = st.columns(5)
        for c, (label, val) in zip(cols, global_results.items()):
            with c:
                color = viz.metric_color(val, good_threshold=1.0, moderate_threshold=0.7,
                                          higher_is_better=True)
                viz.render_colored_metric(label, val, "", color)

        st.subheader("Toxicological Hazard Metrics")
        acol1, acol2 = st.columns(2)
        for c, key in zip([acol1, acol2], ["Air Toxicity (Y_AT)", "Water Ecotoxicity (Y_WE)"]):
            with c:
                val = tox_results[key]
                viz.render_colored_metric(key, val, "", viz.metric_color(val, 1.0, 0.7),
                                           "Y = X(T)/X: ≥ 1 within target.")
        tcol1, tcol2, tcol3, tcol4 = st.columns(4)
        with tcol1:
            color = viz.metric_color(ingtp, 0.01, 0.1, higher_is_better=False)
            viz.render_colored_metric("INGTP", ingtp, "", color,
                                       "Lower = lower ingestion-route toxicity hazard.")
        with tcol2:
            color = viz.metric_color(inhtp, 0.01, 0.1, higher_is_better=False)
            viz.render_colored_metric("INHTP", inhtp, "", color,
                                       "Lower = lower inhalation-route toxicity hazard.")
        with tcol3:
            color = {"Low": viz.GREEN, "Moderate": viz.YELLOW, "High": viz.RED}[bap]
            viz.render_colored_metric("Bioaccumulation", bap, "", color,
                                       "Based on log Kow: <3.5 Low, 3.5-4.3 Moderate, >4.3 High.")
        with tcol4:
            color = viz.metric_color(ep, 10, 50, higher_is_better=False)
            viz.render_colored_metric("Persistence (EP)", ep, "", color,
                                       "Lower EP (faster degradation, higher k) is greener.")

        st.dataframe(dx.build_summary_dataframe({**global_results, **tox_results, **last["burden"]}),
                     hide_index=True, width="stretch")

# ===========================================================================
# PAGE 5: PROCESS COMPARISON & EXPORT
# ===========================================================================

elif page == "Process Comparison & Export":
    st.title("📊 Process Comparison & Export")
    st.caption(
        "Compare two saved reaction routes side-by-side on a normalized radar "
        "chart, then export a combined summary report."
    )

    routes = st.session_state.routes
    route_names = list(routes.keys())

    has_data = any(routes[r] for r in route_names)
    if not has_data:
        st.warning(
            "No saved routes yet. Go to the Mass-Based Metrics, Eco-Scale, or "
            "Hazard & Toxicity pages, run a calculation, and click the save "
            "button to populate Route A / Route B."
        )
    else:
        st.subheader("Radar Chart: AE, RME, CE, Eco-Scale, and 1/PMI")
        st.caption(
            "All axes are normalized to a 0-100 scale, where 100 represents the "
            "greenest possible outcome for that metric. 1/PMI is expressed as "
            "Mass Productivity (MP, %) so that higher values are always better."
        )

        missing = {r: [s for s, label in (("mass_metrics", "mass metrics"), ("eco_scale", "Eco-Scale"))
                       if s not in routes[r]] for r in route_names}
        for r, m in missing.items():
            if m and routes[r]:
                st.info(f"{r} has no saved {' or '.join(m).replace('_', ' ')}: those axes are drawn at 0.")

        def extract_radar_values(route_data: dict) -> dict:
            mass = route_data.get("mass_metrics", {})
            eco = route_data.get("eco_scale", {})
            return {
                "Atom Economy": mass.get("Atom Economy (AE, %)", 0),
                "RME": mass.get("Reaction Mass Efficiency (RME, %)", 0),
                "Carbon Efficiency": mass.get("Carbon Efficiency (CE, %)", 0),
                "Eco-Scale Score": eco.get("Eco-Scale Score", 0),
                "Mass Productivity (1/PMI)": mass.get("Mass Productivity (MP, %)", 0),
            }

        route_a_vals = extract_radar_values(routes["Route A"])
        route_b_vals = extract_radar_values(routes["Route B"])

        fig = viz.radar_chart("Route A", route_a_vals, "Route B", route_b_vals)
        st.plotly_chart(fig, width="stretch")

        st.markdown("---")
        st.subheader("Side-by-Side Summary")
        c1, c2 = st.columns(2)
        for c, rname in zip([c1, c2], route_names):
            with c:
                st.markdown(f"### {rname}")
                data = routes[rname]
                if not data:
                    st.info("No data saved for this route.")
                    continue
                for section in ["mass_metrics", "eco_scale", "hazard_metrics"]:
                    if section in data:
                        st.markdown(f"**{section.replace('_', ' ').title()}**")
                        st.dataframe(dx.build_summary_dataframe(data[section]),
                                     hide_index=True, width="stretch")

        st.markdown("---")
        st.subheader("⬇️ Export Combined Report")

        reports = []
        for rname in route_names:
            data = routes[rname]
            if data:
                reports.append(dx.build_combined_report(
                    route_name=rname,
                    mass_metrics=data.get("mass_metrics"),
                    eco_scale=data.get("eco_scale"),
                    hazard_metrics=data.get("hazard_metrics"),
                ))

        if reports:
            flat_rows = []
            for report in reports:
                flat = dx.flatten_report(report)
                flat_rows.append(flat)
            combined_df = pd.DataFrame(flat_rows)

            ecol1, ecol2 = st.columns(2)
            with ecol1:
                st.download_button(
                    "Download CSV report",
                    data=dx.to_csv_bytes(combined_df),
                    file_name="green_metrics_report.csv",
                    mime="text/csv",
                )
            with ecol2:
                st.download_button(
                    "Download JSON report",
                    data=dx.to_json_bytes(reports),
                    file_name="green_metrics_report.json",
                    mime="application/json",
                )

            with st.expander("Preview combined report table"):
                st.dataframe(combined_df, width="stretch")
