"""PDF extraction as a server-side service: settings only on the server, offline is a normal
state, upload limits. The model server is a local stub."""

import dataclasses

import pytest

from gms import scoring, settings
from gms.extraction import service
from tests.test_dataset_extraction import INTRO, PROCEDURE_TEXT, Stub, make_pdf


def cfg(**kw) -> settings.Settings:
    base = settings.load()
    return dataclasses.replace(base, **kw)


@pytest.fixture(autouse=True)
def fresh_status():
    service.reset_status_cache()
    yield
    service.reset_status_cache()


def test_settings_come_from_the_environment_only(monkeypatch):
    monkeypatch.setenv("GMS_VLM_BASE_URL", "http://qwen.internal:8000/v1")
    monkeypatch.setenv("GMS_VLM_API_KEY", "secret-key")
    monkeypatch.setenv("GMS_MAX_UPLOAD_MB", "7")
    s = settings.load()
    assert (s.vlm_base_url, s.vlm_api_key, s.max_upload_mb, s.offline) == ("http://qwen.internal:8000/v1", "secret-key", 7, True)
    monkeypatch.delenv("GMS_VLM_BASE_URL")
    assert settings.load().vlm_base_url == ""


def test_not_configured_and_offline_are_normal_states():
    st = service.status(cfg(vlm_base_url=""))
    assert (st.configured, st.online) == (False, False) and "by hand" in st.message
    st = service.status(cfg(vlm_base_url="http://127.0.0.1:9/v1", vlm_api_key="secret-key"))
    assert (st.configured, st.online) == (True, False)
    assert "offline" in st.message and "normal" in st.message
    assert "127.0.0.1" not in st.message and "secret-key" not in st.message     # nothing about the server leaks


def test_online_status_and_extraction_to_a_reaction_record():
    stub = Stub(delay=0)
    try:
        c = cfg(vlm_base_url=stub.url, vlm_api_key="secret-key")
        assert service.status(c).online
        out = service.extract(make_pdf([INTRO, PROCEDURE_TEXT]), "si.pdf", 1, 2, c)
        assert stub.calls == 1 and out["failed_requests"] == 0          # the introduction page is not sent
        rec = next(r for r in out["reactions"] if r.get("label") == "3b")
        reaction = service.to_reaction(rec, "si.pdf")
        assert reaction.source["entry"] == "pdf" and reaction.source["pages"] == [2]
        assert reaction.product.name == "4-methylbiphenyl" and reaction.reported_yield_percent == 83
        assert any("inherited" in f for f in reaction.source["flags"])  # validation flags reach the review step
        _, score, _ = scoring.prepare_and_score(reaction, online=False)
        assert score.metrics["yield"] == pytest.approx(83, abs=1.5)
    finally:
        stub.close()


def test_server_that_disappears_gives_a_plain_message():
    stub = Stub(delay=0)
    c = cfg(vlm_base_url=stub.url, vlm_api_key="secret-key")
    assert service.status(c).online
    stub.close()
    with pytest.raises(service.ExtractionError) as e:
        service.extract(make_pdf([PROCEDURE_TEXT]), "si.pdf", 1, 1, c)
    assert "did not answer" in str(e.value) and stub.url not in str(e.value) and "secret-key" not in str(e.value)


def test_upload_limits():
    pdf = make_pdf([PROCEDURE_TEXT] * 3)
    assert service.check_upload(pdf, cfg()) == 3
    with pytest.raises(service.ExtractionError, match="not a PDF"):
        service.check_upload(b"<html>not a pdf</html>", cfg())
    with pytest.raises(service.ExtractionError, match="larger than"):
        service.check_upload(pdf + b"0" * (2 * 1024 * 1024), cfg(max_upload_mb=1))
    with pytest.raises(service.ExtractionError, match="cannot be opened"):
        service.check_upload(b"%PDF-1.7 broken", cfg())
    with pytest.raises(service.ExtractionError, match="At most 2 pages"):
        service.extract(pdf, "x.pdf", 1, 3, cfg(vlm_base_url="http://127.0.0.1:9/v1", max_pdf_pages=2))
    with pytest.raises(service.ExtractionError, match="first page"):
        service.extract(pdf, "x.pdf", 3, 2, cfg(vlm_base_url="http://127.0.0.1:9/v1"))
    with pytest.raises(service.ExtractionError, match="not set up"):
        service.extract(pdf, "x.pdf", 1, 1, cfg(vlm_base_url=""))
