"""
mass_metrics.py
----------------
Mass-based Green Chemistry metrics (univariate analysis metrics).

Reference:
Tamargo, R.J.I., Cosinero, H.S.O., Potato, D.N.C., Dumrigue, A.H.P. (2024).
"Metrics of Green Chemistry and Sustainability." In: Sen, M. (Ed.),
Sustainable Green Catalytic Processes, Ch. 10, pp. 225-258. Scrivener Publishing.
"""

import math
from dataclasses import dataclass
from typing import List, Optional

CARBON_MOLAR_MASS = 12.011  # g/mol


@dataclass
class Reactant:
    """A single reactant charged into a reaction."""
    name: str
    mw: float           # g/mol
    moles: float         # mol actually charged
    mass: float          # g actually charged
    carbons: int = 0      # carbon atoms per molecule (for Carbon Efficiency)
    stoich_ratio: float = 1.0  # moles required per 1 mole of limiting reagent (= coefficient ratio)
    is_limiting: bool = False
    benign: bool = False  # Hudlicky's "benign" (no environmental risk), used by EMY only


@dataclass
class AuxiliaryMaterial:
    """Non-stoichiometric material: solvent, catalyst, base, workup reagent, water, etc."""
    name: str
    mass: float  # g
    benign: bool = False  # e.g. water, NaCl, dilute ethanol (EMY only)


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------

def atom_economy(mw_product: float, reactants: List[Reactant], product_coefficient: float = 1.0) -> float:
    """
    Atom Economy (AE) -- Trost, 1991.
        AE (%) = [nu_P x MW_product / sum(nu_i x MW_i)] x 100     (Eq. 10.4)
    nu are the stoichiometric coefficients relative to the limiting reagent (the
    'Stoich. ratio' column), so 2 A + B -> P counts MW_A twice. With all
    coefficients = 1 this is exactly Eq. 10.4.
    Theoretical metric: assumes 100% yield, exact stoichiometry, and
    excludes solvents/reagents not incorporated into the product.
    """
    denom = sum(max(r.stoich_ratio, 0.0) * r.mw for r in reactants)
    if denom <= 0:
        return 0.0
    return (product_coefficient * mw_product / denom) * 100.0


def theoretical_yield_mass(limiting_moles: float, product_ratio: float, mw_product: float) -> float:
    """Theoretical mass of product (g) = [A] x [P/A] x MW_P   (Eq. 10.2)."""
    return max(limiting_moles, 0.0) * max(product_ratio, 0.0) * max(mw_product, 0.0)


def percentage_yield(actual_mass_product: float, theoretical_mass_product: float) -> float:
    """Percentage yield = actual mass / theoretical mass x 100   (Eq. 10.1)."""
    if theoretical_mass_product <= 0:
        return 0.0
    return actual_mass_product / theoretical_mass_product * 100.0


def effective_mass_yield(mass_product: float, mass_non_benign: float) -> float:
    """
    Effective Mass Yield (EMY) -- Hudlicky et al., 1999   (Eq. 10.9).
        EMY (%) = mass of product / mass of non-benign reagents x 100
    'Benign' = no environmental risk (water, low-concentration saline, dilute
    ethanol, ...). EMY can exceed 100% when most inputs are benign.
    """
    if mass_non_benign <= 0:
        return float("inf") if mass_product > 0 else 0.0
    return mass_product / mass_non_benign * 100.0


def reaction_mass_efficiency(actual_mass_product: float, reactants: List[Reactant]) -> float:
    """
    Reaction Mass Efficiency (RME) -- Curzons et al.
        RME (%) = [Actual mass of product / Total mass of reactants charged] x 100
    Unlike AE, RME captures real yield, excess reagent use, and stoichiometry,
    but still excludes solvents/catalysts/workup materials.
    """
    denom = sum(r.mass for r in reactants)
    if denom <= 0:
        return 0.0
    return (actual_mass_product / denom) * 100.0


def process_mass_intensity(total_mass_input: float, mass_product: float) -> float:
    """
    Process Mass Intensity (PMI).
        PMI = Total mass in reaction vessel, INCLUDING water (kg) / Mass of product (kg)
    Adopted by the ACS GCI Pharmaceutical Roundtable as the standard mass metric.
    Ideal PMI = 1 (input mass equals product mass -- zero waste).
    """
    if mass_product <= 0:
        return float("inf")
    return total_mass_input / mass_product


def mass_intensity_excl_water(total_mass_input_no_water: float, mass_product: float) -> float:
    """
    Mass Intensity (MI) -- same as PMI but EXCLUDES water, since water is
    considered to carry minimal direct environmental burden and can skew
    results for aqueous processes.
    """
    if mass_product <= 0:
        return float("inf")
    return total_mass_input_no_water / mass_product


