"""Dataset extraction pipeline (modules/extraction): page filter, chunking, schema normalization,
general procedures, concurrent client, checkpoint/resume, enrichment and the app adapter.

The model server is a local stub, so the tests need no GPU. Structure lookups run offline
(alias table / OPSIN)."""

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymupdf
import pytest

from modules import mass_metrics as mm
from modules import pdf_extraction as px
from modules.extraction import config, engines, enrich, pages, pipeline
from modules.extraction import schema as sc

CFG = config.load_config()

PROCEDURE_TEXT = (
    "General Procedure A. An oven-dried vial was charged with the aryl bromide (0.50 mmol), "
    "phenylboronic acid (91 mg, 0.75 mmol), Pd(PPh3)4 (2 mol%) and K2CO3 (2.0 equiv). 1,4-Dioxane (2 mL) "
    "was added and the mixture was stirred at 90 °C for 12 h under argon. The mixture was extracted with "
    "EtOAc (3 x 10 mL), dried over Na2SO4 and purified by column chromatography.\n"
    "4-Methylbiphenyl (3b): prepared following General Procedure A from 4-bromotoluene. White solid "
    "(70 mg, 83% yield)."
)

GP_ANSWER = {"reactions": [
    {"label": None, "procedure_label": "A", "follows_procedure": None,
     "components": [
         {"name": "aryl bromide", "role": "reactant", "quantity": 0.5, "unit": "mmol", "mmol": None, "equivalents": 1},
         {"name": "phenylboronic acid", "role": "reactant", "quantity": 91, "unit": "mg", "mmol": 0.75, "equivalents": None},
         {"name": "Pd(PPh3)4", "role": "catalyst", "quantity": 2, "unit": "mol%", "mmol": None, "equivalents": None},
         {"name": "K2CO3", "role": "base", "quantity": 2.0, "unit": "equiv", "mmol": None, "equivalents": None},
         {"name": "1,4-dioxane", "role": "reaction_solvent", "quantity": 2, "unit": "mL", "mmol": None, "equivalents": None},
         {"name": "EtOAc", "role": "workup", "quantity": 30, "unit": "mL", "mmol": None, "equivalents": None},
         {"name": "Na2SO4", "role": "workup", "quantity": None, "unit": None, "mmol": None, "equivalents": None}],
     "yield_percent": None,
     "conditions": {"temperature_c": 90, "reflux": False, "time_h": 12, "atmosphere": "argon", "activation": None},
     "workup": ["extraction", "drying"], "purification": "column chromatography"},
    {"label": "3b", "procedure_label": None, "follows_procedure": "General Procedure A",
     "components": [
         {"name": "4-bromotoluene", "role": "reactant", "quantity": None, "unit": None, "mmol": None, "equivalents": None},
         {"name": "4-methylbiphenyl", "role": "product", "quantity": 70, "unit": "mg", "mmol": None, "equivalents": None}],
     "yield_percent": 83,
     "conditions": {"temperature_c": None, "reflux": False, "time_h": None, "atmosphere": None, "activation": None},
     "workup": [], "purification": None},
]}

TABLE_ANSWER = {"tables": [{
    "title": "Table 1. Optimization of the reaction conditions",
    "standard_conditions": "4-bromotoluene (0.5 mmol), PhB(OH)2 (0.75 mmol), base (1.0 mmol), solvent (2 mL), 12 h",
    "components": [{"name": "4-bromotoluene", "role": "reactant", "quantity": 0.5, "unit": "mmol",
                    "mmol": None, "equivalents": None}],
    "entries": [
        {"entry": "1", "solvent": "toluene", "catalyst": None, "base": "K2CO3", "reagent": None, "additive": None,
         "temperature_c": 110, "time_h": None, "yield_percent": 45, "notes": None},
        {"entry": "2", "solvent": "1,4-dioxane", "catalyst": None, "base": "K2CO3", "reagent": None, "additive": None,
         "temperature_c": 90, "time_h": None, "yield_percent": 83, "notes": "isolated"}]}]}


def make_pdf(pages_text: list[str]) -> bytes:
    doc = pymupdf.open()
    for t in pages_text:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), t, fontsize=9)
    return doc.tobytes()


