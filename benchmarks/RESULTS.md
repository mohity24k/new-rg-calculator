# Benchmark results

Produced by `python scripts/run_benchmarks.py` with Green Metrics Studio 1.0.0. Do not edit by hand.

Each case is a reaction for which a greener alternative is known from the literature. The table says whether the tool finds that alternative. Cases and sources: `benchmarks/cases/`.

**Summary:** 13 known greener alternatives. The tool suggests 9 (8 of them among its first three suggestions). It generates 4 more but its own no-regression check rejects them. It does not generate 0.

A rejection is not always a miss. The no-regression check is strict by design: it rejects a literature-backed swap when the GHS hazard data or a metric gets worse. The reason is given for each one.

## Amide coupling with EDC hydrochloride and HOBt in dichloromethane

Case file: `benchmarks/cases/amide-edc-hobt-dcm.yaml`

Original reaction: verdict Not green, PMI 16 (stated amounts only), EcoScale 58.4.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Ethyl acetate, 2-MeTHF or dimethyl carbonate in place of dichloromethane as the reaction solvent | D. S. MacMillan, J. Murray, H. F. Sneddon, C. Jamieson and A. J. B. Watson, Green Chem., 2013, 15, 596-600, doi:10.1039/C2GC36900A | **suggested** (number 2 of 4, confidence high) | ethyl acetate in place of methylene chloride |
| Oxyma in place of HOBt (explosion risk of HOBt) | R. Subiros-Funosas, R. Prohens, R. Barbas, A. El-Faham and F. Albericio, Chem. Eur. J., 2009, 15, 9394-9403, doi:10.1002/chem.200900614 | **suggested** (number 1 of 4, confidence moderate) | ethyl cyano(hydroxyimino)acetate (Oxyma) in place of 1-hydroxybenzotriazole |

## Amide coupling with HATU in N,N-dimethylformamide

Case file: `benchmarks/cases/amide-hatu-dmf.yaml`

Original reaction: verdict Not green, PMI 14.2 (stated amounts only), EcoScale 55.3. No hazard data for: 4-(3-methylbutanoyl)benzoic acid.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Ethyl acetate, 2-MeTHF or dimethyl carbonate in place of DMF as the reaction solvent | D. S. MacMillan, J. Murray, H. F. Sneddon, C. Jamieson and A. J. B. Watson, Green Chem., 2013, 15, 596-600, doi:10.1039/C2GC36900A | **suggested** (number 1 of 3, confidence high) | ethyl acetate in place of N,N-dimethylformamide |
| T3P with pyridine in place of HATU | J. R. Dunetz, Y. Xiang, A. Baldwin and J. Ringling, Org. Lett., 2011, 13, 5048-5051, doi:10.1021/ol201875q | generated, **rejected by the check** | Worst hazard score: new materials vs the materials they replace: HATU -> propanephosphonic anhydride (T3P, supplied as a 50 wt% solution), pyridine; High-hazard mass intensity: worse by 2.2 kg/kg (tolerance 0.494) |

## Ibuprofen: Boots route (6 steps) and BHC route (3 steps), atom economy

Case file: `benchmarks/cases/ibuprofen-routes.yaml`

| Check | Computed | Published | Result |
|---|---|---|---|
| Boots route: atom economy | 40.1% | 40% | matches |
| BHC route: atom economy | 77.4% | 77% | matches |

## Reductive amination with sodium cyanoborohydride in methanol

Case file: `benchmarks/cases/redam-nabh3cn-methanol.yaml`

Original reaction: verdict Borderline, PMI 18.2 (stated amounts only), EcoScale 47.8.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Sodium triacetoxyborohydride in place of sodium cyanoborohydride (no cyanide by-products) | A. F. Abdel-Magid, K. G. Carson, B. D. Harris, C. A. Maryanoff and R. D. Shah, J. Org. Chem., 1996, 61, 3849-3862, doi:10.1021/jo960057x | generated, **rejected by the check** | Atom economy: worse by 19.3 % (tolerance 0.5); Reaction mass efficiency: worse by 20.1 % (tolerance 1.15); Carbon efficiency: worse by 24.7 % (tolerance 1.54); Excess reactant factor: worse by 0.123  (tolerance 0.01); Process mass intensity: worse by 0.925 kg/kg (tolerance 0.912); Mass intensity (without water): worse by 0.925 kg/kg (tolerance 0.912); E-factor: worse by 0.925 kg/kg (tolerance 0.862); High-hazard mass intensity: worse by 0.925 kg/kg (tolerance 0.841) |
| 2-Picoline borane in place of sodium cyanoborohydride | S. Sato, T. Sakamoto, E. Miyazawa and Y. Kikugawa, Tetrahedron, 2004, 60, 7899-7906, doi:10.1016/j.tet.2004.06.045 | generated, **rejected by the check** | Atom economy: worse by 7.06 % (tolerance 0.5); Reaction mass efficiency: worse by 4.09 % (tolerance 1.15); Carbon efficiency: worse by 18.5 % (tolerance 1.54) |

