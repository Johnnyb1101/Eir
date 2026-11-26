from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic, sleep

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, RequestSpec, Slide, SlideGrade
from src.llm import config
from src.pipeline import build_deck, prepare_approved_outline
from src.source_pdf import SourcePdfError, list_source_pdfs, read_selected_pdf
from src.ui_jobs import RunManager


def write_pdf(path, *texts):
    pdf = PdfWriter()
    for text in texts:
        page = pdf.add_blank_page(width=400, height=300)
        if text:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)}),
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 260 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = pdf._add_object(stream)
    pdf.write(path)


def wait_for(manager, run_id, expected):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        snapshot = manager.snapshot(run_id)
        if snapshot.status in {expected, "failed"}:
            assert snapshot.status == expected, snapshot.message
            return snapshot
        sleep(0.01)
    pytest.fail(f"Timed out waiting for {expected}")


def test_direct_text_uses_original_pages_and_stable_fingerprint(tmp_path):
    pdf = tmp_path / "Example_CPG.PDF"
    long_page = "Assessment " + "Evidence " * 150
    write_pdf(pdf, "Introduction", None, long_page)
    (tmp_path / "not-a-guide.txt").touch()
    source = read_selected_pdf(pdf)
    assert list_source_pdfs(tmp_path) == (pdf,)
    assert source.extractor == "pypdf"
    assert source.page_count == 3
    assert source.pdf_bytes == pdf.read_bytes()
    assert source.sha256 == sha256(pdf.read_bytes()).hexdigest()
    assert [page.number for page in source.pages] == [1, 3]
    assert "PDF page 2" in source.warnings[0]
    evidence = source.evidence_for(OutlineEntry(title="Assessment", objective="Explain evidence",
                                               time_minutes=2))
    assert [item["pages"] for item in evidence] == ["3"]
    assert evidence[0]["text"] == long_page.strip()
    prepared = prepare_approved_outline(Outline(topic="Assessment", entries=[OutlineEntry(
        title="Assessment", objective="Explain evidence", time_minutes=2,
    )]), selected_source=source)
    with pytest.raises(ValueError, match="pinned source"):
        build_deck(prepared, refresh_sources=True)


def test_missing_or_invalid_pdf_fails_with_actionable_message(tmp_path):
    pdf = tmp_path / "invalid.pdf"
    with pytest.raises(SourcePdfError, match="could not read"):
        read_selected_pdf(pdf)
    pdf.write_bytes(b"bad file")
    with pytest.raises(SourcePdfError, match="could not read"):
        read_selected_pdf(pdf)


@pytest.mark.parametrize("extractor", ["pypdf", "pdfium"])
def test_extractors_preserve_physical_pages_and_pinned_bytes(tmp_path, extractor):
    pdf = tmp_path / "Selected_CPG.pdf"
    write_pdf(pdf, "Introduction", None, "Assessment evidence")
    original_bytes = pdf.read_bytes()
    source = read_selected_pdf(pdf, extractor=extractor)
    assert source.extractor == extractor
    assert source.page_count == 3
    assert source.pdf_bytes == original_bytes
    assert source.sha256 == sha256(original_bytes).hexdigest()
    assert [page.number for page in source.pages] == [1, 3]
    assert [page.id for page in source.pages] == ["selected-cpg-pdf-s1", "selected-cpg-pdf-s3"]
    assert source.warnings == ("PDF page 2 has no extractable text. Review the original page.",)
    assert source.pages[1].text == "Assessment evidence"
    assert "pdf_bytes=" not in repr(source)
    pdf.write_bytes(b"changed after selection")
    assert source.pdf_bytes == original_bytes


@pytest.mark.parametrize("extractor", ["pypdf", "pdfium"])
def test_extractors_reject_encrypted_and_corrupt_pdfs(tmp_path, extractor):
    pdf = tmp_path / "Selected_CPG.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=300)
    writer.encrypt("password")
    writer.write(pdf)
    with pytest.raises(SourcePdfError, match="encrypted"):
        read_selected_pdf(pdf, extractor=extractor)
    pdf.write_bytes(b"bad file")
    with pytest.raises(SourcePdfError, match="could not read"):
        read_selected_pdf(pdf, extractor=extractor)


@pytest.mark.parametrize("extractor", ["pypdf", "pdfium"])
def test_extractors_warn_on_sparse_text_with_an_image(tmp_path, extractor):
    pdf = tmp_path / "Illustrated_CPG.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    image = DecodedStreamObject()
    image.set_data(b"\xff\x00\x00")
    image.update({NameObject("/Type"): NameObject("/XObject"),
                  NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(1),
                  NameObject("/Height"): NumberObject(1),
                  NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                  NameObject("/BitsPerComponent"): NumberObject(8)})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q 10 0 0 10 20 20 cm /Im1 Do Q BT /F1 12 Tf 10 260 Td (Short) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(pdf)

    source = read_selected_pdf(pdf, extractor=extractor)
    assert [page.text for page in source.pages] == ["Short"]
    assert source.warnings == ("PDF page 1 has images and little extractable text. Review the original page.",)