def e_factor_from_pmi(pmi: float) -> float:
    """E-Factor derived from PMI: E-factor = PMI - 1 (Sheldon relationship, Eq. 10.27)."""
    return pmi - 1.0


def e_factor_from_waste(mass_waste: float, mass_product: float) -> float:
    """
    Environmental Factor (E-factor) -- Sheldon, early 1990s.
        E-factor = Total mass of waste (kg) / Mass of product (kg)
    "Waste" = anything that is not the desired product. Ideal E-factor = 0.
    """
    if mass_product <= 0:
        return float("inf")
    return mass_waste / mass_product


def carbon_efficiency(carbon_mass_product: float, carbon_mass_reactants: float) -> float:
    """
    Carbon Efficiency (CE).
        CE (%) = [Mass of carbon in product / Total mass of carbon in reactants] x 100
    Estimates what fraction of reactant carbon ends up in the isolated product.
    """
    if carbon_mass_reactants <= 0:
        return 0.0
    return (carbon_mass_product / carbon_mass_reactants) * 100.0


def mass_productivity(mi: float) -> float:
    """
    Mass Productivity (MP) -- percentage reciprocal of Mass Intensity   (Eq. 10.28).
        MP (%) = (1 / MI) x 100 = mass of product / total mass in vessel x 100
    Favored in industry because higher values communicate better resource use.
    """
    if mi <= 0 or math.isinf(mi):
        return 0.0
    return (1.0 / mi) * 100.0


def excess_reactant_factor(stoichiometric_mass: float, excess_mass: float) -> float:
    """
    Excess Reactant Factor (ERF).
        ERF = (stoichiometric mass + excess mass) / stoichiometric mass
    ERF = 1 -> no excess reactant used (ideal). ERF >> 1 -> large excess / waste.
    """
    if stoichiometric_mass <= 0:
        return 0.0
    return (stoichiometric_mass + excess_mass) / stoichiometric_mass


def carbon_mass_from_moles(moles: float, carbons_per_molecule: int,
                            carbon_mw: float = CARBON_MOLAR_MASS) -> float:
    """Helper: mass of carbon (g) contributed by `moles` of a species."""
    return moles * carbons_per_molecule * carbon_mw


# ---------------------------------------------------------------------------
# Aggregate calculator
# ---------------------------------------------------------------------------

def limiting_reactant(reactants: List[Reactant]) -> Optional[Reactant]:
    """The reactant marked as limiting, else the one with the fewest moles per coefficient."""
    marked = [r for r in reactants if r.is_limiting]
    if marked:
        return marked[0]
    usable = [r for r in reactants if r.moles > 0 and r.stoich_ratio > 0]
    return min(usable, key=lambda r: r.moles / r.stoich_ratio) if usable else None


def stoichiometric_masses(reactants: List[Reactant], limiting: Reactant) -> tuple[float, float]:
    """(stoichiometric mass, excess mass) of all reactants relative to the limiting reagent."""
    stoich_total = excess_total = 0.0
    for r in reactants:
        stoich_moles = limiting.moles * r.stoich_ratio / (limiting.stoich_ratio or 1.0)
        stoich_total += stoich_moles * r.mw
        excess_total += max(0.0, r.moles - stoich_moles) * r.mw
    return stoich_total, excess_total


