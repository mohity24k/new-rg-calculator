"""
mass_metrics.py
----------------
Mass-based Green Chemistry metrics (univariate analysis metrics).

Reference:
Tamargo, R.J.I., Cosinero, H.S.O., Potato, D.N.C., Dumrigue, A.H.P. (2024).
"Metrics of Green Chemistry and Sustainability." In: Sen, M. (Ed.),
Sustainable Green Catalytic Processes, Ch. 10, pp. 225-258. Scrivener Publishing.
"""

from dataclasses import dataclass
from typing import List

CARBON_MOLAR_MASS = 12.011  # g/mol


@dataclass
class Reactant:
    """A single reactant charged into a reaction."""
    name: str
    mw: float           # g/mol
    moles: float         # mol actually charged
    mass: float          # g actually charged
    carbons: int = 0      # carbon atoms per molecule (for Carbon Efficiency)
    stoich_ratio: float = 1.0  # moles required per 1 mole of limiting reagent
    is_limiting: bool = False


@dataclass
class AuxiliaryMaterial:
    """Non-stoichiometric material: solvent, catalyst, base, workup reagent, water, etc."""
    name: str
    mass: float  # g


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------

def atom_economy(mw_product: float, reactants: List[Reactant]) -> float:
    """
    Atom Economy (AE) -- Trost, 1991.
        AE (%) = [MW_product / sum(MW_reactants, stoichiometric)] x 100
    Theoretical metric: assumes 100% yield, exact stoichiometry, and
    excludes solvents/reagents not incorporated into the product.
    """
    denom = sum(r.mw for r in reactants)
    if denom <= 0:
        return 0.0
    return (mw_product / denom) * 100.0


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


def mass_productivity(pmi: float) -> float:
    """
    Mass Productivity (MP) -- reciprocal-percentage of PMI, favored in industry
    because higher values intuitively communicate better resource utilization.
        MP (%) = (1 / PMI) x 100
    """
    if pmi <= 0:
        return 0.0
    return (1.0 / pmi) * 100.0


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

def compute_all_mass_metrics(reactants: List[Reactant],
                              product_mw: float,
                              product_actual_mass: float,
                              product_moles: float,
                              auxiliaries: List[AuxiliaryMaterial],
                              water_mass: float = 0.0) -> dict:
    """
    Run the full mass-based metrics suite for one reaction/route.
    Returns a flat dictionary of computed values, ready for display/export.
    """
    aux_mass_total = sum(a.mass for a in auxiliaries)
    reactant_mass_total = sum(r.mass for r in reactants)

    total_mass_input_incl_water = reactant_mass_total + aux_mass_total + water_mass
    total_mass_input_excl_water = reactant_mass_total + aux_mass_total

    pmi = process_mass_intensity(total_mass_input_incl_water, product_actual_mass)
    mi = mass_intensity_excl_water(total_mass_input_excl_water, product_actual_mass)
    efactor = e_factor_from_pmi(pmi)
    mp = mass_productivity(pmi)
    ae = atom_economy(product_mw, reactants)
    rme = reaction_mass_efficiency(product_actual_mass, reactants)

    return {
        "Atom Economy (AE, %)": ae,
        "Reaction Mass Efficiency (RME, %)": rme,
        "Process Mass Intensity (PMI, kg/kg)": pmi,
        "Mass Intensity excl. water (MI, kg/kg)": mi,
        "E-Factor (kg waste/kg product)": efactor,
        "Mass Productivity (MP, %)": mp,
        "Total Reactant Mass (g)": reactant_mass_total,
        "Total Auxiliary Mass (g)": aux_mass_total,
        "Total Mass Input incl. water (g)": total_mass_input_incl_water,
    }
