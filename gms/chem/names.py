"""Chemical names: abbreviations, clean-up of names as printed in papers, and the single
name -> structure resolver (alias table -> OPSIN -> PubChem) with its one cache."""

from __future__ import annotations

import json
import re
import threading
import warnings
from pathlib import Path
from typing import Optional

import requests

from gms.chem.structure import canon

ALIASES = {
    "dmso": "CS(C)=O", "dimethyl sulfoxide": "CS(C)=O",
    "dmap": "CN(C)c1ccncc1", "4-dimethylaminopyridine": "CN(C)c1ccncc1",
    "dipea": "CCN(C(C)C)C(C)C", "etn(ipr)2": "CCN(C(C)C)C(C)C",
    "n,n-diisopropylethylamine": "CCN(C(C)C)C(C)C", "diisopropylethylamine": "CCN(C(C)C)C(C)C",
    "bz2o": "O=C(OC(=O)c1ccccc1)c1ccccc1", "benzoic anhydride": "O=C(OC(=O)c1ccccc1)c1ccccc1",
    "dcm": "ClCCl", "dichloromethane": "ClCCl",
    "thf": "C1CCOC1", "tetrahydrofuran": "C1CCOC1",
    "etoac": "CCOC(C)=O", "ethyl acetate": "CCOC(C)=O",
    "et2o": "CCOCC", "diethyl ether": "CCOCC",
    "hexane": "CCCCCC", "hexanes": "CCCCCC",
    "mecn": "CC#N", "acetonitrile": "CC#N", "meoh": "CO", "methanol": "CO",
    "dmf": "CN(C)C=O", "toluene": "Cc1ccccc1",
    "et3n": "CCN(CC)CC", "triethylamine": "CCN(CC)CC",
    "formic acid": "OC=O", "hco2h": "OC=O",
    "n,n-dimethylformamide": "CN(C)C=O", "dimethylformamide": "CN(C)C=O",
    "hobt": "On1nnc2ccccc21", "1-hydroxybenzotriazole": "On1nnc2ccccc21",
    "edc\u00b7hcl": "CCN=C=NCCCN(C)C.Cl", "edc.hcl": "CCN=C=NCCCN(C)C.Cl",
    "edc hcl": "CCN=C=NCCCN(C)C.Cl", "edc-hcl": "CCN=C=NCCCN(C)C.Cl",
    "edci": "CCN=C=NCCCN(C)C.Cl", "edc hydrochloride": "CCN=C=NCCCN(C)C.Cl",
    # common solvents (so densities apply to any synonym that resolves to the same structure)
    "water": "O", "h2o": "O", "ethanol": "CCO", "etoh": "CCO",
    "isopropanol": "CC(C)O", "2-propanol": "CC(C)O", "ipa": "CC(C)O", "iproh": "CC(C)O",
    "acetone": "CC(C)=O", "chloroform": "ClC(Cl)Cl", "chcl3": "ClC(Cl)Cl",
    "1,2-dichloroethane": "ClCCCl", "dce": "ClCCCl", "1,4-dioxane": "C1COCCO1", "dioxane": "C1COCCO1",
    "pyridine": "c1ccncc1", "benzene": "c1ccccc1", "heptane": "CCCCCCC", "n-heptane": "CCCCCCC",
    "pentane": "CCCCC", "n-pentane": "CCCCC", "n-hexane": "CCCCCC", "cyclohexane": "C1CCCCC1",
    "petroleum ether": "CCCCCC",
    "dma": "CN(C)C(C)=O", "dmac": "CN(C)C(C)=O", "n,n-dimethylacetamide": "CN(C)C(C)=O",
    "nmp": "CN1CCCC1=O", "n-methyl-2-pyrrolidone": "CN1CCCC1=O",
    "mtbe": "COC(C)(C)C", "methyl tert-butyl ether": "COC(C)(C)C",
    "2-methf": "CC1CCCO1", "2-methyltetrahydrofuran": "CC1CCCO1",
    "acetic acid": "CC(=O)O", "acoh": "CC(=O)O", "hoac": "CC(=O)O",
    "tfa": "OC(=O)C(F)(F)F", "trifluoroacetic acid": "OC(=O)C(F)(F)F",
    "tfe": "OCC(F)(F)F", "2,2,2-trifluoroethanol": "OCC(F)(F)F",
    "hfip": "OC(C(F)(F)F)C(F)(F)F", "hexafluoroisopropanol": "OC(C(F)(F)F)C(F)(F)F",
    "carbon tetrachloride": "ClC(Cl)(Cl)Cl", "ccl4": "ClC(Cl)(Cl)Cl",
    "chlorobenzene": "Clc1ccccc1", "phcl": "Clc1ccccc1", "xylene": "Cc1ccccc1C", "xylenes": "Cc1ccccc1C",
    "o-xylene": "Cc1ccccc1C", "dme": "COCCOC", "1,2-dimethoxyethane": "COCCOC",
    "t-buoh": "CC(C)(C)O", "tert-butanol": "CC(C)(C)O", "n-butanol": "CCCCO", "1-butanol": "CCCCO",
    "nitromethane": "C[N+](=O)[O-]", "meno2": "C[N+](=O)[O-]",
    "pyrrolidine": "C1CCNC1", "piperidine": "C1CCNCC1", "morpholine": "C1COCCN1",
    # common inorganic / organometallic reagents (no IUPAC name, not parsable by OPSIN)
    "nahco3": "[Na+].OC([O-])=O", "sodium bicarbonate": "[Na+].OC([O-])=O",
    "sodium hydrogen carbonate": "[Na+].OC([O-])=O",
    "na2co3": "[Na+].[Na+].[O-]C([O-])=O", "sodium carbonate": "[Na+].[Na+].[O-]C([O-])=O",
    "k2co3": "[K+].[K+].[O-]C([O-])=O", "potassium carbonate": "[K+].[K+].[O-]C([O-])=O",
    "cs2co3": "[Cs+].[Cs+].[O-]C([O-])=O", "cesium carbonate": "[Cs+].[Cs+].[O-]C([O-])=O",
    "caesium carbonate": "[Cs+].[Cs+].[O-]C([O-])=O",
    "k3po4": "[K+].[K+].[K+].[O-]P([O-])([O-])=O", "potassium phosphate": "[K+].[K+].[K+].[O-]P([O-])([O-])=O",
    "naoh": "[Na+].[OH-]", "sodium hydroxide": "[Na+].[OH-]", "koh": "[K+].[OH-]",
    "potassium hydroxide": "[K+].[OH-]", "lioh": "[Li+].[OH-]", "lithium hydroxide": "[Li+].[OH-]",
    "nah": "[Na+].[H-]", "sodium hydride": "[Na+].[H-]",
    "kotbu": "CC(C)(C)[O-].[K+]", "t-buok": "CC(C)(C)[O-].[K+]", "potassium tert-butoxide": "CC(C)(C)[O-].[K+]",
    "naotbu": "CC(C)(C)[O-].[Na+]", "sodium tert-butoxide": "CC(C)(C)[O-].[Na+]",
    "hcl": "Cl", "hydrochloric acid": "Cl", "hbr": "Br", "h2so4": "OS(=O)(=O)O", "sulfuric acid": "OS(=O)(=O)O",
    "nacl": "[Na+].[Cl-]", "sodium chloride": "[Na+].[Cl-]", "nh4cl": "[NH4+].[Cl-]",
    "ammonium chloride": "[NH4+].[Cl-]", "licl": "[Li+].[Cl-]", "lithium chloride": "[Li+].[Cl-]",
    "na2so4": "[Na+].[Na+].[O-]S([O-])(=O)=O", "sodium sulfate": "[Na+].[Na+].[O-]S([O-])(=O)=O",
    "mgso4": "[Mg+2].[O-]S([O-])(=O)=O", "magnesium sulfate": "[Mg+2].[O-]S([O-])(=O)=O",
    "na2s2o3": "[Na+].[Na+].[O-]S([O-])(=O)=S", "sodium thiosulfate": "[Na+].[Na+].[O-]S([O-])(=O)=S",
    "nabh4": "[Na+].[BH4-]", "sodium borohydride": "[Na+].[BH4-]",
    "lialh4": "[Li+].[AlH4-]", "lithium aluminium hydride": "[Li+].[AlH4-]",
    "lithium aluminum hydride": "[Li+].[AlH4-]",
    "nabh(oac)3": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]", "stab": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]",
    "sodium triacetoxyborohydride": "CC(=O)O[BH-](OC(C)=O)OC(C)=O.[Na+]",
    "pd(oac)2": "CC(=O)[O-].CC(=O)[O-].[Pd+2]", "palladium(ii) acetate": "CC(=O)[O-].CC(=O)[O-].[Pd+2]",
    "palladium acetate": "CC(=O)[O-].CC(=O)[O-].[Pd+2]",
    "pd(pph3)4": "c1ccc(P(c2ccccc2)c2ccccc2)cc1.c1ccc(P(c2ccccc2)c2ccccc2)cc1."
                 "c1ccc(P(c2ccccc2)c2ccccc2)cc1.c1ccc(P(c2ccccc2)c2ccccc2)cc1.[Pd]",
    "pdcl2": "Cl[Pd]Cl", "pd/c": "[Pd]", "pd(dppf)cl2": "Cl[Pd]Cl.[Fe].c1ccc(P(c2ccccc2)[c-]2cccc2)cc1."
                                                        "c1ccc(P(c2ccccc2)[c-]2cccc2)cc1",
    "pph3": "c1ccc(P(c2ccccc2)c2ccccc2)cc1", "triphenylphosphine": "c1ccc(P(c2ccccc2)c2ccccc2)cc1",
    "cui": "[Cu]I", "copper(i) iodide": "[Cu]I", "cucl": "[Cu]Cl", "cubr": "[Cu]Br",
    "cu(oac)2": "CC(=O)[O-].CC(=O)[O-].[Cu+2]", "cuso4": "[Cu+2].[O-]S([O-])(=O)=O",
    "zncl2": "Cl[Zn]Cl", "fecl3": "Cl[Fe](Cl)Cl", "alcl3": "Cl[Al](Cl)Cl", "ticl4": "Cl[Ti](Cl)(Cl)Cl",
    "bf3·oet2": "B(F)(F)F.CCOCC", "bf3.oet2": "B(F)(F)F.CCOCC", "bf3 oet2": "B(F)(F)F.CCOCC",
    "nbs": "O=C1CCC(=O)N1Br", "n-bromosuccinimide": "O=C1CCC(=O)N1Br",
    "nis": "O=C1CCC(=O)N1I", "ncs": "O=C1CCC(=O)N1Cl", "i2": "II", "iodine": "II", "br2": "BrBr",
    "mcpba": "O=C(OO)c1cccc(Cl)c1", "m-cpba": "O=C(OO)c1cccc(Cl)c1",
    "dbu": "C1CCC2=NCCCN2CC1", "dabco": "C1CN2CCN1CC2", "tbab": "CCCC[N+](CCCC)(CCCC)CCCC.[Br-]",
    "tbaf": "CCCC[N+](CCCC)(CCCC)CCCC.[F-]",
    "boc2o": "CC(C)(C)OC(=O)OC(=O)OC(C)(C)C", "di-tert-butyl dicarbonate": "CC(C)(C)OC(=O)OC(=O)OC(C)(C)C",
    "ac2o": "CC(=O)OC(C)=O", "acetic anhydride": "CC(=O)OC(C)=O",
    "tscl": "Cc1ccc(S(=O)(=O)Cl)cc1", "mscl": "CS(=O)(=O)Cl",
    "dcc": "C1CCC(N=C=NC2CCCCC2)CC1", "hatu": "CN(C)C(=[N+](C)C)On1nnc2cccnc21.F[P-](F)(F)(F)(F)F",
    "tempo": "CC1(C)CCCC(C)(C)N1[O]", "h2o2": "OO", "hydrogen peroxide": "OO",
    "aibn": "CC(C)(C#N)N=NC(C)(C)C#N",
}


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower()).strip()


