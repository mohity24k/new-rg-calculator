# Build Green Metrics Studio v1: a production-ready feedback release

## Why this matters

I am a student building Green Metrics Studio for a supervised research project. A chemist on the team checks data and reviews suggestions. The goal is a tool that chemists anywhere can use to answer three questions about a reaction from a paper or their own notebook:

1. Is this reaction green?
2. If not, why not: which component or step causes the problem?
3. What greener alternative would fix it, and what would it cost elsewhere?

I need a first production-ready version that real chemists can use, so I can collect their feedback. It must be reliable and honest, even where it is not yet complete. A suggestion that looks confident but is chemically wrong will cost the tool its credibility with chemists. Suggestions should therefore be cautious, explained, and backed by evidence.

## Where things stand

The repository is `mohity24k/new-rg-calculator`. Read it before you plan. It is a Streamlit app ("Green Metrics Studio") with these parts:

- **Deterministic calculators.** `modules/mass_metrics.py` covers AE, RME, PMI, MI, E-factor, CE, MP, EMY, ERF and yield. `modules/eco_scale.py` covers the EcoScale. `modules/hazard_metrics.py` holds ratio formulas. The formulas follow Tamargo et al. 2024 (*Metrics of Green Chemistry and Sustainability*) and are tested against the chapter's worked examples.
- **Classic PDF extraction.** `modules/pdf_extraction.py` and `modules/autofill_page.py` send every page as an image to a vision-language model (Qwen2.5-VL-7B on an OpenAI-compatible vLLM server). Every page is merged into one reaction.
- **Dataset extraction pipeline.** `modules/extraction/` was added in PR #3 (branch `claude/great-cerf-wh9q63`). It has:
  - a page filter and text-first requests
  - many reactions per paper, general procedures, and optimisation tables
  - role-tagged components (reactant, reagent, catalyst, base, additive, reaction_solvent, workup, purification, water, product)
  - parallel requests with structured JSON output
  - resumable runs and validation flags
  - an enrichment step, `scripts/build_dataset.py`, `scripts/benchmark_extraction.py`, and a Colab notebook

  It has not yet been run on a real corpus.
- **Planning documents.** `docs/Green_Metrics_Studio_Proposal.docx` is a 13-week plan approved in outline by my supervisor. It covers: core rebuild, automatic hazard data, greenness score and verdict, extraction quality, dataset, reaction-class benchmarks, diagnosis engine, solvent/workup suggestions, reagent/catalyst/stoichiometry suggestions, a no-regression check with yield scenarios, optional retrosynthesis, a redesign page with reports, and validation/launch. Treat it as the map of what the finished tool should do. You are not bound to its week order or to its details.

A design review of the code found these problems:

- The shared chemistry helpers live as private functions inside `pdf_extraction.py`. Other modules reach into them, and there are two separate name-to-structure resolvers with two caches.
- A reaction changes shape three times before it is scored: review table, then app tables, then `mm.Reactant`. Roles are lost along the way.
- Metric results are keyed by their display labels, for example `"Atom Economy (AE, %)"`.
- Colour thresholds are scattered through `app.py`, and `app.py` holds logic that can only be tested through the UI.
- `AuxiliaryMaterial` has no role, so the solvent share of PMI cannot be computed.
- The hazard page is entirely manual.
- No combined greenness score exists.

Security problems for a public deployment:

- The extraction API key is sent to the browser in a password field.
- Visitors can type any server URL, which allows server-side request forgery.
- The devcontainer turns off Streamlit's CORS/XSRF protection.

## Decisions already made

- **Keep Streamlit** for this release. Restructure the code behind it freely. The UI should feel like a professional tool, not a demo.
- **PDF extraction uses my self-hosted Qwen2.5-VL** through an OpenAI-compatible endpoint. It runs on a Colab A100 or later a GPU server, so it will often be offline. Two requirements follow:
  - The endpoint and key come from server-side configuration only. Users never see them and cannot change them.
  - The app must be fully useful without extraction. Manual entry, every calculation, the diagnosis and the suggestions must all work. Extraction is offered only when the server answers. Its being offline is a normal state with a clear message, not an error.
