"""The benchmark of known greener alternatives runs offline and its written results are current."""

import gms
from gms import benchmarks


def test_benchmark_results_file_is_current():
    """benchmarks/RESULTS.md is what the code produces now. If this fails after a deliberate
    change, run `python scripts/run_benchmarks.py` and review the difference."""
    results = benchmarks.run()
    written = (benchmarks.CASES_DIR.parent / "RESULTS.md").read_text(encoding="utf-8")
    assert benchmarks.to_markdown(results, gms.__version__) + "\n" == written


def test_benchmark_outcomes():
    results = {c["id"]: c for c in benchmarks.run()}
    c = benchmarks.counts(list(results.values()))
    assert c["total"] == 13 and c["not generated"] == 0
    assert c["suggested"] >= 9 and c["top3"] >= 8                  # more than half are in the first three
    # every case has a source, and every rejection has its reason
    for case in benchmarks.load_cases():
        assert case["source"]["description"]
        for k in case.get("known_greener", []):
            assert k["source"]["citation"]
    for case in results.values():
        for r in case["results"]:
            if r["outcome"] == "rejected":
                assert r["detail"]
    amide = {r["kind"]: r for r in results["amide-hatu-dmf"]["results"]}
    assert amide["reaction_solvent"]["outcome"] == "suggested" and amide["reaction_solvent"]["rank"] == 1
    assert amide["reagent"]["outcome"] == "rejected" and "hazard" in amide["reagent"]["detail"]
    ibu = {r["description"]: r for r in results["ibuprofen-routes"]["results"]}
    assert ibu["Boots route: atom economy"]["computed"] == 40.1 and ibu["BHC route: atom economy"]["computed"] == 77.4
    assert all(r["outcome"] == "matches" for r in ibu.values())