QUALIFIER = re.compile(
    r"\s*\((?:product|products|extraction|solvent|reagent|substrate|catalyst|base|crude|"
    r"anhydrous[^()]*|\d+[a-z]?|[^()]*%[^()]*|[^()]*\d{2,7}-\d{2}-\d[^()]*|"
    r"[^()]*(?:received|combi|sigma|aldrich|purified)[^()]*)\)\s*$", re.I)

GENERIC = {"product", "products", "compound", "compounds", "substrate", "substrates",
            "reagent", "reagents", "solvent", "catalyst", "base", "additive", "crude product",
            "starting material", "mixture", "residue"}

SYNONYMS = [(r"\bp-toluene", "4-toluene"), (r"toluenesulfonate", "methylbenzenesulfonate"),
             (r"toluenesulfonyl", "methylbenzenesulfonyl"), (r"toluenesulfonic", "methylbenzenesulfonic")]


WATER_RE = re.compile(r"\b(water|brine|h2o|d2o|di water|deionized water|distilled water)\b", re.I)
AQUEOUS_RE = re.compile(r"\b(aqueous|aq\.?|sat(urated|d)?\.?)(?=\W|$)", re.I)
DRYING_RE = re.compile(r"\b(sodium sulfate|magnesium sulfate|na2so4|mgso4|calcium chloride|cacl2)\b", re.I)


