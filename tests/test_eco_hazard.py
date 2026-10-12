"""Eco-Scale (Table 10.3/10.4) and hazard metrics (Sec. 10.1.2)."""

import math

import pytest

from modules import eco_scale as es
from modules import hazard_metrics as hz

LLE = "Liquid-liquid extraction (incl. drying/filtration of desiccant)"
RM = "Removal of solvent (bp < 150 degC)"


def test_table_10_4_nitrobenzene_reduction_scores_64():
    inp = es.EcoScaleInputs(
        "Nitrobenzene -> aniline", 90, "Expensive ($10 - $50 for 10 mmol product)",
        es.compound_hazards(["T", "N"]) + es.compound_hazards(["T", "F"]) + es.compound_hazards(["F"]),
        ["Common setup"], "Room temperature, < 1 h",
        workup_steps=["Simple filtration", RM, "Adding solvent", LLE, RM])
    r = es.compute_eco_scale(inp)
    assert r["Total Penalty"] == 36 and r["Eco-Scale Score"] == 64


def test_table_10_4_benzyl_chloride_oxidation():
    inp = es.EcoScaleInputs(
        "Benzyl chloride -> benzoic acid", 87, "Inexpensive (< $10 for 10 mmol product)",
        es.compound_hazards(["T"]), ["Instruments for controlled addition of chemicals"], "Heating, > 1 h",
        workup_steps=[LLE, LLE, RM, "Crystallization and filtration"])
    r = es.compute_eco_scale(inp)
    # The chapter prints 22 / 78 because it rounds the yield penalty (100 - 87) / 2 = 6.5 down to 6.
    assert r["Total Penalty"] == 22.5 and r["Eco-Scale Score"] == 77.5


def test_score_is_clamped_and_yield_is_bounded():
    many = es.compound_hazards(["E", "F+", "T+", "N"]) * 5
    r = es.compute_eco_scale(es.EcoScaleInputs("bad", -20, list(es.PRICE_PENALTIES)[2], many))
    assert r["Eco-Scale Score"] == 0 and r["Yield Penalty"] == 50
    assert es.yield_penalty(150) == 0


def test_stronger_symbol_replaces_weaker_and_setups_count_once():
    assert es.compound_hazards(["F", "F+", "T", "T+"]) == ["F+ - Extremely flammable", "T+ - Extremely toxic"]
    r = es.compute_eco_scale(es.EcoScaleInputs("x", 100, list(es.PRICE_PENALTIES)[0], [],
                                               ["Inert gas atmosphere", "Inert gas atmosphere"]))
    assert r["Technical Setup Penalty"] == 1


def test_legacy_single_workup_category_still_works():
    r = es.compute_eco_scale(es.EcoScaleInputs("x", 100, list(es.PRICE_PENALTIES)[0],
                                               workup_category="Classical chromatography"))
    assert r["Workup Penalty"] == 10


@pytest.mark.parametrize("score,rating", [(80, "Excellent"), (75, "Excellent"), (60, "Acceptable"), (10, "Poor")])
def test_rating_bands(score, rating):
    assert es.eco_scale_rating(score).startswith(rating)


def test_hazard_ratios_follow_printed_equations():
    assert hz.hazard_ratio(2.0, 1.0) == 0.5            # actual exceeds target -> Y < 1
    assert hz.burden_ratio(2.0, 1.0) == 2.0
    assert hz.air_toxicity_score(4, 2) == 0.5 and hz.water_ecotoxicity_score(1, 3) == 3
    assert math.isinf(hz.hazard_ratio(0.0, 1.0))       # no emission at all
    assert math.isnan(hz.hazard_ratio(0.0, 0.0))       # undefined, shown as "undefined"


@pytest.mark.parametrize("logk,rating", [(3.49, "Low"), (3.5, "Moderate"), (4.3, "Moderate"), (4.31, "High")])
def test_bioaccumulation_boundaries(logk, rating):
    assert hz.bioaccumulation_rating(logk) == rating


def test_toxicity_and_persistence():
    assert hz.ingestion_toxicity_potential(5, 100) == 0.05
    assert hz.inhalation_toxicity_potential(1, 0) == math.inf
    assert hz.environmental_persistence(10, 0.5) == 20
    assert hz.boethling_index(100, 0) == pytest.approx(3.199 - 0.21)