def test_unknown_extractor_fails_before_reading(tmp_path):
    with pytest.raises(SourcePdfError, match="pypdf.*pdfium"):
        read_selected_pdf(tmp_path / "missing.pdf", extractor="other")


def test_selected_run_keeps_original_bytes_across_revisions(tmp_path, monkeypatch):
    pdf = tmp_path / "Original_CPG.pdf"
    write_pdf(pdf, "Assessment original evidence", None)
    original_sha = sha256(pdf.read_bytes()).hexdigest()
    monkeypatch.setitem(sys.modules, "src.retrieve", None)
    shown_texts = []
    written_texts = []

    def create_outline(spec, pages, feedback):
        shown_texts.append(tuple(page["text"] for page in pages))
        return Outline(topic=spec.topic, entries=[OutlineEntry(
            title="Assessment", objective="Review original evidence", time_minutes=2,
            chunk_ids=[pages[0]["id"]],
        )])

    def write(entry, pages, feedback, **_):
        written_texts.extend(page["text"] for page in pages)
        return Slide(title=entry.title, bullets=["Review the evidence"], speaker_notes="Test slide",
                     time_minutes=2, citations=[pages[0]["id"]])

    monkeypatch.setattr(outliner, "outline_deck", create_outline)
    monkeypatch.setattr(writer, "write_slide", write)
    monkeypatch.setattr(critic, "critique_slide", lambda *_, **__: SlideGrade(passed=True))
    manager = RunManager(tmp_path / "output")
    try:
        run = manager.start(RequestSpec(topic="Assessment", audience="Reviewers", duration_minutes=2),
                            source_path=pdf)
        first = wait_for(manager, run, "awaiting_approval")
        assert first.source_name == pdf.name
        assert "PDF page 2" in first.source_warnings[0]
        write_pdf(pdf, "Replacement evidence")
        manager.revise(run, first.revision, "Adjust the outline")
        second = wait_for(manager, run, "awaiting_approval")
        manager.approve(run, second.revision)
        result = wait_for(manager, run, "completed")
        assert shown_texts == [("Assessment original evidence",)] * 2
        assert written_texts == ["Assessment original evidence"]
        events = json.loads(result.artifacts.log.read_text(encoding="utf-8"))
        selected, = [entry for entry in events if entry.get("action") == "source_selected"]
        assert selected["filename"] == pdf.name
        assert selected["sha256"] == original_sha
        packet = result.artifacts.packet.read_text(encoding="utf-8")
        assert "Original_CPG.pdf" in packet
        assert original_sha in packet
        assert "Original CPG, PDF page, 1" in packet
        assert "PDF page 2 has no extractable text" in packet
        assert manager.history()[0].source_mode == "selected_pdf"
    finally:
        manager._executor.shutdown(wait=True)


def test_streamlit_requires_an_explicit_selected_pdf(tmp_path, monkeypatch):
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    pdf = tmp_path / "Sample_CPG.pdf"
    write_pdf(pdf, "Assessment selected document")
    monkeypatch.setitem(config, "source_folder", str(tmp_path))
    monkeypatch.setitem(config, "visual", None)
    monkeypatch.setattr(outliner, "outline_deck", lambda spec, pages, feedback: Outline(
        topic=spec.topic, entries=[OutlineEntry(title="Assessment", objective="Find the source",
                                              time_minutes=2, chunk_ids=[pages[0]["id"]])],
    ))
    manager = RunManager(tmp_path / "output")
    monkeypatch.setattr("src.ui_jobs.RunManager", lambda _: manager)
    streamlit.cache_resource.clear()
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    try:
        app.run()
        assert not app.exception
        extractor = next(widget for widget in app.selectbox if widget.label == "Selected PDF text extractor")
        assert extractor.value == "pypdf"
        app.text_input[0].set_value("Assessment")
        app.text_input[1].set_value("Medics")
        app.number_input[0].set_value(2)
        app.radio[0].set_value("One selected PDF")
        next(button for button in app.button if button.label == "Create outline").click().run()
        assert manager.active_id() is None
        assert any("Choose an available PDF" in warning.value for warning in app.warning)

        next(widget for widget in app.selectbox if widget.label == "Original PDF").set_value(pdf)
        next(widget for widget in app.selectbox if widget.label == "Selected PDF text extractor").set_value("PDFium")
        next(button for button in app.button if button.label == "Create outline").click().run()
        run_id = manager.active_id()
        assert run_id is not None
        snapshot = wait_for(manager, run_id, "awaiting_approval")
        assert snapshot.source_name == pdf.name
        assert snapshot.source_extractor == "pdfium"
        assert snapshot.sources[0].retrieved[0].pages == "1"
        app.run()
        assert next(button for button in app.button if button.label == "Check this page").disabled
        assert not app.exception
    finally:
        streamlit.cache_resource.clear()
        manager._executor.shutdown(wait=True)
