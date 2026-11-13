"""Regression lock: the calculators must reproduce the outputs recorded before the v1
restructuring (tests/golden/metrics_baseline.json, produced by the code at commit d2513eb).

A failure here means a formula changed. Either fix the code, or - for a deliberate change -
add a test that shows why, regenerate the baseline and record the change in docs/PROGRESS.md.
"""
import json
from pathlib import Path

from tests.golden import make_baseline

BASELINE = json.loads(Path(__file__).with_name("golden").joinpath("metrics_baseline.json").read_text("utf-8"))


def test_every_recorded_output_is_reproduced_exactly():
    current = json.loads(json.dumps(make_baseline.build(), sort_keys=True))
    assert current.keys() == BASELINE.keys()
    for section in BASELINE:
        assert current[section].keys() == BASELINE[section].keys(), section
        for case in BASELINE[section]:
            assert current[section][case] == BASELINE[section][case], f"{section}/{case} changed"
