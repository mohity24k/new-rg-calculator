# Green Chemistry Metrics Studio

A modular Streamlit application for calculating and visualizing green
chemistry metrics, based on the classification and formulas presented in:

> Tamargo, R.J.I., Cosiñero, H.S.O., Potato, D.N.C., Dumrigue, A.H.P. (2024).
> *Metrics of Green Chemistry and Sustainability.* In: Sen, M. (Ed.),
> *Sustainable Green Catalytic Processes*, Chapter 10, pp. 225–258.
> Scrivener Publishing.

## Features

- **Dashboard Overview** — metric cheat-sheet and saved-route summary.
- **Upload PDF & Auto-fill** — a vision-language model (Qwen2.5-VL by default,
  any OpenAI-compatible endpoint) reads a paper's experimental section; the app
  resolves structures, computes MW / moles / masses, and pre-fills the
  calculators after the user has reviewed the table.
- **Mass-Based Metrics Calculator** — Atom Economy (AE, with stoichiometric
  coefficients), Reaction Mass Efficiency (RME), Process Mass Intensity (PMI),
  Mass Intensity (MI), E-Factor, Carbon Efficiency (CE), Mass Productivity (MP),
  Effective Mass Yield (EMY), Excess Reactant Factor (ERF = stoichiometric
  factor SF), theoretical and percentage yield. Inputs are checked for common
  lab mistakes (mg typed as g, wrong limiting reagent, missing reactant).
  Pre-loaded with the benzyl alcohol / p-toluenesulfonyl chloride
  esterification example from the source chapter.
- **Reaction Eco-Scale Scoring** — 100-point penalty-based scoring system
  (Van Aken et al., 2006). Safety penalties are entered per compound and
  workup penalties per step, so both reactions of Table 10.4 are reproduced.
- **Global Hazard & Toxicity Impact** — Air Toxicity (AT), Water Ecotoxicity
  (WE), Acidification (AP), Ozone Depletion (ODP), Smog Formation (SFP),
  Global Warming (GWP), and Abiotic Depletion (ADP) ratios, plus human
  toxicity (INGTP/INHTP), bioaccumulation, and environmental persistence.
- **Process Comparison & Export** — normalized radar chart comparing two
  saved routes, plus CSV/JSON export of the full metrics suite.

## Project structure

```
green_metrics_app/
├── app.py                     # Streamlit entry point (5 pages, sidebar nav)
├── requirements.txt
├── tests/                     # pytest suite (formulas, PDF pipeline, UI)
└── modules/
    ├── mass_metrics.py        # AE, RME, PMI, MI, E-factor, CE, MP, EMY, ERF, yield
    ├── pdf_extraction.py      # PDF -> VLM -> review table -> calculator inputs
    ├── autofill_page.py       # Streamlit page for the PDF auto-fill
    ├── inputs.py              # cleaning of the editable tables
    ├── eco_scale.py            # Eco-Scale penalty tables & scoring
    ├── hazard_metrics.py       # Global hazard ratios & toxicity metrics
    ├── visualization.py        # Plotly radar/gauge/bar charts, color coding
    └── data_export.py          # CSV/JSON report builders
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
pip install pytest
python -m pytest -q tests
```

The suite checks every formula against the chapter's worked examples
(AE 88 %, RME 72 %, MI 23.2, MP 4.3 %, CE 83.7 %, SF 1.10, Eco-Scale 64 for
Table 10.4), lab edge cases (zero product mass, blank table rows, wrong units,
2 A + B stoichiometry), the PDF pipeline with model answers for typical SI
procedures, and every UI page end-to-end (Streamlit AppTest, with a stub
model server).

## Running the extraction model

Serve the model with any OpenAI-compatible server, e.g. on one A100:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct --max-model-len 16384 --limit-mm-per-prompt image=1
```

and set `VLM_BASE_URL` (e.g. `http://<host>:8000/v1`), `VLM_MODEL` and
`VLM_API_KEY` in `.streamlit/secrets.toml`, or type them under
"Extraction server settings". Pages are rendered at up to 2000 px
(~3,600 image tokens per page for Qwen2.5-VL).

## Notes on conventions

- Global hazard scores follow the source chapter's printed equations
  `Y = X(target) / X(actual)`, where **Y ≥ 1** means the process is within
  the sustainable/regulatory target, and **Y < 1** means it exceeds the
  target (i.e., a *higher* Y is *more favorable* in this app). The chapter's
  prose describes the reciprocal ("lower = cleaner"), so the results table
  also lists `X(actual) / X(target)`.
- The Eco-Scale yield penalty is computed exactly as (100 − yield)/2; the
  chapter rounds 6.5 down to 6 in Table 10.4 (reaction 2: 77.5 here, 78 there).
- Mass Productivity is 1/MI × 100 (Eq. 10.28), so it excludes water.
- Color coding throughout the UI: 🟩 green = ideal/favorable,
  🟨 yellow = moderate, 🟥 red = unfavorable — thresholds are documented
  inline via `st.caption` / tooltip text next to each metric.
