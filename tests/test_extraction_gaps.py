from types import SimpleNamespace

import pytest

from src import ingest


@pytest.mark.parametrize(
    "text, images, warn",
    [
        ("Header\nFooter", [object()], True),
        (" \n\t", [object()], True),
        (None, [object()], True),
        ("x" * 499, [object()], True),
        ("x" * 500, [object()], False),
        ("x" * 501, [object()], False),
        ("Short text", [], False),
        ("", [], False),
        ("x " * 499, [object()], True),
    ],
)
def test_gap_warning_preserves_extracted_lines(monkeypatch, capsys, text, images, warn):
    page = SimpleNamespace(extract_text=lambda: text, images=images)
    monkeypatch.setattr(ingest, "PdfReader", lambda path: SimpleNamespace(pages=[page]))

    lines = ingest.read_lines("example.pdf")

    assert lines == [(1, line) for line in (text or "").split("\n")]
    output = capsys.readouterr().out
    if warn:
        count = len("".join((text or "").split()))
        assert f"example.pdf: PDF page 1, {count} non-whitespace text characters" in output
        assert "possible missing visual content - review original page" in output
    else:
        assert output == ""


def test_warning_identifies_page_within_document(monkeypatch, capsys):
    pages = [
        SimpleNamespace(extract_text=lambda: "Ordinary text", images=[]),
        SimpleNamespace(extract_text=lambda: "Image caption", images=[object()]),
    ]
    monkeypatch.setattr(ingest, "PdfReader", lambda path: SimpleNamespace(pages=pages))

    lines = ingest.read_lines("example.pdf")

    assert lines == [(1, "Ordinary text"), (2, "Image caption")]
    output = capsys.readouterr().out
    assert "PDF page 2," in output
    assert "PDF page 1," not in output
