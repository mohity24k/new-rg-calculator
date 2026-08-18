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

# ---------------------------------------------------------------------------
# Page config & session state
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Green Chemistry Metrics Studio", layout="wide", page_icon="🌱")

if "routes" not in st.session_state:
    # Stores computed metrics for up to two comparison routes: "Route A", "Route B"
    st.session_state.routes = {"Route A": {}, "Route B": {}}

CARBON_MW = 12.011

# ---------------------------------------------------------------------------
# Sample data: benzyl alcohol + p-toluenesulfonyl chloride esterification
# (Tamargo et al. 2024, Section 10.1.1.2, adapted from Curzons et al.)
# ---------------------------------------------------------------------------

def default_reactants():
    return pd.DataFrame([
        {"Name": "Benzyl alcohol", "MW (g/mol)": 108.14, "Moles charged (mol)": 0.10,
         "Mass charged (g)": 10.81, "Carbons/molecule": 7,
         "Stoich. ratio (per limiting reagent)": 1.0, "Limiting reagent?": True},
        {"Name": "p-Toluenesulfonyl chloride", "MW (g/mol)": 190.64, "Moles charged (mol)": 0.115,
         "Mass charged (g)": 21.90, "Carbons/molecule": 7,
         "Stoich. ratio (per limiting reagent)": 1.0, "Limiting reagent?": False},
    ])


