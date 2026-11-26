import base64
import hashlib
import io
import json
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter

from src import llm, visual_extract
from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, RequestSpec, Slide, SlideGrade, VisualInterpretation
from src.source_pdf import SelectedPdf, SourcePage
from src.ui_jobs import RunConflict, RunManager


def content(**overrides):
    fields = dict(title="Example", text=["Visible text"], tables=[], nodes=[],
                  connections=[], chart_descriptions=[], uncertainties=[])
    fields.update(overrides)
    return fields


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_blank_page(width=200, height=100)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def test_render_selects_requested_page_and_caps_dimensions(pdf):
    original = pdf.read_bytes()
    first, first_size = visual_extract.render_page(pdf, 1)
    second, second_size = visual_extract.render_page(pdf, 2)
    assert first_size == (1100, 2200)
    assert second_size == (2200, 1100)
    for data, dimensions in ((first, first_size), (second, second_size)):
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == "PNG"
            assert image.size == dimensions
    assert pdf.read_bytes() == original


def test_selected_pdf_visual_check_uses_pinned_bytes_and_keeps_notes_separate(pdf, tmp_path, monkeypatch):
    data = pdf.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    page = SourcePage("test-pdf-s1", 1, "PDF page", "Assessment source only", "Assessment source only")
    selected = SelectedPdf(filename=pdf.name, title="Test guide", sha256=digest, pages=(page,),
                           warnings=(), extractor="pdfium", page_count=2, pdf_bytes=data)
    checking = Event()
    release = Event()

    def read_source(path, *, extractor):
        assert path == pdf and extractor == "pdfium"
        return selected

    def review(*, model, system, messages, output_config, max_tokens):
        assert model == "test-vision"
        # Page 2 has no text but can still be checked from the pinned render.
        assert system == visual_extract.REVIEW_SYSTEM
        image_content, text_content = messages[0]["content"]
        prompt = text_content["text"]
        assert 'Extracted page text as JSON:\n""' in prompt
        image = base64.b64decode(image_content["source"]["data"])
        with Image.open(io.BytesIO(image)) as screen:
            assert screen.width > screen.height
        response_format = output_config["format"]
        assert response_format["type"] == "json_schema"
        assert response_format["schema"]["required"] == ["missing", "uncertainties"]
        if client.messages.create.call_count == 1:
            text = "Invalid JSON"
        else:
            checking.set()
            assert release.wait(5)
            text = json.dumps({"missing": ["Test infographic labels"],
                               "uncertainties": ["Test branch uncertain"]})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    def write_slide(entry, chunks, *_rest, **_options):
        assert [chunk["text"] for chunk in chunks] == [page.text]
        return Slide(title=entry.title, bullets=["Source-backed content"], speaker_notes="Draft notes",
                     time_minutes=2, citations=[page.id])

    client = Mock()
    client.messages.create.side_effect = review
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setitem(llm.config, "visual", None)
    monkeypatch.setattr("src.ui_jobs.read_selected_pdf", read_source)
    monkeypatch.setattr(outliner, "outline_deck", lambda spec, evidence, feedback: Outline(
        topic=spec.topic, entries=[OutlineEntry(title="Assessment", objective="Describe the source",
                                              time_minutes=2, chunk_ids=[page.id])],
    ))
    monkeypatch.setattr(writer, "write_slide", write_slide)
    monkeypatch.setattr(critic, "critique_slide", lambda *_, **__: SlideGrade(passed=True))

    runs = RunManager(tmp_path / "output")

    def wait_for(check):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            state = runs.snapshot(run_id)
            if check(state) or state.status == "failed":
                assert state.status != "failed", state.message
                return state
            sleep(0.01)
        pytest.fail("Run did not reach its expected state")

    try:
        run_id = runs.start(RequestSpec(topic="Assessment", audience="Reviewers", duration_minutes=2),
                            source_path=pdf, source_extractor="pdfium")
        outline = wait_for(lambda state: state.status == "awaiting_approval")
        assert outline.source_pages == 2
        with pytest.raises(RunConflict, match="Configure a visual model"):
            runs.review_page(run_id, outline.revision, 2)
        monkeypatch.setitem(llm.config, "visual", {"provider": "anthropic", "model": "test-vision"})
        with pytest.raises(RunConflict, match="from 1 to 2"):
            runs.review_page(run_id, outline.revision, 3)
        client.messages.create.assert_not_called()
        pdf.write_bytes(b"replaced file after outline")
        runs.review_page(run_id, outline.revision, 2)
        rejected = wait_for(lambda state: "failed" in (state.visual_message or ""))
        assert "JSONDecodeError" in rejected.visual_message
        assert rejected.status == "awaiting_approval"
        assert rejected.reviewing_page is None
        assert not rejected.visual_reviews
        runs.review_page(run_id, rejected.revision, 2)
        assert checking.wait(5)
        with pytest.raises(RunConflict, match="active visual check"):
            runs.review_page(run_id, outline.revision, 1)
        with pytest.raises(RunConflict, match="active visual check"):
            runs.approve(run_id, outline.revision)
        release.set()

        checked = wait_for(lambda state: bool(state.visual_reviews))
        assert client.messages.create.call_count == 2
        assert checked.visual_reviews[0].source_sha256 == digest
        assert checked.visual_reviews[0].page_number == 2
        runs.approve(run_id, checked.revision)
        complete = wait_for(lambda state: state.status == "completed")
        packet = complete.artifacts.packet.read_text(encoding="utf-8")
        assert "Text extractor `pdfium`" in packet
        assert "## Unreviewed visual checks" in packet
        assert "### PDF page 2" in packet
        assert "Test infographic labels" in packet
        assert "Test guide, PDF page, 1" in packet
        events = json.loads(complete.artifacts.log.read_text(encoding="utf-8"))
        report, = [event for event in events if event.get("action") == "visual_review"]
        assert (report["status"], report["page_number"], report["source_sha256"]) == ("unreviewed", 2, digest)
    finally:
        release.set()
        runs._executor.shutdown(wait=True)