def clean(name: str) -> str:
    """Strip trailing qualifiers: '(product)', '(3a)', '(99.5%)', '(extraction)', CAS, vendor..."""
    n, prev = name.strip(), None
    while prev != n:
        prev = n
        n = QUALIFIER.sub("", n).strip(" ;,")
    return re.sub(r"\s+", " ", n)


def variants(name: str) -> list[str]:
    c = clean(name)
    s = c
    for pat, rep in SYNONYMS:
        s = re.sub(pat, rep, s, flags=re.I)
    return list(dict.fromkeys(x for x in (name, c, s) if x))


def opsin(name: str) -> Optional[str]:
    try:
        from py2opsin import py2opsin  # pip install py2opsin (needs a Java runtime)
        with warnings.catch_warnings():         # OPSIN warns on every name it cannot parse
            warnings.simplefilter("ignore")
            s = py2opsin(name)
        return s or None
    except Exception:
        return None


class LookupFailed(Exception):
    """Transient failure (network, rate limit): the result must not be cached."""


def pubchem_smiles(name: str) -> Optional[str]:
    # POST keeps names with '/', '#', '?' or ',' intact (they break a URL path).
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/property/"
           "SMILES,ConnectivitySMILES,CanonicalSMILES/JSON")
    try:
        r = requests.post(url, data={"name": name}, timeout=10)
    except requests.RequestException as e:
        raise LookupFailed(str(e)) from e
    if r.status_code in (400, 404):             # PubChem does not know this name
        return None
    if not r.ok:
        raise LookupFailed(f"HTTP {r.status_code}")
    try:
        p = r.json()["PropertyTable"]["Properties"][0]
    except (ValueError, KeyError, IndexError):
        return None
    return p.get("SMILES") or p.get("CanonicalSMILES") or p.get("ConnectivitySMILES")