def default_auxiliaries():
    return pd.DataFrame([
        {"Name": "Triethylamine (base)", "Mass (g)": 15.0},
        {"Name": "Toluene (solvent)", "Mass (g)": 500.0},
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
        "Mass-Based Metrics Calculator",
        "Reaction Eco-Scale Scoring",
        "Global Hazard & Toxicity Impact",
        "Process Comparison & Export",
    ],
)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Based on: Tamargo, R.J.I. *et al.* (2024). *Metrics of Green Chemistry and "
    "Sustainability.* In *Sustainable Green Catalytic Processes* "  \n
    "https://doi.org/10.1002/9781394212767.ch10"
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
               compute AE, RME, PMI, E-factor, CE, MP, and ERF.
            2. **Reaction Eco-Scale Scoring** — score a reaction's overall
               "greenness" (0–100) based on yield, cost, safety, setup, conditions,
               and workup.
            3. **Global Hazard & Toxicity Impact** — assess acidification, ozone
               depletion, smog formation, global warming, abiotic depletion,
               and human/environmental toxicity ratios.
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
            ["Mass Productivity (MP)", "Reciprocal of PMI, as %"],
            ["Eco-Scale", "100 - penalties; 100 = ideal reaction"],
        ], columns=["Metric", "Meaning"])
        st.dataframe(cheat, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.subheader("Saved Comparison Routes")
    rcol1, rcol2 = st.columns(2)
    for rc, rname in zip([rcol1, rcol2], ["Route A", "Route B"]):
        with rc:
            st.markdown(f"**{rname}**")
            data = st.session_state.routes[rname]
            if data:
                st.json(data, expanded=False)
            else:
                st.info("No data saved yet. Visit the calculator pages and click "
                        "'Save to Route' to populate this comparison slot.")

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

    st.subheader("1. Reactants")
    st.caption(
        "Enter every stoichiometric reactant. Mark exactly one as the **limiting "
        "reagent** (stoich. ratio = 1.0). For other reactants, set the "
        "stoichiometric ratio relative to the limiting reagent (e.g., 1.15 mol "
        "per 1 mol limiting reagent) to enable the Excess Reactant Factor (ERF)."
    )
    reactants_df = st.data_editor(
        default_reactants(), num_rows="dynamic", use_container_width=True, key="reactants_editor"
    )

    st.subheader("2. Auxiliary Materials (solvents, bases, catalysts, workup reagents)")
    st.caption(
        "These materials are NOT incorporated into the product but still count "
        "toward PMI, E-factor, and Mass Intensity."
    )
    aux_df = st.data_editor(
        default_auxiliaries(), num_rows="dynamic", use_container_width=True, key="aux_editor"
    )

    st.subheader("3. Product")
    pcol1, pcol2, pcol3, pcol4 = st.columns(4)
    with pcol1:
        product_name = st.text_input("Product name", DEFAULT_PRODUCT["name"])
    with pcol2:
        product_mw = st.number_input("Product MW (g/mol)", min_value=0.0,
                                      value=DEFAULT_PRODUCT["mw"], step=0.01,
                                      help="Used for theoretical Atom Economy (AE).")
    with pcol3:
        product_actual_mass = st.number_input("Actual isolated mass (g)", min_value=0.0,
                                                value=DEFAULT_PRODUCT["actual_mass"], step=0.01,
                                                help="Used for RME, PMI, E-factor, MP, CE.")
    with pcol4:
        product_carbons = st.number_input("Carbons per product molecule", min_value=0,
                                           value=DEFAULT_PRODUCT["carbons"], step=1,
                                           help="Used for Carbon Efficiency (CE).")

    product_moles = st.number_input(
        "Product moles obtained (mol)", min_value=0.0, value=DEFAULT_PRODUCT["moles"], step=0.001,
        help="Used to derive product carbon mass for Carbon Efficiency."
    )

    water_mass = st.number_input(
        "Water mass in reaction vessel (g)", min_value=0.0, value=0.0, step=1.0,
        help="Included in PMI (per ACS GCI convention) but excluded from Mass Intensity (MI)."
    )

    if st.button("🧮 Calculate Mass-Based Metrics", type="primary"):
        reactants = [
            mm.Reactant(
                name=row["Name"], mw=row["MW (g/mol)"], moles=row["Moles charged (mol)"],
                mass=row["Mass charged (g)"], carbons=int(row["Carbons/molecule"]),
                stoich_ratio=row["Stoich. ratio (per limiting reagent)"],
                is_limiting=bool(row["Limiting reagent?"]),
            )
            for _, row in reactants_df.iterrows()
        ]
        auxiliaries = [
            mm.AuxiliaryMaterial(name=row["Name"], mass=row["Mass (g)"])
            for _, row in aux_df.iterrows()
        ]

        results = mm.compute_all_mass_metrics(
            reactants=reactants, product_mw=product_mw, product_actual_mass=product_actual_mass,
            product_moles=product_moles, auxiliaries=auxiliaries, water_mass=water_mass,
        )

        # Carbon efficiency (needs explicit product carbon count)
        c_mass_reactants = sum(mm.carbon_mass_from_moles(r.moles, r.carbons) for r in reactants)
        c_mass_product = mm.carbon_mass_from_moles(product_moles, product_carbons)
        ce = mm.carbon_efficiency(c_mass_product, c_mass_reactants)
        results["Carbon Efficiency (CE, %)"] = ce

        # Excess Reactant Factor (aggregate across non-limiting reactants)
        limiting = next((r for r in reactants if r.is_limiting), None)
        if limiting:
            stoich_mass_total, excess_mass_total = 0.0, 0.0
            for r in reactants:
                stoich_moles = limiting.moles * r.stoich_ratio
                stoich_mass = stoich_moles * r.mw
                excess_moles = max(0.0, r.moles - stoich_moles)
                excess_mass = excess_moles * r.mw
                stoich_mass_total += stoich_mass
                excess_mass_total += excess_mass
            erf = mm.excess_reactant_factor(stoich_mass_total, excess_mass_total)
            results["Excess Reactant Factor (ERF)"] = erf

        st.session_state.routes[save_target]["mass_metrics"] = results
        st.session_state.routes[save_target]["product_name"] = product_name
        st.success(f"Metrics calculated and saved to **{save_target}**.")

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
                                       "kg waste per kg product (PMI - 1). 0 = waste-free process "
                                       "(Sheldon's ideal).")

        r5, r6, r7, r8 = st.columns(4)
        with r5:
            color = viz.metric_color(ce, 80, 60)
            viz.render_colored_metric("Carbon Efficiency (CE)", ce, "%", color,
                                       "% of reactant carbon atoms retained in the isolated product.")
        with r6:
            color = viz.metric_color(results["Mass Productivity (MP, %)"], 20, 5)
            viz.render_colored_metric("Mass Productivity (MP)",
                                       results["Mass Productivity (MP, %)"], "%", color,
                                       "Reciprocal of PMI (as %) -- business-friendly resource "
                                       "utilization indicator; higher is better.")
        with r7:
            if "Excess Reactant Factor (ERF)" in results:
                color = viz.metric_color(results["Excess Reactant Factor (ERF)"], 1.1, 1.5,
                                          higher_is_better=False)
                viz.render_colored_metric("Excess Reactant Factor (ERF)",
                                           results["Excess Reactant Factor (ERF)"], "", color,
                                           "ERF = 1 -> no excess reactant used. Values >> 1 signal "
                                           "large reagent excess and associated waste.")
        with r8:
            color = viz.metric_color(results["Mass Intensity excl. water (MI, kg/kg)"], 5, 20,
                                      higher_is_better=False)
            viz.render_colored_metric("Mass Intensity (MI, excl. water)",
                                       results["Mass Intensity excl. water (MI, kg/kg)"], "kg/kg",
                                       color, "Like PMI but excludes water, avoiding skew in "
                                              "aqueous-heavy processes.")

        with st.expander("Full results table"):
            st.dataframe(dx.build_summary_dataframe(results), hide_index=True, use_container_width=True)

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
    route_name = st.text_input("Route / reaction label", "Reaction Route")

    col1, col2 = st.columns(2)
    with col1:
        yield_pct = st.slider("Product yield (%)", 0.0, 100.0, 90.0, 0.5,
                               help="Penalty = (100 - %yield) / 2")
        price_category = st.selectbox("Price of reaction components", list(es.PRICE_PENALTIES.keys()),
                                       help="Estimated cost to obtain the target mmol of product.")
        safety_hazards = st.multiselect(
            "Applicable hazard warning symbols (GHS-based)", list(es.SAFETY_PENALTIES.keys()),
            help="Select every hazard class present among reagents/solvents used."
        )
    with col2:
        technical_setups = st.multiselect(
            "Technical setup requirements", list(es.TECHNICAL_SETUP_PENALTIES.keys()),
            default=["Common setup"],
            help="Select all equipment/conditions used beyond standard glassware."
        )
        temp_time_category = st.selectbox("Temperature & time conditions",
                                           list(es.TEMPERATURE_TIME_PENALTIES.keys()))
        workup_category = st.selectbox("Workup / purification method",
                                        list(es.WORKUP_PENALTIES.keys()))

    if st.button("🧮 Calculate Eco-Scale", type="primary"):
        inputs = es.EcoScaleInputs(
            route_name=route_name, yield_pct=yield_pct, price_category=price_category,
            safety_hazards=safety_hazards, technical_setups=technical_setups,
            temp_time_category=temp_time_category, workup_category=workup_category,
        )
        results = es.compute_eco_scale(inputs)
        rating = es.eco_scale_rating(results["Eco-Scale Score"])

        st.session_state.routes[save_target]["eco_scale"] = results
        st.success(f"Eco-Scale calculated and saved to **{save_target}**.")

        st.markdown("---")
        gcol, bcol = st.columns([1, 1.4])
        with gcol:
            st.plotly_chart(viz.eco_scale_gauge(results["Eco-Scale Score"], f"{route_name} Eco-Scale"),
                             use_container_width=True)
            st.metric("Rating", rating)
        with bcol:
            st.plotly_chart(viz.penalty_breakdown_bar(results), use_container_width=True)

        st.dataframe(dx.build_summary_dataframe(results), hide_index=True, use_container_width=True)

