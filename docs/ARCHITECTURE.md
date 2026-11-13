# Architecture

One rule shapes the code: **a reaction has one shape, and one function scores it.** Everything
else either builds a reaction record, or reads the result of `score(reaction)`.

```
 manual entry (gms/forms.py)  ─┐
 examples (gms/examples.py)   ─┼─►  Reaction  ──►  amounts.complete  ──►  scoring.score  ──►  Score
 PDF extraction (service.py)  ─┘   (gms/model.py)   (structures, masses)    (metrics, EcoScale,     │
                                                                              hazards, verdict)      │
            ┌────────────────────────────────────────────────────────────────────────────────────────┤
            ▼                                   ▼                                    ▼
     diagnosis.diagnose                  suggest.suggest                      report / feedback
     (edit a copy, score again)   (edit a copy, score again, check,           (JSON, CSV, PDF; SQLite)
                                   break-even)
```

## Folders

| Path | What it is |
|---|---|
| `app.py`, `ui/` | The Streamlit interface. Pages only display results; they hold no chemistry and no thresholds. |
| `gms/model.py` | `Reaction` and `Component`: the single reaction record with role-tagged materials. |
| `gms/metrics/` | The deterministic calculators (`mass.py`, `ecoscale.py`, `hazard_ratios.py`) and the metric registry. |
| `gms/chem/` | Shared chemistry: names and the single name-to-structure resolver, units and densities, structures, conditions, PubChem client, hazard profiles, CHEM21 solvents, reaction class, coefficient inference, amount completion. |
| `gms/scoring.py`, `gms/greenness.py` | `score(reaction)` and the combined verdict. |
| `gms/edits.py` | Small explicit changes to a reaction (replace, scale, remove, add, change a step). |
| `gms/diagnosis.py` | Findings with computed gains. |
| `gms/suggest.py`, `gms/regression.py`, `gms/breakeven.py`, `gms/precedents.py` | Candidates, the no-regression check, break-even yields, precedent counts. |
| `gms/report.py`, `gms/feedback.py` | Exports and the feedback store. |
| `gms/extraction/` | PDF pipelines and `service.py`, the server-side entry point the app uses. |
| `gms/data/` | Packaged reference data: hazard seed, CHEM21 table, substitution rules, precedents, examples. |
| `config/` | Every threshold, weight and tolerance. |
| `benchmarks/` | Cases with known greener alternatives, and the written results. |
| `scripts/` | Data builders, dataset extraction, benchmark runner, feedback export. |
| `tests/` | The test suite (no network). |

## Why it is shaped this way

**One reaction record.** Before version 1 a reaction changed shape three times on the way to
the calculator and lost its roles. Now manual entry, examples and extraction all produce a
`Reaction`. A material keeps its role (reaction solvent, workup, purification...) to the end,
so the share of each role in the PMI can be computed, and a rule can target "the reaction
solvent" and not "a material called DMF".

**The formulas are untouched and locked.** `gms/metrics/mass.py` and `ecoscale.py` are the
calculators from before version 1. `scoring.mass_metrics` calls them and renames the results to
stable keys (`pmi`, `ae`...). `tests/golden/metrics_baseline.json` was recorded before the
restructuring; `tests/test_regression_lock.py` and `tests/test_scoring.py` fail if any value
changes.

**Metric registry.** `config/metrics.yaml` defines each metric once: key, label, unit, which
direction is better, colour thresholds, and the tolerance used by the no-regression check. The
UI, the reports, the check and the diagnosis all read it. Display labels are looked up, never
used as dictionary keys.

**Edits make every gain a computed number.** The diagnosis ("what if this were fixed?") and the
suggestions ("replace DMF by ethyl acetate") both apply a list of edits to a copy of the
reaction and call `score` again. No gain is estimated by a separate formula, so a gain can never
disagree with the calculator.

**The no-regression check is a function with a table as its result.** `regression.check`
returns every comparison it made (product, hazard, each metric, cost). The UI shows that table.
A candidate that fails keeps its reasons and is listed separately; it is never shown as a
suggestion.

**No single predicted yield.** A candidate keeps the product mass of the original (the original
yield). `breakeven` computes, per metric, the lowest yield at which the candidate is still as
good as the original, from closed formulas for PMI, RME and the EcoScale and by bisection for
the combined score. `scoring.with_yield` produces the scenario for the slider.

**Estimates are a property of a material.** `Component.estimated` and `Component.note` say that
a mass is an assumption and where it comes from. `score` lists which metrics depend on
estimated masses. Unit conversions (volume x density, moles x MW) are deterministic and are not
estimates; the note still says how the mass was obtained.

**Hazard data has a status.** A profile is `classified`, `not_classified`, `no_data` or
`unavailable`. Unknown is never read as safe: an alternative with unknown hazard fails the
check. Lookups go bundled seed → local cache → PubChem, so common compounds need no network and
tests need none at all.

**Evidence is data.** `gms/data/substitutions.yaml` holds each rule with its problem statement,
scope, caveats, references (with a "verified" flag) and evidence level. Confidence is derived
from the evidence level, the verified flag and the precedent count (`config/suggestions.yaml`).

**Server-side settings.** `gms/settings.py` reads environment variables. The extraction
endpoint and key exist only there, so a visitor cannot make the server call another address,
and the key is never sent to a browser. `service.status` treats an unreachable server as a
normal state.

**The interface is thin.** `ui/state.py` computes the analysis once per reaction and caches it
in the session. Each page reads that result. Logic that needs a test lives in `gms/` and is
tested without the UI; `tests/test_app_ui.py` then checks the flow end to end.

## What is deliberately simple

- Two extraction pipelines exist. The app uses the dataset pipeline (`pipeline.py`, role-tagged,
  many reactions per document). `classic.py` (every page as an image, one merged reaction) is
  kept for `scripts/benchmark_extraction.py`, which compares their speed.
- The feedback store is one SQLite file. It is enough for a feedback release with one container.
- There is no model in the app. `gms/precedents.py` only counts and summarises.
- Retrosynthesis is not included. If it is added, it should produce `Reaction` records and go
  through `score` and `regression.check` like every other candidate.
