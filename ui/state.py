"""Session state of the app: the reaction being analysed and the cached analysis."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import pandas as pd
import streamlit as st

from gms import diagnosis, feedback, forms, scoring, settings, suggest
from gms.chem import classify, hazard, names
from gms.model import Reaction

log = logging.getLogger("gms.ui")


@st.cache_resource
def _startup() -> bool:
    """Once per server process: logging and the name cache."""
    s = settings.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    names.RESOLVER.online = not s.offline
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        names.RESOLVER.load(s.data_dir / "name_cache.json")
    except (OSError, ValueError) as e:
        log.warning("name cache not loaded: %s", e)
    return True


def app_settings() -> settings.Settings:
    """Server-side settings (environment variables or Streamlit secrets). Never shown in the UI."""
    return settings.load()


def init() -> None:
    _startup()
    ss = st.session_state
    ss.setdefault("session_id", uuid.uuid4().hex)
    ss.setdefault("reaction", None)            # dict form of the reaction that was analysed last
    ss.setdefault("analysis", None)
    ss.setdefault("decisions", {})             # suggestion id -> {"decision", "comment"}
    ss.setdefault("scenario_yields", {})       # suggestion id -> yield fraction
    ss.setdefault("editor_version", 0)
    if "editor" not in ss:
        load_into_editor(None)


def load_into_editor(reaction: Optional[Reaction]) -> None:
    """Put a reaction (example, extraction, import) into the entry form for review."""
    ss = st.session_state
    if reaction is None:
        rows = [dict(forms.EMPTY_ROW, Limiting=True), dict(forms.EMPTY_ROW),
                dict(forms.EMPTY_ROW, Role="reaction solvent", Unit="mL"), dict(forms.EMPTY_ROW, Role="product")]
        meta = forms.meta_from_reaction(Reaction())
        meta["source"] = {"entry": "manual"}
    else:
        rows, meta = forms.rows_from_reaction(reaction), forms.meta_from_reaction(reaction)
    ss["editor"] = {"rows": pd.DataFrame(rows, columns=forms.COLUMNS), "meta": meta}
    ss["editor_version"] = ss.get("editor_version", 0) + 1


def set_reaction(reaction: Reaction) -> None:
    ss = st.session_state
    ss["reaction"] = reaction.to_dict()
    ss["analysis"] = None
    ss["decisions"] = {}
    ss["scenario_yields"] = {}


def analysis() -> Optional[dict]:
    """Score, diagnosis and suggestions for the current reaction (computed once, then cached)."""
    ss = st.session_state
    if ss.get("reaction") is None:
        return None
    key = feedback.reaction_hash(ss["reaction"])
    a = ss.get("analysis")
    if a and a["key"] == key:
        return a
    with st.spinner("Resolving structures, looking up hazard data and computing the metrics..."):
        store = hazard.store()
        reaction, score, issues = scoring.prepare_and_score(Reaction.from_dict(ss["reaction"]), store)
        detected = classify.classify(reaction)
        if reaction.reaction_class is None:
            reaction.reaction_class = detected[0]
        findings = diagnosis.diagnose(reaction, score, store)
        candidates = suggest.suggest(reaction, score, store)
    try:
        names.RESOLVER.save(app_settings().data_dir / "name_cache.json")
    except OSError:
        pass
    a = {"key": key, "reaction": reaction, "score": score, "issues": issues, "detected_class": detected,
         "findings": findings, "candidates": candidates}
    ss["analysis"] = a
    return a


def need_reaction() -> Optional[dict]:
    """The analysis, or a hint to enter a reaction first (returns None in that case)."""
    a = analysis()
    if a is None:
        st.info("No reaction has been analysed yet. Enter one on the **Reaction** page, or load an example there.")
        st.page_link("ui/pages/reaction.py", label="Go to the Reaction page", icon=":material/science:")
    return a