# ===========================================================================
# PAGE 4: GLOBAL HAZARD & TOXICITY IMPACT
# ===========================================================================

elif page == "Global Hazard & Toxicity Impact":
    st.title("🌍 Global Hazard & Toxicity Impact")
    st.caption(
        "Impact-based metrics that go beyond mass to capture environmental and "
        "human-health consequences (Tamargo et al., 2024, Secs. 10.1.2.2 - "
        "10.1.2.3). Global hazard scores follow Y = X(target) / X(actual): "
        "Y ≥ 1 is within the target threshold; Y < 1 exceeds it."
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
    with t2:
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

        tox_results = {
            "Ingestion Toxicity Potential (INGTP)": ingtp,
            "Inhalation Toxicity Potential (INHTP)": inhtp,
            "Bioaccumulation Potential": bap,
            "Environmental Persistence (EP)": ep,
        }

        combined = {**global_results, **tox_results}
        st.session_state.routes[save_target]["hazard_metrics"] = combined
        st.success(f"Hazard/toxicity metrics calculated and saved to **{save_target}**.")

        st.markdown("---")
        st.subheader("Global Hazard Ratios (Y ≥ 1 = within target)")
        cols = st.columns(5)
        for c, (label, val) in zip(cols, global_results.items()):
            with c:
                color = viz.metric_color(val, good_threshold=1.0, moderate_threshold=0.7,
                                          higher_is_better=True)
                viz.render_colored_metric(label, val, "", color)

        st.subheader("Toxicological Hazard Metrics")
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
            viz.render_colored_metric("Bioaccumulation", 0, bap, color,
                                       "Based on log Kow: <3.5 Low, 3.5-4.3 Moderate, >4.3 High.")
        with tcol4:
            color = viz.metric_color(ep, 10, 50, higher_is_better=False)
            viz.render_colored_metric("Persistence (EP)", ep, "", color,
                                       "Lower EP (faster degradation, higher k) is greener.")

        st.dataframe(dx.build_summary_dataframe(combined), hide_index=True, use_container_width=True)

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
        st.plotly_chart(fig, use_container_width=True)

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
                                     hide_index=True, use_container_width=True)

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
                st.dataframe(combined_df, use_container_width=True)
