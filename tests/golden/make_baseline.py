"""Writes tests/golden/metrics_baseline.json from the calculators.

The file committed in the repository was produced by the code at commit d2513eb (before the
v1 restructuring). tests/test_regression_lock.py compares the current code against it, so
any change to a formula shows up as a failing test. Re-run this script only for a deliberate,
documented formula change.
"""
import itertools
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:                                    # layout after the v1 restructuring
    from gms.metrics import ecoscale as es
    from gms.metrics import hazard_ratios as hz
    from gms.metrics import mass as mm
except ImportError:                     # layout at commit d2513eb
    from modules import eco_scale as es
    from modules import hazard_metrics as hz
    from modules import mass_metrics as mm


def enc(v):
    if isinstance(v, float) and not math.isfinite(v):
        return {"__float__": "nan" if math.isnan(v) else ("inf" if v > 0 else "-inf")}
    if isinstance(v, dict):
        return {k: enc(x) for k, x in v.items()}
    return v


MASS_CASES = {
    "curzons": dict(
        reactants=[dict(name="Benzyl alcohol", mw=108.14, moles=0.10, mass=10.81, carbons=7, is_limiting=True),
                   dict(name="p-Toluenesulfonyl chloride", mw=190.64, moles=0.115, mass=21.9, carbons=7)],
        aux=[dict(name="Toluene", mass=500.0), dict(name="Triethylamine", mass=15.0)],
        product_mw=262.32, product_actual_mass=23.6, product_moles=0.09, product_carbons=14),
    "curzons_water_benign": dict(
        reactants=[dict(name="Benzyl alcohol", mw=108.14, moles=0.10, mass=10.81, carbons=7, is_limiting=True),
                   dict(name="p-Toluenesulfonyl chloride", mw=190.64, moles=0.115, mass=21.9, carbons=7)],
        aux=[dict(name="Toluene", mass=500.0), dict(name="Ethanol", mass=40.0, benign=True)],
        product_mw=262.32, product_actual_mass=23.6, water_mass=100.0, product_carbons=14),
    "two_to_one": dict(
        reactants=[dict(name="H2", mw=2.016, moles=0.25, mass=0.504, stoich_ratio=2),
                   dict(name="O2", mw=31.998, moles=0.1, mass=3.1998, stoich_ratio=1, is_limiting=True)],
        aux=[], product_mw=18.015, product_actual_mass=3.2, product_coefficient=2.0),
    "no_limiting_marked": dict(
        reactants=[dict(name="A", mw=150.0, moles=0.02, mass=3.0, carbons=8),
                   dict(name="B", mw=100.0, moles=0.03, mass=3.0, carbons=5, benign=True)],
        aux=[dict(name="DMF", mass=18.9), dict(name="EtOAc", mass=90.2), dict(name="silica", mass=50.0)],
        product_mw=232.0, product_actual_mass=3.9, water_mass=60.0, product_carbons=13),
    "zero_product": dict(
        reactants=[dict(name="A", mw=150.0, moles=0.02, mass=3.0, is_limiting=True)],
        aux=[dict(name="THF", mass=10.0)], product_mw=150.0, product_actual_mass=0.0),
    "no_reactants": dict(reactants=[], aux=[dict(name="THF", mass=10.0)], product_mw=150.0,
                         product_actual_mass=1.0),
    "suzuki_small_scale": dict(
        reactants=[dict(name="4-bromoanisole", mw=187.04, moles=0.001, mass=0.18704, carbons=7, is_limiting=True),
                   dict(name="phenylboronic acid", mw=121.93, moles=0.0015, mass=0.182895, carbons=6)],
        aux=[dict(name="Pd(PPh3)4", mass=0.05778), dict(name="K2CO3", mass=0.276),
             dict(name="1,4-dioxane", mass=4.12), dict(name="ethyl acetate", mass=27.06)],
        product_mw=184.24, product_actual_mass=0.166, water_mass=1.0, product_carbons=13),
}

ECO_CASES = {
    "ideal": dict(route_name="r", yield_pct=100.0, price_category="Inexpensive (< $10 for 10 mmol product)"),
    "table_10_4_like": dict(
        route_name="r", yield_pct=87.0, price_category="Inexpensive (< $10 for 10 mmol product)",
        safety_hazards=["T - Toxic", "N - Dangerous to environment", "T - Toxic", "F - Highly flammable"],
        technical_setups=["Common setup"], temp_time_category="Heating, > 1 h",
        workup_steps=["Liquid-liquid extraction (incl. drying/filtration of desiccant)",
                      "Liquid-liquid extraction (incl. drying/filtration of desiccant)",
                      "Crystallization and filtration"]),
    "harsh": dict(
        route_name="r", yield_pct=42.5, price_category="Very expensive (> $50 for 10 mmol product)",
        safety_hazards=["T+ - Extremely toxic", "F+ - Extremely flammable", "E - Explosive"],
        technical_setups=["Inert gas atmosphere", "Glove box", "Inert gas atmosphere",
                          "Pressure equipment (> 1 atm)"],
        temp_time_category="Cooling, < 0 degC", workup_steps=["Classical chromatography", "Distillation"]),
    "single_workup_category": dict(
        route_name="r", yield_pct=90.0, price_category="Expensive ($10 - $50 for 10 mmol product)",
        temp_time_category="Room temperature, < 24 h", workup_category="Classical chromatography"),
    "floor_at_zero": dict(
        route_name="r", yield_pct=0.0, price_category="Very expensive (> $50 for 10 mmol product)",
        safety_hazards=["E - Explosive"] * 6, workup_steps=["Classical chromatography"] * 3),
}


def build():
    out = {"mass": {}, "eco": {}, "hazard": {}, "yield_penalty": {}, "compound_hazards": {}}
    for name, c in MASS_CASES.items():
        kw = {k: v for k, v in c.items() if k not in ("reactants", "aux")}
        r = mm.compute_all_mass_metrics(
            reactants=[mm.Reactant(**x) for x in c["reactants"]],
            auxiliaries=[mm.AuxiliaryMaterial(**x) for x in c["aux"]], **kw)
        out["mass"][name] = {"inputs": c, "outputs": enc(r)}
    for name, c in ECO_CASES.items():
        out["eco"][name] = {"inputs": c, "outputs": enc(es.compute_eco_scale(es.EcoScaleInputs(**c)))}
    for a, t in itertools.product([0.0, 0.5, 2.0], [0.0, 1.0, 3.0]):
        out["hazard"][f"{a}|{t}"] = enc({"hazard_ratio": hz.hazard_ratio(a, t), "burden_ratio": hz.burden_ratio(a, t)})
    out["hazard"]["suite"] = enc(hz.compute_global_hazard_suite(hz.GlobalHazardInputs(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
    out["hazard"]["tox"] = enc({
        "ingtp": hz.ingestion_toxicity_potential(2.0, 300.0), "inhtp": hz.inhalation_toxicity_potential(0.5, 20.0),
        "bap": [hz.bioaccumulation_rating(x) for x in (1.0, 3.5, 4.3, 5.0)],
        "ep": hz.environmental_persistence(3.0, 0.2), "boethling": hz.boethling_index(180.0, 0.4)})
    for y in (0, 12.5, 77, 100, 140, -5):
        out["yield_penalty"][str(y)] = es.yield_penalty(y)
    for syms in (["F", "F+"], ["T", "T+", "N"], ["E"], ["x", ""], ["N", "N"]):
        out["compound_hazards"]["|".join(syms)] = es.compound_hazards(syms)
    return out


if __name__ == "__main__":
    path = Path(__file__).with_name("metrics_baseline.json")
    path.write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", path)