## Reductive amination with sodium triacetoxyborohydride in 1,2-dichloroethane

Case file: `benchmarks/cases/redam-stab-dce.yaml`

Original reaction: verdict Not green, PMI 15 (stated amounts only), EcoScale 62.4. No hazard data for: tert-butyl 3-ethoxy-4-oxopiperidine-1-carboxylate.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Ethyl acetate in place of 1,2-dichloroethane as the reaction solvent | F. I. McGonagle, D. S. MacMillan, J. Murray, H. F. Sneddon, C. Jamieson and A. J. B. Watson, Green Chem., 2013, 15, 1159-1165, doi:10.1039/C3GC40359A | **suggested** (number 1 of 3, confidence high) | ethyl acetate in place of 1,2-dichloroethane |

## Reductive amination with sodium triacetoxyborohydride in dichloromethane

Case file: `benchmarks/cases/redam-stab-dcm.yaml`

Original reaction: verdict Not green, PMI 26.1 (stated amounts only), EcoScale 79.3.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Ethyl acetate in place of dichloromethane as the reaction solvent | F. I. McGonagle, D. S. MacMillan, J. Murray, H. F. Sneddon, C. Jamieson and A. J. B. Watson, Green Chem., 2013, 15, 1159-1165, doi:10.1039/C3GC40359A | **suggested** (number 1 of 2, confidence high) | ethyl acetate in place of dichloromethane |

## Suzuki-Miyaura coupling with Pd(PPh3)4 in 1,4-dioxane / water (microwave)

Case file: `benchmarks/cases/suzuki-pd-pph3-4-dioxane.yaml`

Original reaction: verdict Borderline, PMI 29.5 (stated amounts only), EcoScale 61. No hazard data for: 3-[(4-Boc-piperazin-1-yl)methyl]phenylboronic acid.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Isopropanol/water or 2-MeTHF/water in place of 1,4-dioxane | J. Sherwood, Beilstein J. Org. Chem., 2020, 16, 1001-1005, doi:10.3762/bjoc.16.89 | **suggested** (number 1 of 4, confidence high) | isopropanol/water (3:1 v/v) in place of 1,4-dioxane |
| Pd(OAc)2 with SPhos at lower loading in place of Pd(PPh3)4 | T. E. Barder, S. D. Walker, J. R. Martinelli and S. L. Buchwald, J. Am. Chem. Soc., 2005, 127, 4685-4696, doi:10.1021/ja042491j | generated, **rejected by the check** | Worst hazard score: new materials vs the materials they replace: Pd(PPh3)4 -> palladium(II) acetate with SPhos ligand, SPhos (2-dicyclohexylphosphino-2',6'-dimethoxybiphenyl) |

## Suzuki-Miyaura coupling with Pd(PPh3)4 in 1,2-dimethoxyethane / water

Case file: `benchmarks/cases/suzuki-pd-pph3-4-dme.yaml`

Original reaction: verdict Not green, PMI 47 (stated amounts only), EcoScale 49.

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Isopropanol/water or 2-MeTHF/water in place of 1,2-dimethoxyethane | J. Sherwood, Beilstein J. Org. Chem., 2020, 16, 1001-1005, doi:10.3762/bjoc.16.89 | **suggested** (number 1 of 3, confidence high) | isopropanol/water (3:1 v/v) in place of 1,2-dimethoxyethane |

## Extraction with dichloromethane and chromatography with hexane (class-independent rules)

Case file: `benchmarks/cases/workup-dcm-extraction.yaml`

| Known greener alternative | Source | Result | Detail |
|---|---|---|---|
| Ethyl acetate or 2-MeTHF in place of dichloromethane for the extraction | D. Prat et al., Green Chem., 2016, 18, 288-296 (CHEM21 solvent guide: dichloromethane is ranked hazardous), doi:10.1039/C5GC01008J | **suggested** (number 3 of 4, confidence low) | ethyl acetate in place of dichloromethane |
| Heptane in place of hexane as the chromatography eluent | D. Prat et al., Green Chem., 2016, 18, 288-296 (CHEM21 solvent guide: hexane is ranked hazardous, heptane problematic), doi:10.1039/C5GC01008J | **suggested** (number 5 of 5, confidence low) | n-heptane in place of hexane |

