"""Shared test setup: no test may reach the network.

* PubChem name lookups fail the lookup (as if offline) unless a test installs a fake.
* The hazard store is offline and uses only the bundled seed file.
* The app (Streamlit AppTest) gets its own data directory and GMS_OFFLINE=1.
"""

import os
import tempfile

os.environ["GMS_OFFLINE"] = "1"
os.environ["GMS_DATA_DIR"] = tempfile.mkdtemp(prefix="gms-test-")
os.environ.pop("GMS_VLM_BASE_URL", None)

import pytest  # noqa: E402

from gms.chem import hazard, names, pubchem  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise names.LookupFailed("network access is disabled in tests")

    monkeypatch.setattr(names, "pubchem_smiles", blocked)
    monkeypatch.setattr(pubchem, "_request", blocked)
    names.RESOLVER.cache.clear()
    hazard.set_store(hazard.HazardStore(online=False))
    yield
    hazard.set_store(None)
