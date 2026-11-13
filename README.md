# Green Metrics Studio

Green Metrics Studio helps a chemist answer three questions about one reaction, from a paper
or from a lab notebook:

1. **Is this reaction green?** Every standard metric, a combined verdict with its reasons, and
   hazard data for each material.
2. **If not, why not?** Which material or step causes the mass, the hazard and the penalty
   points, and how much fixing each one would gain.
3. **What greener alternative would fix it, and what would it cost elsewhere?** Alternatives
   with their evidence, a check that nothing else gets worse, and the yield the alternative must
   reach to stay ahead.

It is a decision-support tool for trained chemists. It does not replace a risk assessment, the
safety data sheet or a laboratory test. See [Known limitations](docs/LIMITATIONS.md).

## Quick start

```bash
docker build -t green-metrics-studio .
```

```bash
docker run -p 7860:7860 -v gms-data:/data green-metrics-studio
```

Open http://localhost:7860. Without Docker (Python 3.13 and a Java runtime are needed):

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

The app is fully usable without the PDF extraction server. To deploy it, and to connect the
extraction model, read the [deployment guide](docs/DEPLOYMENT.md).

## How to use it

The pages follow the order of the work.

1. **Reaction.** Enter every material with its amount and its role (reactant, reagent, catalyst,
   base, additive, reaction solvent, workup, purification, water, product), the conditions and
   the workup steps. You can also load an example, load a reaction from a report that you
   exported earlier, or extract a reaction from a PDF if the extraction server is online. Select
   **Analyse**. The page then shows what the app understood: structures, masses, the reaction
   class, and warnings about amounts that do not fit together.
2. **Score card.** The verdict (green, borderline, not green) with its reasons, all metrics, the
   EcoScale breakdown, the hazard statements of each material, and a comparison with similar
   reactions from patents.
3. **Diagnosis.** Where the input mass goes, and a ranked list of findings. Each finding shows
   the gain of an idealised fix, computed by running the calculator on an edited copy of the
   reaction.
4. **Greener alternatives.** Each alternative shows why the change is proposed, its references
   and precedents, a confidence level, the table of the no-regression check, the break-even
   yields and a yield slider. Accept or reject it and leave a comment. Candidates that failed
   the check are listed separately with the reason.
5. **Compare and export.** The original and chosen alternatives side by side, and the report as
   PDF, CSV and JSON.

### How to read the results

- **Metrics are computed from the published formulas**, never by a model: atom economy, reaction
  mass efficiency, process mass intensity, mass intensity, E-factor, carbon efficiency, mass
  productivity, effective mass yield, excess reactant factor, yield and the EcoScale
  (Tamargo et al. 2024; Van Aken et al. 2006).
- **The yield of an alternative is not predicted.** Changes are computed at the original yield,
  which is not a claim that the yields are equal. The break-even yield is the lowest yield at
  which the alternative is still at least as good as the original.
- **"estimate"** marks every value that depends on an assumed amount, on screen and in the
  exports.
- **"no GHS data"** means the hazard is unknown, not that the material is safe.
- **The verdict uses provisional weights** (`config/scoring.yaml`). The score card shows how
  often the verdict stays the same when the weights change.

### Which reactions are supported

The score card, the diagnosis and the workup and purification alternatives work for any single
reaction step. Alternatives for the reaction solvent, reagents, catalysts and bases exist for
three reaction classes: **amide coupling, Suzuki-Miyaura coupling and reductive amination**.

## Your feedback

Each accept or reject decision and each comment is stored on the server (reaction record,
suggestion, decision, comment, random session number; no personal data). The administrator can
download the data on the **Feedback data** page or with `python scripts/export_feedback.py`.

## For developers

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q tests
```

```bash
python scripts/run_benchmarks.py
```

- The tests use no network. PubChem and the extraction server are replaced by stubs.
- `benchmarks/RESULTS.md` lists which known greener alternatives the tool finds.
- [Architecture](docs/ARCHITECTURE.md) explains the modules.
  [Progress](docs/PROGRESS.md) lists what version 1 includes, what was left out and what comes next.
- Thresholds, weights and tolerances are in `config/*.yaml`, not in code.

### Building a dataset from your own PDFs

The extraction pipeline can read a folder of papers and supporting information into reaction
records (many reactions per paper, general procedures, optimisation tables, validation flags,
resumable runs):

```bash
python scripts/build_dataset.py extract --pdf-dir pdfs/ --out data/run1
```

```bash
python scripts/build_dataset.py enrich --out data/run1
```

`notebooks/colab_dataset_extraction.ipynb` runs the model server and the pipeline in one Colab
session. `scripts/benchmark_extraction.py` measures speed and, with a hand-checked gold file
(`benchmarks/gold_template.json`), accuracy.

## Data sources and licences

| Data | Source | Licence |
|---|---|---|
| Metric formulas | Tamargo et al., *Metrics of Green Chemistry and Sustainability*, 2024; Van Aken et al., *Beilstein J. Org. Chem.* 2006, 2, 3 | formulas |
| Hazard statements (`gms/data/ghs_seed.json`, and live lookups) | PubChem, GHS Classification (EU CLP Annex VI, ECHA C&L Inventory) | PubChem data is free to use; cite the primary sources |
| Solvent ranking (`gms/data/chem21_solvents.csv`) | CHEM21 selection guide, Prat et al., *Green Chem.* 2016, 18, 288 | CC BY 3.0 |
| Substitution rules (`gms/data/substitutions.yaml`) | curated from the literature; each rule lists its references | this project |
| Precedents (`gms/data/precedents.json`, `precedent_reactions.jsonl.gz`) | Open Reaction Database, US patent reactions (Lowe) | CC BY-SA 4.0 (source data CC0) |
| Name to structure | OPSIN and PubChem | MIT; PubChem terms |

The files derived from the Open Reaction Database are shared under CC BY-SA 4.0.