@pytest.mark.parametrize("page", [0, -1, 3, True, 1.5])
def test_invalid_page_is_rejected(pdf, page):
    with pytest.raises(ValueError):
        visual_extract.render_page(pdf, page)


def test_corrupt_pdf_is_rejected(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a PDF")
    with pytest.raises(visual_extract.pdfium.PdfiumError):
        visual_extract.render_page(path, 1)


def test_oversized_render_is_rejected(pdf, monkeypatch):
    monkeypatch.setattr(visual_extract, "MAX_IMAGE_BYTES", 1)
    with pytest.raises(ValueError, match="image limit"):
        visual_extract.render_page(pdf, 1)


def test_disabled_visual_makes_no_request_or_output(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(visual_extract, "load_config", lambda: SimpleNamespace(visual=None))
    request = Mock(side_effect=AssertionError("No provider request allowed"))
    monkeypatch.setattr(llm, "generate_visual", request)
    output = tmp_path / "reviews"
    with pytest.raises(ValueError, match="disabled"):
        visual_extract.extract_page(pdf, 1, output)
    request.assert_not_called()
    assert not output.exists()


def test_review_provenance_and_no_overwrite(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(visual_extract, "load_config", lambda: SimpleNamespace(visual=True))
    response = VisualInterpretation(**content())
    usage = dict(provider="ollama", model="vision-model", input_tokens=5, output_tokens=10)
    request = Mock(return_value=(response, usage))
    monkeypatch.setattr(llm, "generate_visual", request)
    original = pdf.read_bytes()
    output = tmp_path / "reviews"
    first = visual_extract.extract_page(pdf, 2, output)
    first_bytes = first.read_bytes()
    second = visual_extract.extract_page(pdf, 2, output)
    assert first != second
    assert first.read_bytes() == first_bytes
    record = json.loads(first_bytes)
    assert record["status"] == "unreviewed"
    assert record["origin"] == "ai_visual_interpretation"
    assert record["source"] == {
        "filename": "source.pdf", "path": str(pdf.resolve()),
        "sha256": hashlib.sha256(original).hexdigest(), "pdf_page": 2,
    }
    image = first.parent / record["image"]["file"]
    assert record["image"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert request.call_args.args[0] == image.read_bytes()
    assert record["usage"] == usage
    assert record["interpretation"] == response.model_dump()
    assert pdf.read_bytes() == original
    assert not (tmp_path / "db").exists()


def test_provider_failure_creates_no_review(pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(visual_extract, "load_config", lambda: SimpleNamespace(visual=True))
    monkeypatch.setattr(llm, "generate_visual", Mock(side_effect=RuntimeError("Provider failed")))
    output = tmp_path / "reviews"
    with pytest.raises(RuntimeError, match="Provider failed"):
        visual_extract.extract_page(pdf, 1, output)
    assert not output.exists()


@pytest.mark.parametrize("changes", [
    {"status": "approved"},
    {"pdf_page": 99},
    {"tables": [{"title": "T", "columns": ["A", "B"], "rows": [["one"]], "footnotes": []}]},
    {"nodes": [{"id": "a", "text": "A"}, {"id": "a", "text": "B"}]},
    {"connections": [{"from_id": "a", "to_id": "missing", "label": "yes"}]},
    {"text": []},
])
def test_invalid_visual_structure_is_rejected(changes):
    with pytest.raises(ValidationError):
        VisualInterpretation(**content(**changes))


def test_uncertainty_only_response_is_allowed():
    result = VisualInterpretation(**content(text=[], uncertainties=["Image is unreadable"]))
    assert result.uncertainties


def test_table_and_labeled_connections_are_preserved():
    fields = content(
        tables=[dict(title="Table", columns=["Name", "Value"], rows=[["A", "1 mg"]],
                     footnotes=["Footnote"])],
        nodes=[dict(id="a", text="Question?"), dict(id="b", text="Action")],
        connections=[dict(from_id="a", to_id="b", label="Yes")])
    assert VisualInterpretation(**fields).model_dump() == fields
