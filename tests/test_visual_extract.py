import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfWriter

from src import llm, visual_extract
from src.contracts import VisualInterpretation


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
