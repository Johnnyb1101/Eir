import json
from hashlib import sha256

import pytest
from pptx.text.text import TextFrame

from src.contracts import Citation, Deck, Outline, OutlineEntry, RequestSpec, Slide
from src.render import render_deck
from src.review_context import ReviewInputsError, _digest, read_review_inputs, write_review_inputs
from src.source_pdf import SelectedPdf
from src.visual_extract import PageReview


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(TextFrame, "fit_text", lambda *_args, **_kwargs: None)
    primary = {"id": "p3", "source": "CPG", "section": "Triage", "pages": "3", "text": "Assess first."}
    supplement = {"id": "supp", "source": "Reference", "section": "Excerpt", "pages": "",
                  "text": "Additional context.", "kind": "supplemental", "url": "https://example.org/reference"}
    slide = Slide(title="Triage 🩺\nNow", bullets=["Assess\vfirst", "Then act\ncarefully"],
                  speaker_notes="Explain 🩺.\nKeep qualifications.", time_minutes=2,
                  citations=["p3", "supp"], sources=[
                      Citation(chunk_id=c["id"], source=c["source"], section=c["section"], pages=c["pages"],
                               kind=c.get("kind", "primary"), url=c.get("url")) for c in (primary, supplement)])
    args = dict(spec=RequestSpec(topic="Triage", audience="Medics", duration_minutes=2),
                outline=Outline(topic="Triage", entries=[OutlineEntry(title="Triage", objective="Assess",
                                                                    time_minutes=2, chunk_ids=["p3"])]),
                deck=Deck(title="Triage", slides=[slide]),
                source=SelectedPdf("guide.pdf", "CPG", "source-hash", (), (), "pdfium", 3, b""),
                chunks=[[primary, supplement]],
                visual_reviews=[PageReview(3, "guide.pdf", "source-hash", "image-hash", "anthropic",
                                           "vision", ("Diagram",), ())])
    deck_path = tmp_path / "deck.pptx"
    path = tmp_path / "review-inputs.json"
    render_deck(args["deck"], deck_path)
    write_review_inputs(path, deck_path, **args)
    return path, deck_path, args


def rewrite(path, change, *, rehash=True):
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document["payload"])
    if rehash:
        document["payload_sha256"] = _digest(document["payload"])
    path.write_text(json.dumps(document), encoding="utf-8")


def test_round_trip_preserves_review_content_without_pdf_bytes(bundle):
    path, deck_path, args = bundle
    result = read_review_inputs(path, deck_path)
    assert result.spec == args["spec"] and result.outline == args["outline"]
    assert result.deck == args["deck"]
    assert result.chunks == tuple(tuple(group) for group in args["chunks"])
    assert result.visual_reviews == tuple(args["visual_reviews"])
    assert result.source.pages == () and result.source.pdf_bytes == b""
    assert result.source.page_count == 3
    assert result.deck_sha256 == sha256(deck_path.read_bytes()).hexdigest()


def test_rejects_modified_deck_bytes(bundle):
    path, deck_path, _ = bundle
    deck_path.write_bytes(deck_path.read_bytes() + b"changed")
    with pytest.raises(ReviewInputsError):
        read_review_inputs(path, deck_path)


@pytest.mark.parametrize("change", [
    lambda p: p.update(version=2),
    lambda p: p["source"].update(extractor="unknown"),
    lambda p: p["visual_reviews"][0].update(source_sha256="other"),
    lambda p: p["visual_reviews"][0].update(filename="other.pdf"),
    lambda p: p["visual_reviews"][0].update(page_number=4),
    lambda p: p["outline"]["entries"].clear(),
    lambda p: p["deck"]["slides"][0].update(citations=["missing"]),
    lambda p: p["deck"]["slides"][0]["sources"][0].update(kind="supplemental"),
    lambda p: p["deck"]["slides"][0].update(title="Altered"),
    lambda p: p["deck"]["slides"][0].update(bullets=["Altered"]),
    lambda p: p["deck"]["slides"][0].update(speaker_notes="Altered"),
    lambda p: p["chunks"][0][0].update(text="Altered retained passage"),
])
def test_rejects_invalid_even_with_recomputed_payload_digest(bundle, change):
    path, deck_path, _ = bundle
    rewrite(path, change)
    with pytest.raises(ReviewInputsError):
        read_review_inputs(path, deck_path)


def test_rejects_manifest_tampering(bundle):
    path, deck_path, _ = bundle
    rewrite(path, lambda p: p["spec"].update(topic="Changed"), rehash=False)
    with pytest.raises(ReviewInputsError):
        read_review_inputs(path, deck_path)


def test_errors_do_not_expose_local_paths_or_payload(tmp_path):
    path = tmp_path / "sensitive-name.json"
    path.write_text('{"private": "patient detail"}', encoding="utf-8")
    with pytest.raises(ReviewInputsError) as error:
        read_review_inputs(path, tmp_path / "missing.pptx")
    assert "sensitive" not in str(error.value) and "patient" not in str(error.value)


def test_role_and_primary_source_validation_survives_rehash(bundle):
    path, deck_path, _ = bundle
    def change(payload):
        payload["chunks"][0][0]["source"] = "Another CPG"
        payload["chunks_sha256"] = _digest(payload["chunks"])
    rewrite(path, change)
    with pytest.raises(ReviewInputsError):
        read_review_inputs(path, deck_path)

def test_rejects_content_edit_even_after_updating_deck_fingerprint(bundle):
    from pptx import Presentation
    path, deck_path, _ = bundle
    presentation = Presentation(deck_path)
    presentation.slides[0].placeholders[1].text = "Changed clinical claim"
    presentation.save(deck_path)
    rewrite(path, lambda p: p.update(deck_sha256=sha256(deck_path.read_bytes()).hexdigest()))
    with pytest.raises(ReviewInputsError):
        read_review_inputs(path, deck_path)


def test_citation_footer_labels_are_not_reviewed_as_speaker_notes(bundle):
    from pptx import Presentation
    path, deck_path, _ = bundle
    presentation = Presentation(deck_path)
    presentation.slides[0].notes_slide.notes_text_frame.paragraphs[-1].text = "Different citation label"
    presentation.save(deck_path)
    rewrite(path, lambda p: p.update(deck_sha256=sha256(deck_path.read_bytes()).hexdigest()))
    assert read_review_inputs(path, deck_path).deck.slides[0].speaker_notes.startswith("Explain")

@pytest.mark.parametrize("valid", [True, False])
def test_validates_optional_exact_excerpt_hash(bundle, valid):
    path, deck_path, _ = bundle
    def change(payload):
        chunk = payload["chunks"][0][0]
        chunk["text_sha256"] = sha256(chunk["text"].encode("utf-8")).hexdigest() if valid else "wrong"
        payload["chunks_sha256"] = _digest(payload["chunks"])
    rewrite(path, change)
    if valid:
        assert read_review_inputs(path, deck_path).chunks[0][0]["text_sha256"]
    else:
        with pytest.raises(ReviewInputsError):
            read_review_inputs(path, deck_path)
