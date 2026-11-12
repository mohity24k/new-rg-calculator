"""Shared test setup: no test may reach the network.

PubChem is replaced by a stub that fails the test if it is called without an explicit fake,
and the name cache is emptied between tests."""

import pytest

from gms.chem import names


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(name):
        raise names.LookupFailed("network access is disabled in tests")

    monkeypatch.setattr(names, "pubchem_smiles", blocked)
    names.RESOLVER.cache.clear()
    yield
