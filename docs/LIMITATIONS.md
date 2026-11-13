# Known limitations

Written for chemists who use the tool. The same list is in the app on the page
**Methods and limitations**.

## What the tool does not do

- **It does not predict yields.** An alternative is scored at the original yield. That is not
  a claim that the yields are equal. Use the yield slider and the break-even yield to judge the
  risk. The yields shown for precedents are reported yields of other substrates.
- **It does not test chemistry.** A suggestion says that a change has worked in a cited study or
  appears in patent procedures. It can fail for your substrates: solubility, selectivity, rate,
  epimerisation and side reactions are not modelled.
- **It does not replace safety work.** Read the safety data sheet. Do your own risk assessment.
- **It handles one reaction step.** Multi-step routes and retrosynthesis are not in this version.

## Coverage

- Alternatives for the reaction solvent, reagents, catalysts and bases exist for amide coupling,
  Suzuki-Miyaura coupling and reductive amination only. The table has 30 rules.
- Workup and purification alternatives (extraction solvent, chromatography eluent,
  crystallisation in place of chromatography) are offered for every reaction.
- The reaction class is recognised from the structures of the reactants and the product. If the
  names are not recognised, enter a SMILES or choose the class yourself.

## Data quality

- **Hazard data can be missing or disputed.** GHS statements come from PubChem. Some common
  compounds have no GHS record there (for example sodium hydride, potassium tert-butoxide,
  HBTU). They show as "no GHS data", which means unknown. An alternative whose hazard is
  unknown cannot pass the check.
- **GHS statements do not capture every hazard.** HATU carries only "may cause an allergic skin
  reaction" in the company notifications, although it is a known sensitiser with a reported
  explosion risk. So the check rejects T3P (corrosive) as a replacement for HATU on hazard
  grounds. The reason is shown. You may judge differently.
- **Company notifications to ECHA disagree with each other.** The tool keeps a statement that
  at least half of the reports contain (`config/hazard.yaml`).
- **Amounts in papers are incomplete.** Extraction solvent, silica and eluent amounts are
  rarely printed. The tool does not invent them: the PMI of such a procedure is a lower bound
  and the score card lists what is not counted.
- **Precedent data comes from US patents** (Open Reaction Database). Roles and amounts were
  extracted by text mining and contain errors. Extraction solvents are sometimes listed as
  reaction solvents, so the tool counts a solvent as a precedent only where it is the complete
  solvent list. There is no workup data, so no PMI ranking against similar reactions is shown.

## Judgements built into the tool

- **The verdict is provisional.** The limits and weights in `config/scoring.yaml` are starting
  values that the project chemist has not yet reviewed. The score card shows how robust the
  verdict is to the weights.
- **The colour thresholds of the mass metrics describe an efficient process.** A laboratory
  procedure with extraction and chromatography is usually far from them.
- **The EcoScale counts "highly flammable" like "toxic".** A swap from dichloromethane to ethyl
  acetate loses 5 EcoScale points. The check tolerates a loss of 5 points and shows it as a
  trade-off (`config/metrics.yaml`).
- **The check is strict.** It rejects a literature-backed swap if atom economy or another metric
  gets worse beyond its tolerance. Example: sodium triacetoxyborohydride in place of sodium
  cyanoborohydride is rejected because the heavier reagent lowers the atom economy.
  `benchmarks/RESULTS.md` lists these cases.
- **Amounts in a suggestion are assumptions**: the same volume of the new solvent, or the
  equivalents of the cited procedure. They are marked as estimates.
- **Crystallisation in place of chromatography** assumes 10 mL of ethanol per gram of product
  as the crystallisation solvent. It is a prompt to consider, with low confidence.

## Not available in this version

- **Prices.** There is no free, licensed price source. The EcoScale price penalty is 0 unless
  you choose a category, and the cost check runs only when prices are added to
  `config/prices.yaml`.
- **A trained model.** There is no yield or condition model. The patent data is too noisy for a
  yield model that would be honest to show.
- **PMI percentiles within a reaction class.** They need workup amounts, which the available
  data does not have.
- **Products given only as a compound number ("3a").** PDF extraction keeps the label. You
  enter the name or the SMILES in the review step.
- **Full equation balancing.** Coefficients are 1 unless you set them. The app proposes
  coefficients from a carbon balance and warns if the product has more carbon than the reactants.

## PDF extraction

- It needs the project's own model server. When the server is offline the app says so and
  everything else keeps working.
- The model can misread amounts and roles. Every extracted reaction goes through the review
  form before it is analysed. Check it against the paper.
