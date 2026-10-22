"""End-to-end UI tests with Streamlit's AppTest (no browser needed).

The vision model is replaced by a tiny local OpenAI-compatible server that returns the
answer a model gives for a Suzuki coupling procedure."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")

ANSWER = json.dumps({"components": [
    {"name": "4-bromoanisole", "role": "reactant", "quantity": 187, "unit": "mg", "equivalents": 1},
    {"name": "phenylboronic acid", "role": "reactant", "quantity": 1.5, "unit": "equiv"},
    {"name": "Pd(PPh3)4", "role": "catalyst", "quantity": 5, "unit": "mol%"},
    {"name": "K2CO3", "role": "base", "quantity": 276, "unit": "mg"},
    {"name": "1,4-dioxane", "role": "solvent", "quantity": 4, "unit": "mL"},
    {"name": "water", "role": "solvent", "quantity": 1, "unit": "mL"},
    {"name": "4-methoxybiphenyl", "role": "product", "quantity": 166, "unit": "mg"}],
    "yield_percent": 90, "conditions": {"temperature_c": 90, "time_h": 12, "atmosphere": "argon",
                                        "workup": ["extraction", "column chromatography"]}})


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"choices": [{"message": {"content": ANSWER}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def vlm_url():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def goto(at, page):
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
    return at


def button(at, label):
    return next(b for b in at.button if label in b.label)


@pytest.mark.parametrize("page", ["Dashboard Overview", "Upload PDF & Auto-fill", "Mass-Based Metrics Calculator",
                                  "Reaction Eco-Scale Scoring", "Global Hazard & Toxicity Impact",
                                  "Process Comparison & Export"])
def test_every_page_renders(page):
    goto(AppTest.from_file(APP, default_timeout=60).run(), page)


def test_mass_page_default_example_and_results_persist():
    at = goto(AppTest.from_file(APP, default_timeout=60).run(), "Mass-Based Metrics Calculator")
    button(at, "Calculate Mass-Based Metrics").click().run()
    r = at.session_state.routes["Route A"]["mass_metrics"]
    assert round(r["Atom Economy (AE, %)"]) == 88 and round(r["Carbon Efficiency (CE, %)"], 1) == 83.7
    at.text_input[0].set_value("renamed").run()          # any other interaction
    assert any("Showing the last calculation" in s.value for s in at.success)
    assert not at.warning                                 # the paper example is consistent


def test_mass_page_zero_product_mass_warns_instead_of_crashing():
    at = goto(AppTest.from_file(APP, default_timeout=60).run(), "Mass-Based Metrics Calculator")
    next(n for n in at.number_input if n.label.startswith("Actual isolated mass")).set_value(0.0).run()
    button(at, "Calculate Mass-Based Metrics").click().run()
    assert not at.exception
    assert any("Product mass is 0" in w.value for w in at.warning)


def test_eco_page_default_and_hazard_page():
    at = goto(AppTest.from_file(APP, default_timeout=60).run(), "Reaction Eco-Scale Scoring")
    button(at, "Calculate Eco-Scale").click().run()
    assert at.session_state.routes["Route A"]["eco_scale"]["Eco-Scale Score"] == 95   # 90 % yield only
    goto(at, "Global Hazard & Toxicity Impact")
    button(at, "Calculate Hazard").click().run()
    hz = at.session_state.routes["Route A"]["hazard_metrics"]
    assert hz["Air Toxicity (Y_AT)"] == 1 and hz["Bioaccumulation Potential"] == "Low"


def test_comparison_and_export_with_infinite_values():
    at = AppTest.from_file(APP, default_timeout=60).run()
    goto(at, "Mass-Based Metrics Calculator")
    next(n for n in at.number_input if n.label.startswith("Actual isolated mass")).set_value(0.0).run()
    button(at, "Calculate Mass-Based Metrics").click().run()
    goto(at, "Process Comparison & Export")
    assert len(at.get("download_button")) == 2
    goto(at, "Dashboard Overview")


def test_autofill_flow_end_to_end(vlm_url, monkeypatch):
    """Upload a PDF, extract 2 pages, review, apply, then calculate on the other pages."""
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Experimental page {i + 1}")
    data = doc.tobytes()

    class Upload:
        name = "paper.pdf"

        def getvalue(self):
            return data

    import streamlit as st
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload())
    at = goto(AppTest.from_file(APP, default_timeout=120).run(), "Upload PDF & Auto-fill")
    at.text_input[0].set_value(vlm_url).run()
    next(n for n in at.number_input if n.label == "Last page").set_value(2).run()
    button(at, "Extract").click().run()
    assert not at.exception and not at.error
    at.checkbox[0].check().run()
    button(at, "Apply to calculators").click().run()
    pf = at.session_state.prefill
    assert pf["product"]["name"] == "4-methoxybiphenyl"
    assert pf["eco"]["workup_steps"] == ["Liquid-liquid extraction (incl. drying/filtration of desiccant)",
                                         "Classical chromatography"]
    assert "Pd(PPh3)4" in pf["compounds"]

    goto(at, "Mass-Based Metrics Calculator")
    button(at, "Calculate Mass-Based Metrics").click().run()
    r = at.session_state.routes["Route A"]["mass_metrics"]
    assert r["Percentage Yield (%)"] == pytest.approx(90.1, abs=0.2)
    assert r["Water Mass (g)"] == pytest.approx(1.0)

    goto(at, "Reaction Eco-Scale Scoring")
    button(at, "Calculate Eco-Scale").click().run()
    eco = at.session_state.routes["Route A"]["eco_scale"]
    # yield 5 + inert gas 1 + heating > 1 h 3 + extraction 3 + chromatography 10 = 22
    assert eco["Total Penalty"] == 22


def test_autofill_multi_reaction_mode(monkeypatch):
    """New mode: general procedure + substrate entry come back as separate reactions; the
    selected one goes through the same review and apply steps as the classic mode."""
    from tests.test_dataset_extraction import INTRO, PROCEDURE_TEXT, Stub, make_pdf
    data = make_pdf([INTRO, PROCEDURE_TEXT])

    class Upload:
        name = "si.pdf"

        def getvalue(self):
            return data

    import streamlit as st
    monkeypatch.setattr(st, "file_uploader", lambda *a, **k: Upload())
    stub = Stub(delay=0)
    try:
        at = goto(AppTest.from_file(APP, default_timeout=120).run(), "Upload PDF & Auto-fill")
        at.text_input[0].set_value(stub.url).run()
        next(r for r in at.radio if r.label == "Extraction mode").set_value(
            "Several reactions per document (new)").run()
        button(at, "Extract").click().run()
        assert not at.exception and not at.error
        assert stub.calls == 1                                  # the intro page is not sent
        sel = next(s for s in at.selectbox if s.label == "Reaction to review")
        assert sel.options[0].startswith("3b: 4-methylbiphenyl (83%)")     # specific reaction first
        assert any(o.startswith("General procedure A") for o in sel.options)
        at.checkbox[0].check().run()
        button(at, "Apply to calculators").click().run()
        pf = at.session_state.prefill
        assert pf["product"]["name"] == "4-methylbiphenyl"
        assert "1,4-dioxane" in pf["compounds"] and "phenylboronic acid" in list(pf["reactants_df"]["Name"])
        assert "Classical chromatography" in pf["eco"]["workup_steps"]
    finally:
        stub.close()