INTRO = ("Introduction. Cross-coupling reactions are widely used in synthesis. " * 12)
SPECTRUM = "1H NMR (400 MHz, CDCl3) of 3b\n8 7 6 5 4 3 2 1 0 ppm"
TABLE_PAGE = ("Table 1. Optimization of the reaction conditions. entry solvent base yield 1 toluene K2CO3 45 "
              "2 dioxane K2CO3 83. Reaction conditions as stated in the footnote. " * 3)


# ---------------------------------------------------------------------------
# Page filter and chunks
# ---------------------------------------------------------------------------

def test_page_labels():
    assert pages.classify_page(PROCEDURE_TEXT, 0, CFG).label == "procedure"
    assert pages.classify_page(SPECTRUM, 0, CFG).label == "spectrum"
    assert pages.classify_page(INTRO, 0, CFG).label == "other"
    assert pages.classify_page("", 0.95, CFG).label == "scanned"
    refs = " ".join(f"({i}) A. Author et al. J. Am. Chem. Soc. 2019, 141, {i}." for i in range(20))
    assert pages.classify_page(refs, 0, CFG).label == "references"
    char = ("3c: white solid (85 mg, 90% yield). 1H NMR (400 MHz, CDCl3) δ 7.52 (d, 2H), 2.40 (s, 3H) ppm. "
            "13C NMR δ 140.1 ppm. mp 45 °C.")
    assert pages.classify_page(char, 0, CFG).label == "procedure"     # characterization data is kept
    assert pages.classify_page(TABLE_PAGE, 0, CFG).has_table


def test_plan_paper_skips_useless_pages_and_routes_tables_to_images():
    pdf = make_pdf([INTRO, PROCEDURE_TEXT, SPECTRUM, TABLE_PAGE])
    infos, units, meta = pages.plan_paper(pdf, CFG)
    assert [i.label for i in infos] == ["other", "procedure", "spectrum", "other"]
    text_units = [u for u in units if u.mode == "text"]
    assert len(text_units) == 1 and text_units[0].pages == [2]
    assert "General Procedure A" in text_units[0].text
    tables = [u for u in units if u.kind == "table"]
    assert len(tables) == 1 and tables[0].pages == [4] and tables[0].png.startswith(b"\x89PNG")
    assert meta["n_pages"] == 4 and len(meta["paper_id"]) == 12


def test_procedure_split_over_two_pages_is_one_chunk():
    first, second = PROCEDURE_TEXT[:200], PROCEDURE_TEXT[200:]
    infos, units, _ = pages.plan_paper(make_pdf([first + " mmol mL stirred °C", second]), CFG)
    text_units = [u for u in units if u.mode == "text"]
    assert len(text_units) == 1 and text_units[0].pages == [1, 2]


def test_chunks_respect_size_and_cut_before_a_new_procedure():
    blocks = [(1, "x" * 500)] * 2 + [(2, "General Procedure B. Stirred with 1 mmol."), (2, "y" * 1200)]
    chunks = pages.chunk_blocks(blocks, 2000)
    assert len(chunks) == 2 and all(sum(len(b) + 2 for _, b in c) <= 2000 for c in chunks)
    many = pages.chunk_blocks([(1, "z" * 900)] * 7, 2000)
    assert all(sum(len(b) + 2 for _, b in c) <= 2000 for c in many) and sum(map(len, many)) == 7
    assert any(c[0][1].startswith("General Procedure B") for c in chunks)
    assert [b for c in chunks for b in c] == blocks            # nothing lost or reordered


# ---------------------------------------------------------------------------
# Normalization and general procedures
# ---------------------------------------------------------------------------

def test_roles_units_and_stages():
    norm = lambda **c: sc.normalize_component({"quantity": None, "unit": None, **c})
    assert norm(name="brine", role="workup", quantity=20, unit="mL")["role"] == "water"
    w = norm(name="H2O", role="reaction_solvent", quantity=1, unit="mL")
    assert (w["role"], w["stage"]) == ("water", "reaction")
    assert norm(name="silica gel", role="ignore")["role"] == "purification"
    assert norm(name="hexane/EtOAc", role="purification", quantity=200, unit="mL")["stage"] == "purification"
    assert norm(name="MgSO4", role="additive")["role"] == "additive"   # an additive in the reaction stays one
    assert norm(name="MgSO4", role="workup")["role"] == "workup"
    assert norm(name="TLC plate", role="workup")["role"] == "ignore"
    assert norm(name="EDC·HCl", role="coupling reagent", quantity=1.2, unit="equiv")["role"] == "reagent"
    cat = norm(name="Pd(OAc)2", role="catalyst", quantity=5, unit="mol%")
    assert cat["equiv"] == pytest.approx(0.05) and cat["quantity"] is None
    sub = norm(name="aniline", role="reactant", mmol=0.5)
    assert (sub["quantity"], sub["unit"]) == (0.5, "mmol")
    assert norm(name="aryl bromide", role="reactant")["generic"]
    assert norm(name="3a", role="product")["generic"]
    assert not norm(name="4-bromotoluene", role="reactant")["generic"]


