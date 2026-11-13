"""Reference page: how the numbers are made, where the data comes from, and what the tool cannot do."""

import streamlit as st

import gms
from gms import config, precedents, report
from gms.chem import hazard, solvents
from gms.metrics import registry
from ui import state

state.init()
st.title("Methods and limitations")
st.caption(f"Green Metrics Studio version {gms.__version__}")
st.warning(report.DISCLAIMER)

st.subheader("How the numbers are made")
st.markdown("""
- **Metrics** (atom economy, RME, PMI, E-factor, carbon efficiency, mass productivity, effective mass yield,
  excess reactant factor, yield, EcoScale) are computed from the published formulas (Tamargo et al. 2024;
  Van Aken et al. 2006). No model computes or adjusts a metric.
- **Hazard data** are GHS hazard statements from PubChem. The EU harmonised classification is used where it
  exists; otherwise the company notifications to ECHA, keeping a statement that at least half of the reports
  contain. Hazard scores (1 to 10) follow the CHEM21 guide for health and environment.
- **The verdict** (green, borderline, not green) is a weighted mean of six desirabilities: atom economy, RME, PMI,
  EcoScale, the worst hazard score, and the CHEM21 class of the solvents. The limits and weights are provisional
  defaults. The score card shows how robust the verdict is when the weights change.
- **The diagnosis** applies an idealised fix to a copy of the reaction and runs the calculator again. The gain
  is the difference.
- **Alternatives** come from a curated table of substitutions with literature references, filtered by reaction
  class, and from two general rules (less excess, higher concentration). Each alternative must pass the
  no-regression check. Changes are computed at the original yield, and a break-even yield is given. The app never
  predicts the yield of an untested change.
- **Estimates.** A value that depends on an assumed amount is marked "estimate" on screen and in every export.
""")

st.subheader("Known limitations")
st.markdown("""
- **Three reaction classes.** Suggestions for the reaction solvent, reagents and catalysts exist only for amide
  coupling, Suzuki-Miyaura coupling and reductive amination. Workup and purification suggestions, the score card
  and the diagnosis work for any reaction.
- **A suggestion is not a tested procedure.** A swap that worked in the cited study can fail for your substrates
  (solubility, selectivity, rate, epimerisation). The amounts in a suggestion (same volume, cited equivalents) are
  starting points and are marked as estimates.
- **Hazard data can be missing or wrong.** Some compounds have no GHS data in PubChem (shown as "no GHS data",
  which means unknown, not safe). Company notifications can disagree. Hazards that GHS statements do not capture
  (for example the explosive potential of some coupling reagents) are in the text of a suggestion, not in the scores.
- **The EcoScale counts "highly flammable" like "toxic".** A swap from a non-flammable hazardous solvent to a
  recommended flammable one (dichloromethane to ethyl acetate) loses 5 EcoScale points. The check tolerates 5
  points and shows the loss as a trade-off.
- **Price.** The app has no price data. The EcoScale price penalty is 0 unless you choose a category, and the cost
  check runs only if the administrator adds prices.
- **PMI of a paper is often a lower bound.** Papers rarely give the amounts of extraction solvent, silica and
  eluent. Missing amounts are not invented; the score card says which materials are not counted.
- **Similar-reaction data comes from patents.** It has no workup amounts and contains text-mining errors. It is
  used for precedent counts and typical ranges, not for a PMI ranking and not to predict yields.
- **No yield or condition model.** This version has no trained model. Precedent yields are shown as ranges of
  other substrates.
- **Stoichiometry.** Coefficients are 1 unless you set them. The app suggests coefficients from an atom balance
  on carbon; it does not balance the full equation.
- **PDF extraction** needs the project's own model server and is offered only while that server answers. Extracted
  values must be checked against the paper. Products given only as a compound number ("3a") are not resolved to a
  structure: enter a name or SMILES.
- **One reaction step at a time.** Multi-step routes and retrosynthesis are not part of this version.
""")

st.subheader("Data sources and licences")
seed = hazard.store().seed().get("meta", {})
pm = precedents.meta()
n_solv = len(solvents.table())
st.markdown(f"""
- **Formulas:** R. J. I. Tamargo et al., *Metrics of Green Chemistry and Sustainability*, in *Sustainable Green
  Catalytic Processes*, Scrivener, 2024, ch. 10. EcoScale: K. Van Aken, L. Strekowski, L. Patiny, *Beilstein J. Org.
  Chem.* 2006, 2, 3.
- **Hazard statements:** PubChem (National Library of Medicine), GHS Classification section; primary sources are
  the EU CLP Regulation Annex VI and the ECHA C&L Inventory. Bundled data for {len(hazard.store().seed().get('records', {}))}
  compounds built on {seed.get('built', 'n/a')}; other compounds are looked up when needed and cached.
- **Solvent ranking:** CHEM21 selection guide, D. Prat et al., *Green Chem.* 2016, 18, 288 (CC BY 3.0);
  {n_solv} solvents, read from the ACS GCI Pharmaceutical Roundtable reproduction of the guide.
- **Substitution rules:** curated from the literature; every rule lists its references (gms/data/substitutions.yaml).
- **Precedents:** {pm.get('name', 'not installed')}. Licence: {pm.get('license', 'n/a')} {pm.get('citation', '')}
- **Name to structure:** OPSIN (MIT licence) and PubChem.
""")

st.subheader("Privacy")
st.markdown("""
- Uploaded PDFs are processed in memory, sent only to this installation's extraction server, and not stored.
- Chemical names that the app does not know are sent to PubChem to find the structure and the hazard data.
- When you accept or reject a suggestion, or send a comment, the app stores the decision, the comment and the
  reaction record on this server, with a random session number. No name, e-mail or IP address is stored.
""")

with st.expander("Thresholds and weights in use"):
    st.json({"metrics": {k: {"good": m.good, "moderate": m.moderate, "tolerance_rel": m.tolerance_rel,
                             "tolerance_abs": m.tolerance_abs} for k, m in registry.metrics().items()},
             "scoring": config.load("scoring"), "hazard": {k: v for k, v in config.load("hazard").items() if k != "scores"}})
