# Green Chemistry Metrics Studio

A modular Streamlit application for calculating and visualizing green
chemistry metrics, based on the classification and formulas presented in:

> Tamargo, R.J.I., Cosiñero, H.S.O., Potato, D.N.C., Dumrigue, A.H.P. (2024).
> *Metrics of Green Chemistry and Sustainability.* In: Sen, M. (Ed.),
> *Sustainable Green Catalytic Processes*, Chapter 10, pp. 225–258.
> Scrivener Publishing.

## Features

- **Dashboard Overview** — metric cheat-sheet and saved-route summary.
- **Mass-Based Metrics Calculator** — Atom Economy (AE), Reaction Mass
  Efficiency (RME), Process Mass Intensity (PMI), Mass Intensity (MI),
  E-Factor, Carbon Efficiency (CE), Mass Productivity (MP), and Excess
  Reactant Factor (ERF). Pre-loaded with the benzyl alcohol /
  p-toluenesulfonyl chloride esterification example from the source chapter.
- **Reaction Eco-Scale Scoring** — 100-point penalty-based scoring system
  (Van Aken et al., 2006) covering yield, cost, GHS safety hazards, technical
  setup, temperature/time, and workup/purification.
- **Global Hazard & Toxicity Impact** — Acidification (AP), Ozone Depletion
  (ODP), Smog Formation (SFP), Global Warming (GWP), and Abiotic Depletion
  (ADP) ratios, plus human toxicity (INGTP/INHTP), bioaccumulation, and
  environmental persistence.
- **Process Comparison & Export** — normalized radar chart comparing two
  saved routes, plus CSV/JSON export of the full metrics suite.

## Project structure

```
green_metrics_app/
├── app.py                     # Streamlit entry point (5 pages, sidebar nav)
├── requirements.txt
└── modules/
    ├── mass_metrics.py        # AE, RME, PMI, E-factor, CE, MP, ERF
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

## Notes on conventions

- Global hazard scores follow the source chapter's convention
  `Y = X(target) / X(actual)`, where **Y ≥ 1** means the process is within
  the sustainable/regulatory target, and **Y < 1** means it exceeds the
  target (i.e., a *higher* Y is *more favorable* in this app).
- Color coding throughout the UI: 🟩 green = ideal/favorable,
  🟨 yellow = moderate, 🟥 red = unfavorable — thresholds are documented
  inline via `st.caption` / tooltip text next to each metric.