def test_general_procedure_is_inherited_without_changing_the_inputs():
    rx = sc.reactions_from_answer(GP_ANSWER)
    before = copy.deepcopy(rx)
    out = sc.resolve_general_procedures(rx)
    assert rx == before                                         # inputs untouched
    r3b = next(r for r in out if r["label"] == "3b")
    names = {c["name"]: c for c in r3b["components"]}
    assert names["4-bromotoluene"]["quantity"] == 0.5 and names["4-bromotoluene"]["unit"] == "mmol"
    assert "aryl bromide" not in names                          # replaced by the specific substrate
    assert "phenylboronic acid" in names and "1,4-dioxane" in names and "K2CO3" in names
    assert r3b["conditions"]["temperature_c"] == 90 and r3b["purification"] == "column chromatography"
    assert any(f["code"] == "inherited_from_general_procedure" for f in r3b["flags"])
    assert next(c for c in r3b["components"] if c["role"] == "product")["quantity"] == 70


def test_missing_general_procedure_is_flagged():
    ans = copy.deepcopy(GP_ANSWER)
    ans["reactions"][1]["follows_procedure"] = "GP C"
    out = sc.resolve_general_procedures(sc.reactions_from_answer(ans))
    r3b = next(r for r in out if r["label"] == "3b")
    assert any(f["code"] == "general_procedure_not_found" for f in r3b["flags"])


def test_duplicates_from_main_text_and_si_are_merged():
    one = sc.reactions_from_answer(GP_ANSWER)[1]
    poorer = copy.deepcopy(one)
    poorer["components"] = poorer["components"][1:]
    assert len(sc.dedupe_reactions([poorer, one])) == 1
    assert len(sc.dedupe_reactions([poorer, one])[0]["components"]) == 2


def test_old_single_reaction_answer_still_parses():
    old = {"components": [{"name": "benzyl alcohol", "role": "reactant", "quantity": 108, "unit": "mg"},
                          {"name": "toluene", "role": "solvent", "quantity": 5, "unit": "mL"}],
           "yield_percent": 90, "conditions": {"temperature_c": "reflux", "workup": ["column chromatography"]}}
    (r,) = sc.reactions_from_answer(old)
    assert [c["role"] for c in r["components"]] == ["reactant", "reaction_solvent"]
    assert r["conditions"]["reflux"] and r["purification"] == "column chromatography"


def test_tables_are_normalized():
    (t,) = sc.tables_from_answer(TABLE_ANSWER)
    assert [e["yield_percent"] for e in t["entries"]] == [45, 83]
    assert t["components"][0]["unit"] == "mmol"


# ---------------------------------------------------------------------------
# Enrichment, validation and the app adapter
# ---------------------------------------------------------------------------

def _paper_reactions():
    out = pipeline.assemble_paper(
        {"paper_id": "p1", "doi": "10.1000/x"},
        [{"unit_id": "p1:c0", "kind": "procedure", "mode": "text", "pages": [2]}],
        [{"ok": True, "text": json.dumps(GP_ANSWER)}])
    return out["reactions"]


