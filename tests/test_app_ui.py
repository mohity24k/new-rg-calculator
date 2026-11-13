"""End-to-end tests of the user interface with Streamlit's AppTest (no browser).

The extraction model is replaced by a local stub server. No test uses the network."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from gms import feedback, settings
from gms.extraction import service
from tests.test_dataset_extraction import INTRO, PROCEDURE_TEXT, Stub, make_pdf

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
PAGES = ["reaction", "scorecard", "diagnosis", "alternatives", "compare", "hazard_ratios", "about", "admin"]
EXAMPLES = {"example-amide-hatu-dmf": ("Amide coupling", "ethyl acetate in place of DMF"),
            "example-suzuki-dioxane": ("Suzuki-Miyaura coupling", "isopropanol/water (3:1 v/v) in place of 1,4-dioxane"),
            "example-redam-dce": ("Reductive amination", "ethyl acetate in place of 1,2-dichloroethane")}


@pytest.fixture(autouse=True)
def clean_state():
    service.reset_status_cache()
    yield
    service.reset_status_cache()


def start() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180).run()
    assert not at.exception, at.exception
    return at


def goto(at: AppTest, page: str) -> AppTest:
    at.switch_page(f"ui/pages/{page}.py").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def text(at) -> str:
    """Everything a user can read on the page."""
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success", "title", "subheader"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [e.label for e in at.expander]
    return "\n".join(parts)


def analyse_example(at: AppTest, example: str) -> AppTest:
    sel = next(s for s in at.selectbox if s.label == "Example reaction")
    sel.set_value(example).run()
    button(at, "Load example").click().run()
    button(at, "Analyse").click().run()
    assert not at.exception and not at.error, [e.value for e in at.error]
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_without_a_reaction(page):
    at = goto(start(), page)
    if page in ("scorecard", "diagnosis", "alternatives", "compare"):
        assert any("No reaction has been analysed yet" in i.value for i in at.info)


@pytest.mark.parametrize("example", sorted(EXAMPLES))
def test_main_flow_for_each_reaction_class(example):
    """enter -> score -> diagnose -> suggest -> check -> compare -> export -> feedback."""
    cls, expected = EXAMPLES[example]
    at = analyse_example(start(), example)
    assert any("Analysed" in s.value for s in at.success)
    assert f"**Reaction class:** {cls}" in text(at)

    goto(at, "scorecard")
    page = text(at)
    assert "Not green" in page and "Main reasons against" in page and "provisional" in page
    assert "Process mass intensity (PMI)" in page and "EcoScale" in page and "Hazard data of the materials" in page
    assert "Compared with similar reactions" in page and "No PMI percentile is shown" in page

    goto(at, "diagnosis")
    page = text(at)
    assert "is a high-hazard material" in page and "What if" in page and "idealised" in page

    goto(at, "alternatives")
    page = text(at)
    assert "not a claim that the yields are equal" in page and "does not predict the yield" in page
    assert expected in page and "No-regression check: what was compared" in page
    assert "Considered, but not suggested" in page and "doi.org" in page and "Break-even" not in at.error
    a = at.session_state["analysis"]
    shown = next(c for c in a["candidates"] if c.title == expected)
    assert shown.status == "passed"
    at.text_area(key=f"comment_{shown.id}").set_value("we would try this first").run()
    at.button(key=f"accept_{shown.id}").click().run()
    assert not at.exception
    assert at.session_state["decisions"][shown.id] == {"decision": "accept", "comment": "we would try this first"}
    rows = feedback.rows(settings.load().data_dir / "feedback.db")
    assert rows[-1]["decision"] == "accept" and rows[-1]["title"] == expected and rows[-1]["comment"]
    at.slider(key=f"yield_{shown.id}").set_value(60).run()
    assert at.session_state["scenario_yields"][shown.id] == 0.60
    assert any("Alternative at 60% yield" in d.value.columns for d in at.dataframe)

    goto(at, "compare")
    assert [d.label for d in at.get("download_button")] == ["Download PDF", "Download CSV", "Download JSON"]
    ms = at.multiselect[0]
    assert shown.id in ms.value                                   # the accepted alternative is preselected
    table = at.dataframe[0].value
    assert list(table.columns) == ["Metric", "Original", expected] and "Verdict" in list(table["Metric"])


def test_invalid_entry_shows_plain_messages_and_keeps_the_app_alive():
    at = start()
    button(at, "Clear the form").click().run()
    button(at, "Analyse").click().run()
    assert not at.exception
    assert any("exactly one product" in e.value for e in at.error) or any("name is missing" in e.value for e in at.error)
    assert at.session_state["reaction"] is None


def test_reaction_outside_the_supported_classes_still_gets_a_score():
    at = analyse_example(start(), "example-amide-hatu-dmf")
    r = dict(at.session_state["reaction"])
    r["components"] = [c for c in r["components"] if c["name"] not in ("benzylamine", "HATU")]   # no class
    r["reaction_class"] = None
    at.session_state["reaction"] = r
    at.session_state["analysis"] = None
    at.run()
    goto(at, "alternatives")
    assert any("class was not recognised" in w.value for w in at.warning)
    goto(at, "scorecard")
    assert any("No comparison data for this reaction class" in i.value for i in at.info)


# --- extraction: server-side settings, offline as a normal state ------------------------------

def test_no_endpoint_or_key_field_and_manual_entry_works_without_extraction():
    """The public app must not accept a server URL or show a key (SSRF, key leak)."""
    at = start()
    for page in PAGES:
        goto(at, page)
        labels = [w.label.lower() for w in list(at.text_input) + list(at.text_area)]
        assert not any(k in l for l in labels for k in ("endpoint", "url", "api key", "model name")), (page, labels)
    goto(at, "reaction")
    assert any("not set up on this server" in i.value for i in at.info)
    analyse_example(at, "example-redam-dce")                       # everything else works


def test_offline_server_is_a_normal_state(monkeypatch):
    monkeypatch.setenv("GMS_VLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("GMS_VLM_API_KEY", "very-secret-key")
    at = start()
    assert any("offline at the moment" in i.value and "normal" in i.value for i in at.info)
    assert not at.error and "Check again" in [b.label for b in at.button]
    assert "PDF extraction: offline" in text(at)
    for page in PAGES:
        goto(at, page)
        assert "very-secret-key" not in text(at) and "127.0.0.1" not in text(at)
    goto(at, "reaction")
    analyse_example(at, "example-suzuki-dioxane")


def test_extraction_flow_with_a_stub_server(monkeypatch):
    data = make_pdf([INTRO, PROCEDURE_TEXT])

    class Upload:
        name = "si.pdf"

        def getvalue(self):
            return data

    import streamlit as st
    real = st.file_uploader
    monkeypatch.setattr(st, "file_uploader", lambda label, *a, **k: Upload() if label == "PDF" else None)
    stub = Stub(delay=0)
    try:
        monkeypatch.setenv("GMS_VLM_BASE_URL", stub.url)
        monkeypatch.setenv("GMS_VLM_API_KEY", "very-secret-key")
        at = start()
        assert "PDF extraction: available" in text(at)
        assert stub.keys[-1] == "Bearer very-secret-key"           # the key goes to the model server only
        button(at, "Extract reactions").click().run()
        assert not at.exception and not at.error
        assert stub.calls == 1                                      # the introduction page is not sent
        sel = next(s for s in at.selectbox if s.label == "Reaction to review")
        assert len(sel.options) == 2 and any("inherited from general procedure" in w.value for w in at.warning)
        button(at, "Put this reaction into the form for review").click().run()
        assert any("extracted from a PDF by a model" in w.value and "si.pdf" in w.value for w in at.warning)
        assert at.session_state["reaction"] is None                 # nothing is scored before the review
        button(at, "Analyse").click().run()
        assert not at.exception and not at.error
        a = at.session_state["analysis"]
        assert a["reaction"].product.name == "4-methylbiphenyl" and a["reaction"].source["entry"] == "pdf"
        assert a["reaction"].reaction_class == "suzuki_miyaura"
        assert {c.role for c in a["reaction"].components} >= {"reaction_solvent", "workup", "catalyst", "base"}
        assert "very-secret-key" not in text(at) and stub.url not in text(at)
    finally:
        stub.close()
        monkeypatch.setattr(st, "file_uploader", real)


def test_feedback_download_needs_the_server_side_token(monkeypatch):
    at = goto(start(), "admin")
    assert any("switched off" in i.value for i in at.info) and not at.get("download_button")
    monkeypatch.setenv("GMS_ADMIN_TOKEN", "tok-123")
    at = goto(start(), "admin")
    at.text_input[0].set_value("wrong").run()
    assert any("not correct" in e.value for e in at.error) and not at.get("download_button")
    at.text_input[0].set_value("tok-123").run()
    assert len(at.get("download_button")) == 1


def test_json_report_can_be_loaded_again():
    from gms import report, scoring, examples
    r, s, _ = scoring.prepare_and_score(examples.load("example-redam-dce"), online=False)
    rep = json.loads(report.to_json(report.build(r, s)))
    from gms import forms
    from gms.model import Reaction
    again = Reaction.from_dict(rep["reaction"])
    r2, problems = forms.reaction_from_rows(forms.rows_from_reaction(again), forms.meta_from_reaction(again))
    assert problems == [] and r2.product.name == r.product.name
