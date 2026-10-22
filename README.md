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
    ├── extraction/            # dataset pipeline: page filter, multi-reaction schema, batching
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

## Building a dataset (many papers, many reactions)

`modules/extraction/` is a second extraction pipeline made for datasets. The classic Upload PDF
mode is unchanged; the page also offers it as **Several reactions per document (new)**.

What it does differently:

- **Page filter.** Pages are labelled from their text layer (procedure, scanned, spectrum,
  references, other). Spectra, reference lists and discussion pages are not sent to the model.
- **Text first.** Procedure pages are sent as text (exact numbers, ~4x fewer tokens than an
  image). Only scanned pages and optimization tables are sent as images (1280 px).
- **Many reactions per document.** Each substrate-scope entry is its own record. General
  procedures are extracted too, and entries that cite them ("following GP A") inherit the
  missing amounts, solvent, conditions and workup, with a flag.
- **Optimization tables** (entry / solvent / base / yield) are saved separately: they show the
  effect of one change on the same reaction.
- **Every mass is tagged by role**: reactant, reagent, catalyst, base, additive,
  reaction_solvent, workup, purification, water, product.
- **Parallel requests with structured output.** 32 requests in flight (vLLM batches them), and
  the server is asked for JSON that matches a schema, so answers need no repair.
- **Checkpoints.** Results are written after every paper; a new run continues where the last one
  stopped. Model answers are kept in `raw/`, so a parser fix can be applied without the GPU.
- **Validation flags** (recomputed yield, mass balance, printed mmol vs mass, equivalents,
  temperature, time, solvent volume per mmol, missing workup / chromatography amounts).
  Nothing is dropped. Thresholds are in `config/extraction.yaml`.

```bash
export VLM_API_KEY=...            # same key as the vLLM server
# measure speed (and accuracy, with a hand-checked gold file) before a large run
python scripts/benchmark_extraction.py --pdf a.pdf --pdf b.pdf --concurrency 1,8,32 --classic-pages 5
# extract a folder of PDFs (resumable), then add structures / masses / checks
python scripts/build_dataset.py extract --pdf-dir pdfs/ --out data/run1
python scripts/build_dataset.py enrich --out data/run1
python scripts/build_dataset.py stats --out data/run1
```

On Colab, `notebooks/colab_dataset_extraction.ipynb` starts the server and runs the pipeline in
the same session (no tunnel), writing to Google Drive. An optional `manifest.csv`
(`file,doi,group`) links SI files to their paper so train/test splits can be made by paper.

Output files: `papers.jsonl` (one line per finished paper), `reactions.jsonl`,
`reactions_enriched.jsonl`, `tables.jsonl`, `raw/<paper_id>.jsonl`, `smiles_cache.json`.

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
