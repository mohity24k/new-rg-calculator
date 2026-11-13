"""Run the benchmark of known greener alternatives and write benchmarks/RESULTS.md.

    python scripts/run_benchmarks.py            (bundled hazard data only: reproducible, no network)
    python scripts/run_benchmarks.py --online   (look up missing hazard data in PubChem)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gms  # noqa: E402
from gms import benchmarks  # noqa: E402
from gms.chem import hazard, names  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--online", action="store_true")
    a = ap.parse_args()
    names.RESOLVER.online = a.online
    store = hazard.HazardStore(online=a.online)
    results = benchmarks.run(store=store, online=a.online)
    out = benchmarks.CASES_DIR.parent / "RESULTS.md"
    out.write_text(benchmarks.to_markdown(results, gms.__version__) + "\n", encoding="utf-8")
    c = benchmarks.counts(results)
    print(f"{c['total']} known alternatives: {c['suggested']} suggested ({c['top3']} in the first three), "
          f"{c['rejected']} rejected by the check, {c['not generated']} not generated. Wrote {out}")


if __name__ == "__main__":
    main()