- **Deployment is Docker, host-agnostic.** Provide a Dockerfile that runs on Hugging Face Spaces, Render or any VM, with docs for each. Do not deploy anything yourself.
- **There is no PDF corpus yet.** Build everything that needs data so it works now with whatever data exists. Seed it from free sources where the licence allows: USPTO reactions (CC0), the Open Reaction Database (CC-BY-SA) and published solvent guides such as CHEM21 (CC-BY). Make the dataset pipeline ready for my corpus when it arrives. Features that need more data than exists, such as a trained model or percentile benchmarks, must say so plainly in the UI rather than show weak numbers as if they were strong.
- **Retrosynthesis is optional.** Build it only behind an adapter, so the app runs without it. Do it last, if the rest is solid.
- **First reaction classes:** amide coupling, Suzuki–Miyaura coupling and reductive amination.

## Principles that must hold

These come from how chemists will judge the tool. Keep them unless you find a strong reason not to, and tell me if you do.

- **Metrics stay deterministic.** Scores come from the literature formulas, never from a model. Do not change an existing formula without a test that shows why. Lock today's outputs with regression tests before refactoring.
- **"Greener without affecting other factors" must be a check, not a claim.** Show a suggestion only if all of these hold:
  - it makes the same product
  - hazard does not get worse
  - no other metric gets worse beyond a configurable tolerance
  - cost does not get worse, where cost is known

  Show the user what the check compared.
- **Never present a single predicted yield for an untested change.** Show the score change at the original yield, labelled as not a claim that yields are equal. Add a yield slider and a break-even yield: the lowest yield at which the candidate is still greener. A worked check: candidate inputs 40 g, maximum product 8 g and original PMI 15.625 give a break-even yield of 0.32. RME and EcoScale need their own break-even functions.
- **Estimates are visibly estimates.** Any number from an assumed mass, a typical volume, a model or a precedent is marked as estimated in the UI and in exports. It is never mixed silently with values computed from experimental data.
- **Suggestions carry evidence.** Each one says where it came from: the rule or table that produced it, the precedents in the data, and how many. It also carries a confidence level. The chemist accepts or rejects it, and that choice is recorded. This is the feedback I need.
- **ML is a helper, never the judge.** Model inputs contain chemistry features only: no metric values, masses or yields. Train/test splits are by paper (DOI) or reaction class, never random. Every prediction has an uncertainty range and a "far from the training data" flag.
- **Thresholds, weights and tolerances live in config files**, not in code.

## What the finished v1 should do

The 13-week proposal is the map. In short, a chemist should be able to:

1. enter a reaction by hand or extract it from a PDF, with a review step that shows confidence and validation flags
2. see every metric, a combined greenness verdict with its reasons, and automatic hazard data from GHS codes
3. see the diagnosis: which components or steps drive PMI, hazard and penalties, each with the exact score gain of fixing it, computed by re-running the calculator on an edited copy of the reaction
4. get greener alternatives for solvents, workup, purification, reagents and catalysts, and stoichiometry. Each alternative must:
   - be filtered by reaction class and backed by precedent
   - pass the no-regression check
   - show its yield scenarios and break-even yield
5. compare the original with candidates side by side, change the yield, and export a report (PDF, CSV and JSON) that includes provenance, assumptions and the app version
6. accept or reject suggestions and leave comments, stored so I can analyse them

I also expect you to find what these plans missed. Some candidates I am aware of:

- inferring stoichiometric coefficients instead of assuming 1:1
- products identified only by compound numbers such as "3a"
- price data for the EcoScale
- upload limits and privacy (do not keep uploaded PDFs by default)
- error tracking and logs
- caching of PubChem lookups
- accessibility and phone layout
- a safety disclaimer
- licences of the data sources
- CI running the tests on every push

Decide which belong in v1, and list the rest with reasons.

## Ownership and approvals

This codebase is yours. What it's for and how it's built are your call, and you'll keep building on it. Restructure it where the design review or your own reading says it should be restructured. Keep the existing tests passing, or replace them with better tests at the new interfaces.

Approved without asking:

- Create and switch branches. Base your work on the latest `main` together with the PR #3 branch `claude/great-cerf-wh9q63`, merging if PR #3 is not yet merged.
- Commit often and push to your working branch.
- Install open-source Python packages and pin them.
- Download the free datasets named above.
- Run tests, the app and Docker builds locally.