def test_enrich_computes_amounts_and_flags_problems():
    r3b = next(r for r in _paper_reactions() if r["label"] == "3b")
    before = copy.deepcopy(r3b)
    e = enrich.enrich_reaction(r3b, CFG, online=False)
    assert r3b == before
    c = {x["name"]: x for x in e["components"]}
    assert c["4-bromotoluene"]["moles"] == pytest.approx(5e-4)
    assert c["phenylboronic acid"]["mass_g"] == pytest.approx(0.091)
    assert c["K2CO3"]["moles"] == pytest.approx(1e-3) and "derived from equivalents" in c["K2CO3"]["flags"]
    assert c["1,4-dioxane"]["mass_g"] == pytest.approx(2 * 1.03)
    assert e["computed_yield_percent"] == pytest.approx(83.2, abs=0.2)
    codes = {f["code"] for f in e["flags"]}
    assert "purification_amounts_missing" in codes and "inherited_from_general_procedure" in codes
    assert "workup_amounts_missing" in codes and "missing_amounts" not in codes   # only Na2SO4 lacks a mass
    assert "yield_mismatch" not in codes and "mass_balance" not in codes
    assert e["solvent_ml_per_mmol"] == pytest.approx(4.0)
    assert e["group"] == "10.1000/x" and e["reaction_id"].startswith("p1#")


def test_validation_catches_wrong_yield_mass_balance_and_mmol():
    r = sc.reactions_from_answer({"reactions": [{
        "label": "4", "components": [
            {"name": "benzyl alcohol", "role": "reactant", "quantity": 108, "unit": "mg", "mmol": 2.0},
            {"name": "benzaldehyde", "role": "product", "quantity": 150, "unit": "mg"}],
        "yield_percent": 50, "conditions": {"temperature_c": 500}}]})[0]
    codes = {f["code"] for f in enrich.enrich_reaction(r, CFG, online=False)["flags"]}
    assert {"moles_mass_mismatch", "yield_mismatch", "mass_balance", "temperature_out_of_range"} <= codes


def test_review_table_adapter_feeds_the_existing_calculator():
    r3b = enrich.enrich_reaction(next(r for r in _paper_reactions() if r["label"] == "3b"), CFG, online=False)
    df = enrich.to_review_table(r3b)
    assert set(df.columns) == set(px.COLUMNS)
    assert df.loc[df["Name"] == "1,4-dioxane", "Role"].item() == "solvent"
    assert df.loc[df["Name"] == "Na2SO4", "Role"].item() == "ignore"     # no amount, as in the classic mode
    t = px.to_app_tables(df)
    assert t["product"]["name"] == "4-methylbiphenyl"
    rx = [mm.Reactant(x["Name"], x["MW (g/mol)"], x["Moles charged (mol)"], x["Mass charged (g)"],
                      int(x["Carbons/molecule"]), is_limiting=x["Limiting reagent?"])
          for _, x in t["reactants_df"].iterrows()]
    res = mm.compute_all_mass_metrics(rx, t["product"]["mw"], t["product"]["actual_mass"])
    assert res["Percentage Yield (%)"] == pytest.approx(83.2, abs=0.2)
    sug = px.suggest_eco_inputs(enrich.review_meta(r3b), r3b["yield_percent"])
    assert "Classical chromatography" in sug["workup_steps"] and "Inert gas atmosphere" in sug["setups"]


# ---------------------------------------------------------------------------
# Concurrent client and checkpointed runs (stub server)
# ---------------------------------------------------------------------------

class Stub:
    def __init__(self, reject_schema=False, delay=0.05):
        self.reject_schema, self.delay = reject_schema, delay
        self.in_flight = self.max_in_flight = self.calls = 0
        self.schemas = []
        self.lock = threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with stub.lock:
                    stub.calls += 1
                    stub.in_flight += 1
                    stub.max_in_flight = max(stub.max_in_flight, stub.in_flight)
                    stub.schemas.append("response_format" in body)
                try:
                    if stub.reject_schema and "response_format" in body:
                        self._send(400, {"error": {"message": "response_format json_schema is not supported"}})
                        return
                    time.sleep(stub.delay)
                    content = body["messages"][0]["content"]
                    text = content if isinstance(content, str) else next(x["text"] for x in content if x.get("type") == "text")
                    ans = TABLE_ANSWER if "optimization or condition-screening" in text else GP_ANSWER
                    self._send(200, {"choices": [{"message": {"content": json.dumps(ans)}}],
                                     "usage": {"prompt_tokens": 1000, "completion_tokens": 400}})
                finally:
                    with stub.lock:
                        stub.in_flight -= 1

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def test_requests_run_concurrently_with_structured_output(stub):
    eng = engines.HttpEngine(stub.url, "m", concurrency=8)
    unit = pages.Unit("u", "procedure", "text", [1], text=PROCEDURE_TEXT)
    t0 = time.perf_counter()
    res = eng.run([engines.build_request(unit, 512)] * 16)
    assert all(r.ok and r.used_schema for r in res)
    assert stub.max_in_flight > 1 and all(stub.schemas)
    assert time.perf_counter() - t0 < 16 * stub.delay           # faster than one at a time
    assert res[0].prompt_tokens == 1000


