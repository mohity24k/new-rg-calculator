"""
hazard_metrics.py
------------------
Impact-based (multivariate) green chemistry metrics: global hazard scores
and toxicological hazard indicators.

Reference:
Tamargo et al. (2024), Sections 10.1.2.2 (Toxicological Hazard Metrics) and
10.1.2.3 (Global Hazard Metrics).

NOTE ON CONVENTION:
The source chapter defines global-hazard scores generically as
    Y = X(target) / X(actual)
where X(actual) is the calculated/emitted burden and X(target) is the
regulatory or sustainable threshold. Under this convention, Y >= 1 means
the process is operating AT OR WITHIN the target threshold (favorable),
while Y < 1 means the actual burden EXCEEDS the target (unfavorable).
This app follows that convention consistently for AP, ODP, SFP, GWP and ADP.
"""

from dataclasses import dataclass


def hazard_ratio(x_actual: float, x_target: float) -> float:
    """
    Generic global hazard ratio, Y = X(target) / X(actual).
    Used for Acidification Potential (AP), Ozone Depletion Potential (ODP),
    Smog Formation Potential (SFP), Global Warming Potential (GWP), and
    Abiotic Depletion Potential (ADP).
    Y >= 1  -> within target / favorable
    Y < 1   -> exceeds target / unfavorable
    """
    if x_actual <= 0:
        return float("inf")
    return x_target / x_actual


def ingestion_toxicity_potential(c_water: float, ld50: float) -> float:
    """
    Human Toxicity by Ingestion Potential (INGTP) = C_w / LD50
    C_w   : concentration of compound in water
    LD50  : estimated lethal dose to 50% of test-animal population
    Lower INGTP -> lower ingestion hazard.
    """
    if ld50 <= 0:
        return float("inf")
    return c_water / ld50


def inhalation_toxicity_potential(c_air: float, lc50: float) -> float:
    """
    Human Toxicity by Inhalation Potential (INHTP) = C_a / LC50
    C_a   : concentration of the emitted chemical in air
    LC50  : estimated lethal concentration (single exposure, 50% mortality)
    Lower INHTP -> lower inhalation hazard.
    """
    if lc50 <= 0:
        return float("inf")
    return c_air / lc50


def bioaccumulation_rating(log_kow: float) -> str:
    """
    Bioaccumulation potential (BAP), approximated via octanol-water
    partition coefficient, log P (log Kow).
        < 3.5        -> Low bioaccumulation potential
        3.5 - 4.3    -> Moderate bioaccumulation potential
        > 4.3        -> High bioaccumulation potential
    """
    if log_kow < 3.5:
        return "Low"
    elif log_kow <= 4.3:
        return "Moderate"
    else:
        return "High"


def environmental_persistence(c0: float, k: float) -> float:
    """
    Environmental Persistence (EP) = C0 / k
    C0 : initial concentration of the substance in the environment
    k  : first-order degradation / removal rate constant
    Lower EP (i.e., higher k) is greener -- the substance degrades faster.
    """
    if k <= 0:
        return float("inf")
    return c0 / k


def boethling_index(mw: float, sum_functional_group_terms: float) -> float:
    """
    Boethling Index -- estimates aerobic biodegradation lifetime.
        BI = 3.199 - 0.0021*MW + sum(a_n + f_n)
    where a_n is the number of functional groups and f_n a group-specific
    factor. `sum_functional_group_terms` should be pre-computed by the user
    as sum(a_n + f_n) across all relevant functional groups.
    """
    return 3.199 - 0.0021 * mw + sum_functional_group_terms


@dataclass
class GlobalHazardInputs:
    ap_actual: float
    ap_target: float
    odp_actual: float
    odp_target: float
    sfp_actual: float
    sfp_target: float
    gwp_actual: float
    gwp_target: float
    adp_actual: float
    adp_target: float


def compute_global_hazard_suite(inputs: GlobalHazardInputs) -> dict:
    """Compute all five global hazard ratios (Y_AP, Y_ODP, Y_SFP, Y_GWP, Y_ADP)."""
    return {
        "Acidification Potential (Y_AP)": hazard_ratio(inputs.ap_actual, inputs.ap_target),
        "Ozone Depletion Potential (Y_ODP)": hazard_ratio(inputs.odp_actual, inputs.odp_target),
        "Smog Formation Potential (Y_SFP)": hazard_ratio(inputs.sfp_actual, inputs.sfp_target),
        "Global Warming Potential (Y_GWP)": hazard_ratio(inputs.gwp_actual, inputs.gwp_target),
        "Abiotic Depletion Potential (Y_ADP)": hazard_ratio(inputs.adp_actual, inputs.adp_target),
    }
