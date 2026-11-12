# Green Metrics Studio v1: progress

This file is the hand-over note for the v1 build. It says what is done, what is next and why
each decision was made. Update it whenever a part is finished.

## How to check the work

```bash
python -m pytest -q tests          # full test suite (no network)
```

## Done

- **Regression lock.** `tests/golden/metrics_baseline.json` holds the outputs of the calculators
  before any restructuring (commit d2513eb). `tests/test_regression_lock.py` fails if a formula
  changes.
- **One package.** Code moved from `modules/` to `gms/`:
  - `gms/metrics/` the deterministic calculators (`mass.py`, `ecoscale.py`, `hazard_ratios.py`),
    unchanged, plus `registry.py` (metric keys, labels, thresholds, tolerances from
    `config/metrics.yaml`).
  - `gms/chem/` the shared chemistry helpers: `names.py` (abbreviations and the single
    name-to-structure resolver with one cache), `units.py`, `structure.py`, `conditions.py`,
    `pubchem.py` (fixed-host client), `hazard.py` (GHS hazard profiles and scores).
  - `gms/extraction/` the PDF pipelines (`classic.py` is the former `pdf_extraction.py`).
  - `gms/model.py` the reaction record (role-tagged components) used everywhere.
  - `gms/settings.py` server-side settings from environment variables.
- **Config files.** `config/metrics.yaml`, `config/scoring.yaml`, `config/hazard.yaml`.

## In progress / next (in this order)

1. `gms/chem/amounts.py` (fill MW, moles, masses) and `gms/scoring.py` (`score(reaction)`).
2. Hazard seed file `gms/data/ghs_seed.json` built from PubChem (`scripts/build_hazard_seed.py`).
3. Greenness score and verdict, diagnosis, suggestion engine, no-regression check, break-even.
4. Streamlit UI on the new core; reports; feedback store.
5. Extraction behind server-side settings; security fixes; Docker; CI; documentation.
6. Seed precedents from the Open Reaction Database; benchmarks in `benchmarks/`.

## Decisions and reasons

- **Reagents count as reactants in AE / RME / CE / ERF**, as before v1. Catalysts, bases,
  additives, solvents, workup and purification materials are auxiliaries; water is separate.
- **Hazard data source.** GHS H-codes come from PubChem. The EU harmonised classification is
  used when present; otherwise the ECHA aggregated notifications, keeping statements reported
  by at least 50% of companies (`config/hazard.yaml`).
- **Unknown hazard is not "no hazard".** A profile has a status (classified, not classified,
  no data, unavailable). A replacement with unknown hazard cannot pass the no-regression check.
- **Network in this build session.** A personal cloud environment `new-rg-calculator` allows
  PubChem and Hugging Face (Open Reaction Database mirror). Tests never use the network
  (`tests/conftest.py` blocks PubChem).
- **Docker in this build session.** Docker Hub is rate-limited here; use
  `mirror.gcr.io/library/python:3.12-slim` as the base image for local test builds.

## Open items for the student

- The verdict thresholds and weights in `config/scoring.yaml` are provisional defaults. The
  chemist should review them.