def test_falls_back_when_server_rejects_structured_output():
    s = Stub(reject_schema=True)
    try:
        eng = engines.HttpEngine(s.url, "m", concurrency=2)
        unit = pages.Unit("u", "procedure", "text", [1], text=PROCEDURE_TEXT)
        res = eng.run([engines.build_request(unit, 512)] * 3)
        assert all(r.ok and not r.used_schema for r in res) and eng.use_schema is False
        assert sc.reactions_from_answer(px.parse_json(res[0].text))
    finally:
        s.close()


def test_extract_pdf_end_to_end(stub):
    pdf = make_pdf([INTRO, PROCEDURE_TEXT, SPECTRUM, TABLE_PAGE])
    out = pipeline.extract_pdf(pdf, engines.HttpEngine(stub.url, "m"), CFG, file="paper.pdf")
    assert stub.calls == 2                                      # 1 text chunk + 1 table image, 2 pages skipped
    labels = [r.get("label") or r.get("procedure_label") for r in out["reactions"]]
    assert labels == ["A", "3b"] and out["reactions"][0]["record_type"] == "general_procedure"
    assert out["tables"][0]["entries"][1]["yield_percent"] == 83


def test_unreachable_server_raises():
    pdf = make_pdf([PROCEDURE_TEXT])
    eng = engines.HttpEngine("http://127.0.0.1:9/v1", "m", retries=0, timeout=2)
    with pytest.raises(engines.ServerUnavailable):
        pipeline.extract_pdf(pdf, eng, CFG)


def test_corpus_run_checkpoints_resumes_and_reparses(stub, tmp_path):
    pdfs = []
    for i in range(3):
        p = tmp_path / f"paper{i}.pdf"
        p.write_bytes(make_pdf([PROCEDURE_TEXT + f" Run {i}.", TABLE_PAGE]))
        pdfs.append(str(p))
    out = tmp_path / "out"
    down = engines.HttpEngine("http://127.0.0.1:9/v1", "m", retries=0, timeout=2)
    s0 = pipeline.run_corpus(pdfs, str(out), down, CFG, batch_papers=2, log=lambda m: None)
    assert s0["stopped"] and not pipeline.read_jsonl(out / "papers.jsonl")    # nothing committed

    eng = engines.HttpEngine(stub.url, "m", concurrency=8)
    s1 = pipeline.run_corpus(pdfs[:2], str(out), eng, CFG, batch_papers=2, log=lambda m: None)
    assert s1["papers"] == 2 and stub.max_in_flight > 1
    # a crash after writing reactions but before the commit line leaves orphan rows: cleaned on resume
    with open(out / "reactions.jsonl", "a") as f:
        f.write(json.dumps({"source": {"paper_id": "orphan"}}) + "\n")
    s2 = pipeline.run_corpus(pdfs, str(out), eng, CFG, batch_papers=2, log=lambda m: None)
    assert s2["papers"] == 1                                    # only the new paper
    papers = pipeline.read_jsonl(out / "papers.jsonl")
    assert len(papers) == 3 and all(p["status"] == "done" for p in papers)
    assert papers[0]["prompt_tokens"] == 2000
    rx = pipeline.read_jsonl(out / "reactions.jsonl")
    assert len(rx) == 6 and not any(r["source"]["paper_id"] == "orphan" for r in rx)
    assert len(pipeline.read_jsonl(out / "tables.jsonl")) == 3

    before = [r["reaction_id"] for r in rx]
    pipeline.reparse(str(out), log=lambda m: None)
    assert sorted(r["reaction_id"] for r in pipeline.read_jsonl(out / "reactions.jsonl")) == sorted(before)


def test_scanned_page_is_sent_as_an_image():
    src = pymupdf.open(stream=make_pdf([PROCEDURE_TEXT]), filetype="pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=src[0].get_pixmap(dpi=100))   # image only, no text layer
    infos, units, _ = pages.plan_paper(doc.tobytes(), CFG)
    assert infos[0].label == "scanned"
    assert [(u.kind, u.mode) for u in units] == [("procedure", "image")]
