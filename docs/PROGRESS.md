# Green Metrics Studio v1: progress

Hand-over note for the v1 build: what version 1 includes, what was left out and why, the
decisions that were made, and what to do next. Another person (or session) can continue from here.

## How to check the work

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q tests
```

```bash
python scripts/run_benchmarks.py
```

```bash
docker build -t green-metrics-studio . && docker run -p 7860:7860 green-metrics-studio
```

- The tests use no network (`tests/conftest.py` blocks PubChem; the extraction server is a stub).
- `benchmarks/RESULTS.md` is written by the benchmark script; a test fails if it is out of date.
- CI (`.github/workflows/ci.yml`) runs the tests, the benchmark check and a Docker build with a
  health check on every push.

## What version 1 includes

| Area | State | Where |
|---|---|---|
| One reaction record with role-tagged materials | done | `gms/model.py` |
| One scoring function; metric keys, thresholds, tolerances in config | done | `gms/scoring.py`, `gms/metrics/registry.py`, `config/metrics.yaml` |
| Regression lock on the pre-v1 metric outputs | done, no formula changed | `tests/golden/`, `tests/test_regression_lock.py`, `tests/test_scoring.py` |
| Shared chemistry module, one name resolver, one cache | done | `gms/chem/` |
| Automatic hazard data from GHS codes (PubChem), bundled for 208 compounds, cached | done | `gms/chem/hazard.py`, `gms/data/ghs_seed.json` |
| Automatic EcoScale safety penalties from hazard statements | done | `config/hazard.yaml`, `gms/scoring.py` |
| CHEM21 solvent table (77 solvents) | done | `gms/data/chem21_solvents.csv` |
| Combined verdict with reasons, caps and a sensitivity check | done, weights provisional | `gms/greenness.py`, `config/scoring.yaml` |
| Diagnosis with computed gains | done | `gms/diagnosis.py`, `config/diagnosis.yaml` |
| Alternatives: reaction solvent, workup solvent, eluent, purification method, reagent, catalyst, base, additive, stoichiometry, concentration | done for 3 classes (30 curated rules + 2 built-in rules) | `gms/suggest.py`, `gms/data/substitutions.yaml` |
| No-regression check (same product, hazard, metrics, cost where known) with the comparison table | done | `gms/regression.py` |
| Yield scenarios, slider and break-even yields (PMI, RME, EcoScale, combined score) | done; worked check 40 g / 8 g / 15.625 gives 0.32 | `gms/breakeven.py` |
| Estimates marked on screen and in exports | done | `Component.estimated`, `Score.estimated_metrics` |
| Evidence, precedent counts, confidence level per suggestion | done | `gms/precedents.py`, `gms/data/precedents.json` |
| Precedent data: 110,501 patent reactions of the 3 classes | done | `scripts/build_precedents.py` |
| Reaction class detection; coefficient suggestion from carbon balance | done | `gms/chem/classify.py`, `gms/chem/stoich.py` |
| Manual entry with validation; examples; reload from a JSON report | done | `gms/forms.py`, `ui/pages/reaction.py` |
| PDF extraction behind server-side settings; offline is a normal state; review step with flags | done, tested against a stub | `gms/extraction/service.py` |
| Compare page; report as PDF, CSV, JSON with provenance, assumptions, version | done | `gms/report.py`, `ui/pages/compare.py` |
| Accept / reject / comment stored for analysis; token-protected download | done | `gms/feedback.py`, `ui/pages/admin.py` |
| Security: no endpoint or key in the browser, no user-supplied URLs, XSRF/CORS on, non-root container, upload limits | done | `gms/settings.py`, `.streamlit/config.toml`, `Dockerfile` |
| Docker image; CI | done | `Dockerfile`, `.github/workflows/ci.yml` |
| Documentation | done | `README.md`, `docs/` |
| Benchmarks with sources | done: 13 known alternatives, 9 suggested (8 in the first three), 4 rejected by the check, 0 not generated | `benchmarks/` |

### Verified in the build session

- Full test suite passes (see the last commit message for the count).
- `docker build` of the committed Dockerfile succeeded; the container was driven with a real
  browser twice: without an extraction server (enter, analyse, all pages, PDF download) and with
  a stub extraction server (extract, review, analyse). The key and endpoint did not appear in the page.
- Not verified: the app against a real Qwen server (none was available), and any real deployment.

## Deliberate changes to earlier behaviour

No metric formula changed. These things changed around the formulas:

1. **The EcoScale safety penalty is now derived from GHS hazard statements** (it was ticked by
   hand). The translation table is in `config/hazard.yaml`.
2. **"Not assessed" price** counts 0 points and is listed as an assumption (the old page
   defaulted to "inexpensive", also 0 points, without saying so).
3. **The old pages** (separate mass, EcoScale and comparison pages with Route A / Route B slots)
   are replaced by the reaction record flow. The life-cycle hazard ratio page is kept as a
   reference page.
4. **The classic "one reaction per PDF" mode is no longer in the UI.** The app uses the dataset
   pipeline (roles kept, many reactions per document). `gms/extraction/classic.py` remains for
   the extraction speed benchmark.

## Decisions and reasons

- **Reagents count as reactants in AE / RME / CE / ERF**, as before v1.
- **Hazard source order:** EU harmonised classification, then ECHA notifications reported by at
  least 50% of companies. A source that says "not classified" is respected (otherwise a
  lower-priority source made sodium sulfate "organ-toxic").
- **Unknown hazard is not "no hazard".** A replacement with unknown hazard fails the check.
- **"Hazard does not get worse" is tested on the worst score** (any dimension) of the new
  materials against the replaced ones, of the whole reaction, and on the mass-weighted solvent
  class. A single dimension that rises (flammability when DMF is replaced by ethyl acetate) is
  shown as a trade-off.
- **EcoScale tolerance of 5 points** in the check. The EcoScale gives "highly flammable" the
  same penalty as "toxic", so every swap from dichloromethane to a recommended flammable solvent
  loses 5 points. Without the tolerance all of them would be rejected. The loss is shown.
- **5% tolerance on the mass-intensity metrics** in the check: an equal-volume solvent swap
  changes the mass by the density ratio.
- **A rejected candidate is listed, not hidden**, in a separate section with the reason. It is
  not presented as a suggestion.
- **Solvent precedents count only reactions where the solvent is the complete solvent list**,
  because the patent data sometimes lists extraction solvents as reaction solvents.
- **Patent data tags most reagents as reactants.** The import separates the two coupling
  partners (by functional group) from everything else.
- **No bundled prices** (no free licensed source). The mechanism exists: `config/prices.yaml`.
- **No model in v1.** See "Left out".
- **Replacement materials get role-qualified names** when the name is already used ("ethyl
  acetate (reaction solvent)"), because names identify rows.

## From the list of things the plans might have missed

| Item | Decision |
|---|---|
| Inferring stoichiometric coefficients | In v1 as a suggestion from a carbon balance, never applied silently. Full balancing: later. |
| Products given only as compound numbers ("3a") | Not solved. The review step asks for a name or SMILES. Needs structure recognition from schemes. |
| Price data for the EcoScale | Mechanism in v1 (category chosen by the user; `config/prices.yaml` for the cost check). No data shipped. |
| Upload limits and privacy | In v1: size and page limits, PDFs kept in memory only, privacy text in the app. |
| Error tracking and logs | Logs to standard output in v1. External error tracking (for example Sentry): later. |
| Caching of PubChem lookups | In v1: bundled seed, disk cache, name cache. |
| Accessibility and phone layout | Partly: ratings carry a word and a symbol, not colour alone; no sideways scroll on a phone. No formal audit. |
| Safety disclaimer | In v1: on every analysis page, in the reports, on the methods page. |
| Licences of the data sources | In v1: README table and the methods page. **The code itself has no licence file yet: choose one.** |
| CI on every push | In v1. |

Found during the build and handled: hazard sources that disagree; unknown hazards; duplicate
names after a swap; solvent mixtures as replacements; reagents mislabelled in patent data; the
EcoScale flammability effect; GHS data that understates known hazards (HATU), documented as a
limitation.

## Left out of v1, and why

- **Retrosynthesis.** Optional in the brief, to be done last. Not started: the single-step flow
  needed the time. The architecture note says where it would plug in.
- **A trained model (yield or condition recommendation).** Patent yields are too noisy for an
  honest yield model, and there is no corpus yet. The UI says so. `gms/data/precedent_reactions.jsonl.gz`
  (110,501 records with a patent number each) is ready for a split by patent when a model is tried.
- **PMI percentiles per reaction class.** The data has no workup amounts. The score card shows
  yield and solvent-volume ranges and says why PMI is not ranked.
- **Estimating missing workup amounts from typical volumes.** Only the user's own "estimated"
  flag and the crystallisation assumption exist. A typical-volume table needs the chemist's input.
- **Bundled prices, compound-number resolution, full equation balancing, external error
  tracking, user accounts, rate limiting.** See the table above and `docs/DEPLOYMENT.md`.

## Recommended next steps

1. **Chemist review** of `config/scoring.yaml` (verdict limits and weights), `config/hazard.yaml`
   (the 50% rule, "high hazard" limits), the tolerances in `config/metrics.yaml`, and the 30 rules
   in `gms/data/substitutions.yaml` (three references are marked "not verified": Dunetz 2016,
   Sabatini 2019, Tang 2005; the curator could not open publisher full texts).
2. **Decide the four rejected benchmark alternatives** (`benchmarks/RESULTS.md`): T3P for HATU,
   NaBH(OAc)3 and picoline borane for NaBH3CN, Pd(OAc)2/SPhos for Pd(PPh3)4. Either the check
   is right to be strict, or a rule needs a documented exception (for example a hazard override
   for HATU, whose GHS data understates it).
3. **Test against the real Qwen server**, then run the gold-set accuracy benchmark
   (`scripts/benchmark_extraction.py`).
4. **Deploy** (your decision; `docs/DEPLOYMENT.md`) and collect feedback. Analyse
   `feedback.csv` by rule id: which rules chemists accept and reject.
5. **Run the corpus** when it arrives (`scripts/build_dataset.py`), then extend
   `scripts/build_precedents.py` to merge your records with the patent data; your data has the
   workup amounts that PMI percentiles need.
6. Add a licence file for the code.

## Notes for the next build session

- Cloud environment `new-rg-calculator` allows PubChem, Hugging Face (ORD mirror) and
  `deb.debian.org`. Docker Hub is rate-limited there: pull `mirror.gcr.io/library/python:3.13-slim`.
  To build the image in that sandbox, use a base image that trusts the sandbox proxy
  certificate and pass `--network host --build-arg HTTPS_PROXY=$HTTPS_PROXY`. On a normal
  machine `docker build .` is enough.
- Raw ORD files are not in the repository (about 800 MB). Re-download with
  `python scripts/build_precedents.py download`.