# ---------------------------------------------------------------------------
# The resolver (one for the whole application) and its cache
# ---------------------------------------------------------------------------

class Resolver:
    """Name -> canonical SMILES via the alias table, OPSIN, then PubChem.

    One instance (`RESOLVER`) serves the app, the dataset pipeline and the scripts. Results are
    cached by name, including definitive misses. A lookup that failed for a transient reason
    (network, rate limit) is not cached, so it is retried later. `online=False` skips PubChem.
    """

    def __init__(self):
        self.cache: dict[str, Optional[str]] = {}
        self.online = True
        self._lock = threading.Lock()

    def load(self, path) -> None:
        p = Path(path)
        if p.exists():
            with self._lock:
                self.cache.update(json.loads(p.read_text(encoding="utf-8")))

    def save(self, path) -> None:
        with self._lock:
            data = json.dumps(self.cache, ensure_ascii=False, indent=0, sort_keys=True)
        Path(path).write_text(data, encoding="utf-8")

    def resolve(self, name: str, online: Optional[bool] = None) -> Optional[str]:
        online = self.online if online is None else online
        name = str(name or "").strip()
        if not name:
            return None
        if name in self.cache and (self.cache[name] is not None or online):
            return self.cache[name]
        for cand in variants(name):
            if norm(cand) in ALIASES:
                return self._store(name, canon(ALIASES[norm(cand)]))
        transient = False
        for cand in variants(name):
            s = opsin(cand)
            if not s and online:
                try:
                    s = pubchem_smiles(cand)
                except LookupFailed:
                    transient = True
            s = canon(s) if s else None
            if s:
                return self._store(name, s)
        if online and not transient:
            self._store(name, None)          # definitive miss: do not ask again
        return None

    def _store(self, name: str, smiles: Optional[str]) -> Optional[str]:
        with self._lock:
            self.cache[name] = smiles
        return smiles


RESOLVER = Resolver()


def resolve_smiles(name: str, online: Optional[bool] = None) -> Optional[str]:
    """Canonical SMILES for a chemical name, or None."""
    return RESOLVER.resolve(name, online)