Ask me first:

- deploying anywhere
- anything that costs money, or calling a paid API
- deleting data that is not yours
- opening a pull request

The tests must not call the network. Use stubs for PubChem and the extraction server.

## How to work

- Plan briefly first, then build. Before going wide, build the main flow end to end and make it work on the three reaction classes: enter, score, diagnose, suggest, check, compare, export, feedback. Then deepen each part.
- Keep a short progress file, `docs/PROGRESS.md`. Record what is done, what is next and the decisions you made with their reasons. Write it so that you, or another session, can continue from it if this run ends early.
- Establish a way to check your own work as you build, and run it regularly:
  - the full test suite
  - a smoke run of the app
  - a small set of benchmark reactions where the greener alternative is known

  Put each benchmark case in `benchmarks/` with its source. Use a fresh-context subagent to review major pieces against this brief. Fresh reviewers catch what self-review misses.
- Use subagents for independent work that runs well in parallel, for example curating a substitution table while you build the UI. Keep the integration with yourself.
- Do not add features nobody needs, layers of abstraction for hypothetical futures, or validation for cases that cannot happen. Validate at the system's edges: user input, uploaded files, the extraction server and PubChem.

## Finishing work

Please avoid stopping while you still owe work the user asked for. Be persistent and do not stop unless needed. While status notes are welcome, as is clarifying with the user on open decisions, carry on with work that does not depend on the user's answer without unnecessary stops. If you notice yourself inviting the user to redirect you or offering to wait, instead proceed on the next part of the task. You may meet ordinary obstacles like errors, timeouts, locked files, empty results or failing tools. Diagnose them first, and when they are not real blockers, work through them with the access you have (wait and retry, fix the request, use another tool or source) rather than stopping unnecessarily. If someone clearly decided something is a hard blocker, such as a file marked not to be touched, access that was intentionally withheld, or a safety guardrail, leave it alone, say plainly what you found, and look for another safe way to finish the task. Stopping before the task is complete is very rarely merited - as in when the task can't move forward without user input, or where the blocker is deliberate and should not be worked past. This does not override the need for confirmation on risky or destructive actions.

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly. Report outcomes faithfully: if tests fail, say so with the output; if a step was skipped, say that; when something is done and verified, state it plainly without hedging.

## Definition of done for v1

- `docker build` and `docker run` start the app. It works with the extraction server absent and with it present (tested against a stub).
- All tests pass, and CI runs them on every push. Regression tests prove the existing metric outputs are unchanged, or explain each deliberate change.
- The main flow works end to end for the three reaction classes, and the benchmark results are written down: which known greener alternatives the tool found, and which it missed.
- The security problems listed above are fixed.
- The documentation covers:
  - a README for users
  - a deployment guide for Hugging Face Spaces, Render and a VM, including how to point the app at my Qwen server
  - a short architecture note explaining the main modules and why they are shaped that way
  - a "known limitations" section that a chemist can understand
- `docs/PROGRESS.md` lists what v1 includes, what was left out and why, and what you recommend next.

## Communicating clearly

Write for an intelligent but low-context reader. The person reading has not seen your tool calls, your reasoning or the files you opened, and may be switching in from other work. Messages should generally be self-contained: the reader should understand what you mean without having to dig for other context. Put the key points in the first few sentences. Practice progressive disclosure in your sentence order, and do not use undefined references and ideas in line 1 that won't make sense until the reader reaches line 12. Use plain words, and write in clear, understandable full sentences. Do not use a term or abbreviation that you made up during the work - say what the thing is in a way any reader would understand. Write mostly in the style of ASD-STE100 Simplified Technical English: short sentences, one idea per sentence, active voice, and consistently using the same word for the same thing. Use a paragraph of flowing prose where ideas connect, and keep bullets for lists of separate items. Leave out unnecessary details unless the reader asks and practice concision, but do ensure that your messages have the key context. Aim to make your user-facing text as human-readable and clear as possible.

If something in this brief blocks you and only I can answer it, ask at the start, in one message. Then carry on with everything that does not depend on the answer.