def validate_inputs(reactants: List[Reactant], product_mw: float, product_actual_mass: float,
                    rel_tol: float = 0.02) -> List[str]:
    """Plain-language warnings about inputs that would make the metrics misleading."""
    w = []
    if not reactants:
        w.append("No reactants entered: AE, RME, CE and ERF cannot be computed.")
    for r in reactants:
        if r.mw <= 0:
            w.append(f"'{r.name}': MW is 0 or missing.")
        if r.mass <= 0:
            w.append(f"'{r.name}': mass charged is 0 or missing.")
        if r.mw > 0 and r.moles > 0 and r.mass > 0 and \
                abs(r.moles * r.mw - r.mass) > rel_tol * r.mass:
            w.append(f"'{r.name}': mass ({r.mass:g} g) does not match moles x MW "
                     f"({r.moles * r.mw:.4g} g). Check the units (g vs mg, mol vs mmol).")
        if r.stoich_ratio <= 0:
            w.append(f"'{r.name}': stoichiometric ratio must be > 0.")
    n_lim = sum(1 for r in reactants if r.is_limiting)
    if reactants and n_lim == 0:
        w.append("No limiting reagent ticked: the reactant with the fewest moles (per coefficient) is used.")
    elif n_lim > 1:
        w.append("More than one limiting reagent ticked: only the first one is used.")
    lim = limiting_reactant(reactants)
    usable = [r for r in reactants if r.moles > 0 and r.stoich_ratio > 0]
    if lim is not None and usable and lim.moles > 0:
        true_lim = min(usable, key=lambda r: r.moles / r.stoich_ratio)
        if true_lim is not lim and true_lim.moles / true_lim.stoich_ratio < \
                (1 - rel_tol) * lim.moles / lim.stoich_ratio:
            w.append(f"'{true_lim.name}' runs out before '{lim.name}': it is the real limiting reagent.")
    if product_mw <= 0:
        w.append("Product MW is 0: AE, yield and CE cannot be computed.")
    if product_actual_mass <= 0:
        w.append("Product mass is 0: PMI, MI, E-factor and RME are undefined.")
    total_reactant = sum(r.mass for r in reactants)
    if product_actual_mass > 0 and total_reactant > 0 and product_actual_mass > total_reactant * (1 + rel_tol):
        w.append("Product mass is larger than the total reactant mass: a reactant is probably missing "
                 "(e.g. a reagent that is incorporated into the product).")
    return w


def compute_all_mass_metrics(reactants: List[Reactant],
                              product_mw: float,
                              product_actual_mass: float,
                              product_moles: Optional[float] = None,
                              auxiliaries: Optional[List[AuxiliaryMaterial]] = None,
                              water_mass: float = 0.0,
                              product_coefficient: float = 1.0,
                              product_carbons: Optional[int] = None) -> dict:
    """
    Run the full mass-based metrics suite for one reaction/route.
    Returns a flat dictionary of computed values, ready for display/export.
    If product_moles is None it is derived from the product mass and MW.
    """
    auxiliaries = auxiliaries or []
    water_mass = max(water_mass or 0.0, 0.0)
    if product_moles is None:
        product_moles = product_actual_mass / product_mw if product_mw > 0 else 0.0

    aux_mass_total = sum(a.mass for a in auxiliaries)
    reactant_mass_total = sum(r.mass for r in reactants)

    total_mass_input_incl_water = reactant_mass_total + aux_mass_total + water_mass
    total_mass_input_excl_water = reactant_mass_total + aux_mass_total

    pmi = process_mass_intensity(total_mass_input_incl_water, product_actual_mass)
    mi = mass_intensity_excl_water(total_mass_input_excl_water, product_actual_mass)
    efactor = e_factor_from_pmi(pmi)
    mp = mass_productivity(mi)
    ae = atom_economy(product_mw, reactants, product_coefficient)
    rme = reaction_mass_efficiency(product_actual_mass, reactants)
    non_benign = (sum(r.mass for r in reactants if not r.benign)
                  + sum(a.mass for a in auxiliaries if not a.benign))   # water is benign
    emy = effective_mass_yield(product_actual_mass, non_benign)

    results = {
        "Atom Economy (AE, %)": ae,
        "Reaction Mass Efficiency (RME, %)": rme,
        "Process Mass Intensity (PMI, kg/kg)": pmi,
        "Mass Intensity excl. water (MI, kg/kg)": mi,
        "E-Factor (kg waste/kg product)": efactor,
        "Mass Productivity (MP, %)": mp,
        "Effective Mass Yield (EMY, %)": emy,
    }

    lim = limiting_reactant(reactants)
    if lim is not None and lim.moles > 0:
        theo = theoretical_yield_mass(lim.moles / (lim.stoich_ratio or 1.0), product_coefficient, product_mw)
        results["Theoretical Yield (g)"] = theo
        results["Percentage Yield (%)"] = percentage_yield(product_actual_mass, theo)
        stoich, excess = stoichiometric_masses(reactants, lim)
        results["Excess Reactant Factor (ERF)"] = excess_reactant_factor(stoich, excess)

    if product_carbons is not None:
        c_react = sum(carbon_mass_from_moles(r.moles, r.carbons) for r in reactants)
        c_prod = carbon_mass_from_moles(product_moles, product_carbons)
        results["Carbon Efficiency (CE, %)"] = carbon_efficiency(c_prod, c_react)

    results.update({
        "Product Moles (mol)": product_moles,
        "Total Reactant Mass (g)": reactant_mass_total,
        "Total Auxiliary Mass (g)": aux_mass_total,
        "Water Mass (g)": water_mass,
        "Total Mass Input incl. water (g)": total_mass_input_incl_water,
    })
    return results
