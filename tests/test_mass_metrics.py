"""Mass-based metrics, checked against the worked examples in Tamargo et al. (2024), Sec. 10.1.1."""

import json
import math

import pytest

from modules import data_export as dx
from modules import mass_metrics as mm


def curzons():
    """Benzyl alcohol + TsCl -> benzyl tosylate (Sections 10.1.1.2 - 10.1.1.8)."""
    reactants = [
        mm.Reactant("Benzyl alcohol", 108.14, 0.10, 10.81, carbons=7, is_limiting=True),
        mm.Reactant("p-Toluenesulfonyl chloride", 190.64, 0.115, 21.9, carbons=7),
    ]
    aux = [mm.AuxiliaryMaterial("Toluene", 500.0), mm.AuxiliaryMaterial("Triethylamine", 15.0)]
    return reactants, aux


def test_paper_example_matches_chapter():
    reactants, aux = curzons()
    r = mm.compute_all_mass_metrics(reactants, 262.32, 23.6, 0.09, aux, product_carbons=14)
    assert r["Atom Economy (AE, %)"] == pytest.approx(88, abs=0.5)            # Eq. 10.7
    assert r["Reaction Mass Efficiency (RME, %)"] == pytest.approx(72, abs=0.5)  # Eq. 10.14
    assert r["Mass Intensity excl. water (MI, kg/kg)"] == pytest.approx(23.2, abs=0.05)  # Eq. 10.25
    assert r["Mass Productivity (MP, %)"] == pytest.approx(4.3, abs=0.05)     # Sec. 10.1.1.7
    assert r["Carbon Efficiency (CE, %)"] == pytest.approx(83.7, abs=0.05)    # Eq. 10.33
    assert r["Excess Reactant Factor (ERF)"] == pytest.approx(1.10, abs=0.005)  # = SF, Eq. 10.18
    assert r["Percentage Yield (%)"] == pytest.approx(90, abs=0.1)            # Eq. 10.16
    assert r["E-Factor (kg waste/kg product)"] == pytest.approx(r["Process Mass Intensity (PMI, kg/kg)"] - 1)


def test_rme_equals_yield_times_ae_over_sf():
    """Eq. 10.11: RME = yield x AE x 1/SF."""
    reactants, aux = curzons()
    r = mm.compute_all_mass_metrics(reactants, 262.32, 23.6, None, aux)
    lhs = r["Reaction Mass Efficiency (RME, %)"]
    rhs = r["Percentage Yield (%)"] / 100 * r["Atom Economy (AE, %)"] / r["Excess Reactant Factor (ERF)"]
    # Holds to ~0.1 %: the chapter's masses (10.81 g, 21.9 g) are rounded, not exactly moles x MW.
    assert lhs == pytest.approx(rhs, rel=2e-3)
    exact = [mm.Reactant(r.name, r.mw, r.moles, r.moles * r.mw, is_limiting=r.is_limiting) for r in reactants]
    r = mm.compute_all_mass_metrics(exact, 262.32, 23.6, None, aux)
    assert r["Reaction Mass Efficiency (RME, %)"] == pytest.approx(
        r["Percentage Yield (%)"] / 100 * r["Atom Economy (AE, %)"] / r["Excess Reactant Factor (ERF)"], rel=1e-9)


def test_water_counts_in_pmi_not_in_mi_and_mp_uses_mi():
    reactants, aux = curzons()
    r = mm.compute_all_mass_metrics(reactants, 262.32, 23.6, None, aux, water_mass=100)
    assert r["Process Mass Intensity (PMI, kg/kg)"] == pytest.approx(647.71 / 23.6)
    assert r["Mass Intensity excl. water (MI, kg/kg)"] == pytest.approx(547.71 / 23.6)
    assert r["Mass Productivity (MP, %)"] == pytest.approx(100 * 23.6 / 547.71)  # Eq. 10.28/10.29


def test_atom_economy_uses_stoichiometric_coefficients():
    # 2 H2 + O2 -> 2 H2O : AE = 100 %
    h2 = mm.Reactant("H2", 2.016, 0.2, 0.4032, stoich_ratio=2)
    o2 = mm.Reactant("O2", 31.998, 0.1, 3.1998, stoich_ratio=1, is_limiting=True)
    assert mm.atom_economy(18.015, [h2, o2], product_coefficient=2) == pytest.approx(100, abs=0.1)
    # Fischer esterification AcOH + EtOH -> EtOAc + H2O : AE = 83 %
    ac = mm.Reactant("AcOH", 60.05, 1, 60.05)
    et = mm.Reactant("EtOH", 46.07, 1, 46.07)
    assert mm.atom_economy(88.11, [ac, et]) == pytest.approx(83.0, abs=0.1)


def test_theoretical_yield_with_product_coefficient():
    # A -> 2 P : 0.1 mol A gives 0.2 mol P
    a = mm.Reactant("A", 100, 0.1, 10, is_limiting=True)
    r = mm.compute_all_mass_metrics([a], 50, 9.0, None, [], product_coefficient=2)
    assert r["Theoretical Yield (g)"] == pytest.approx(10.0)
    assert r["Percentage Yield (%)"] == pytest.approx(90.0)


def test_limiting_reagent_with_coefficient_2():
    # 2 A + B -> P, B limiting (0.1 mol), A charged at 0.25 mol (0.05 mol excess)
    a = mm.Reactant("A", 100, 0.25, 25, stoich_ratio=2)
    b = mm.Reactant("B", 50, 0.10, 5, stoich_ratio=1, is_limiting=True)
    stoich, excess = mm.stoichiometric_masses([a, b], b)
    assert stoich == pytest.approx(0.2 * 100 + 0.1 * 50)
    assert excess == pytest.approx(0.05 * 100)


def test_effective_mass_yield_treats_benign_and_water():
    reactants, _ = curzons()
    aux = [mm.AuxiliaryMaterial("Toluene", 500), mm.AuxiliaryMaterial("NaCl(aq)", 100, benign=True)]
    r = mm.compute_all_mass_metrics(reactants, 262.32, 23.6, None, aux, water_mass=1000)
    assert r["Effective Mass Yield (EMY, %)"] == pytest.approx(100 * 23.6 / (32.71 + 500))


def test_zero_product_mass_is_infinite_but_exportable():
    reactants, aux = curzons()
    r = mm.compute_all_mass_metrics(reactants, 262.32, 0.0, None, aux)
    assert math.isinf(r["Process Mass Intensity (PMI, kg/kg)"])
    assert r["Mass Productivity (MP, %)"] == 0.0
    json.loads(dx.to_json_bytes([dx.build_combined_report("Route A", mass_metrics=r)]))  # valid JSON
    assert "infinite" in dx.build_summary_dataframe(r)["Value"].tolist()


def test_no_reactants_does_not_crash():
    r = mm.compute_all_mass_metrics([], 100.0, 1.0, None, [])
    assert r["Atom Economy (AE, %)"] == 0.0
    assert "Excess Reactant Factor (ERF)" not in r
    assert any("No reactants" in w for w in mm.validate_inputs([], 100.0, 1.0))


def test_validation_catches_common_lab_mistakes():
    a = mm.Reactant("A", 100, 0.010, 1000.0, is_limiting=True)   # mass typed in mg
    b = mm.Reactant("B", 50, 0.005, 0.25)                         # B actually runs out first
    w = " ".join(mm.validate_inputs([a, b], 120, 2000))
    assert "does not match moles x MW" in w
    assert "real limiting reagent" in w
    assert "larger than the total reactant mass" in w
    w2 = " ".join(mm.validate_inputs([mm.Reactant("A", 100, 0.1, 10, is_limiting=True),
                                       mm.Reactant("B", 100, 0.1, 10, is_limiting=True)], 100, 5))
    assert "More than one limiting" in w2


def test_unmarked_limiting_reagent_is_inferred():
    a = mm.Reactant("A", 100, 0.2, 20)
    b = mm.Reactant("B", 100, 0.1, 10)
    assert mm.limiting_reactant([a, b]) is b
