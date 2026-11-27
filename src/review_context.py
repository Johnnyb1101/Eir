"""Portable, data-only review context bound to one exported presentation."""
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import BadZipFile

from pptx import Presentation
from lxml.etree import XMLSyntaxError

from src.contracts import Citation, Deck, Outline, RequestSpec
from src.source_pdf import SelectedPdf
from src.visual_extract import PageReview


class ReviewInputsError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewInputs:
    spec: RequestSpec
    outline: Outline
    deck: Deck
    source: SelectedPdf
    chunks: tuple
    visual_reviews: tuple[PageReview, ...]
    deck_sha256: str


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _require(condition):
    if not condition:
        raise ReviewInputsError("Review inputs are invalid or stale. Export the deck and review inputs again.")


def _text(value, *, empty=False):
    _require(isinstance(value, str) and (empty or bool(value.strip())))


def _validate(payload, deck_bytes):
    _require(payload["version"] == 1)
    _require(payload["deck_sha256"] == sha256(deck_bytes).hexdigest())
    _require(payload["chunks_sha256"] == _digest(payload["chunks"]))
    spec = RequestSpec.model_validate(payload["spec"], strict=True)
    outline = Outline.model_validate(payload["outline"], strict=True)
    deck = Deck.model_validate(payload["deck"], strict=True)
    metadata = payload["source"]
    _require(set(metadata) == {"filename", "title", "sha256", "warnings", "extractor", "page_count"})
    for key in ("filename", "title", "sha256"):
        _text(metadata[key])
    _require(metadata["extractor"] in {"pypdf", "pdfium"})
    _require(type(metadata["page_count"]) is int and metadata["page_count"] > 0)
    _require(isinstance(metadata["warnings"], list))
    for warning in metadata["warnings"]:
        _text(warning)
    source = SelectedPdf(**{**metadata, "warnings": tuple(metadata["warnings"])}, pages=(), pdf_bytes=b"")
    chunks = payload["chunks"]
    _require(isinstance(chunks, list) and len(chunks) == len(deck.slides) == len(outline.entries))
    for slide, group in zip(deck.slides, chunks, strict=True):
        _require(isinstance(group, list) and bool(group))
        by_id = {}
        for chunk in group:
            _require(isinstance(chunk, dict))
            for key in ("id", "source", "text", "section", "pages"):
                _text(chunk[key], empty=key in {"section", "pages"})
            if "text_sha256" in chunk:
                _require(chunk["text_sha256"] == sha256(chunk["text"].encode("utf-8")).hexdigest())
            kind = chunk.get("kind", "primary")
            _require(kind in {"primary", "supplemental"})
            if kind == "primary":
                _require(chunk["source"] == source.title and bool(chunk["pages"].strip()))
                _require(not chunk.get("url"))
            else:
                _text(chunk.get("url"))
                _require(chunk["url"].startswith("https://"))
            _require(chunk["id"] not in by_id)
            by_id[chunk["id"]] = chunk
        _require(all(cite in by_id for cite in slide.citations))
        _require(any(by_id[cite].get("kind", "primary") == "primary" for cite in slide.citations))
        expected = [Citation(chunk_id=cite, source=by_id[cite]["source"],
                            section=by_id[cite]["section"], pages=by_id[cite]["pages"],
                            kind=by_id[cite].get("kind", "primary"),
                            url=by_id[cite].get("url") if by_id[cite].get("kind") == "supplemental" else None)
                    for cite in slide.citations]
        _require(sorted((c.model_dump_json() for c in expected)) ==
                 sorted(c.model_dump_json() for c in slide.sources))
    reviews = []
    _require(isinstance(payload["visual_reviews"], list))
    seen_pages = set()
    for item in payload["visual_reviews"]:
        _require(isinstance(item, dict))
        _require(type(item["page_number"]) is int and 1 <= item["page_number"] <= source.page_count)
        _require(item["page_number"] not in seen_pages)
        seen_pages.add(item["page_number"])
        _require(item["filename"] == source.filename and item["source_sha256"] == source.sha256)
        for key in ("image_sha256", "provider", "model"):
            _text(item[key])
        _require(item["status"] == "unreviewed")
        for key in ("missing", "uncertainties"):
            _require(isinstance(item[key], list))
            for finding in item[key]:
                _text(finding)
        reviews.append(PageReview(**{**item, "missing": tuple(item["missing"]),
                                     "uncertainties": tuple(item["uncertainties"])}))
    _validate_presentation(deck, deck_bytes)
    return ReviewInputs(spec, outline, deck, source, tuple(tuple(group) for group in chunks),
                        tuple(reviews), payload["deck_sha256"])


def _validate_presentation(deck, deck_bytes):
    actual = Presentation(BytesIO(deck_bytes))
    _require(len(actual.slides) == len(deck.slides) + 1)
    _require(actual.slides[-1].shapes.title.text == "References")
    # Use python-pptx's own string encoding for newlines, soft breaks and emoji.
    expected = Presentation()
    for model, slide in zip(deck.slides, actual.slides, strict=False):
        reference = expected.slides.add_slide(expected.slide_layouts[1])
        reference.shapes.title.text = model.title
        _require(slide.shapes.title.text == reference.shapes.title.text)
        body = reference.placeholders[1].text_frame
        _require(bool(model.bullets))
        body.text = model.bullets[0]
        for bullet in model.bullets[1:]:
            body.add_paragraph().text = bullet
        _require(slide.placeholders[1].text == body.text)
        notes = reference.notes_slide.notes_text_frame
        notes.text = model.speaker_notes
        actual_notes = slide.notes_slide.notes_text_frame
        # Citation paragraphs are export labels, not clinician-authored notes.
        count = len(notes.paragraphs)
        _require(len(actual_notes.paragraphs) == count + 1 + len(model.sources))
        _require("\n".join(p.text for p in actual_notes.paragraphs[:count]) == notes.text)


_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, BadZipFile, XMLSyntaxError)


def read_review_inputs(path: Path, deck_path: Path) -> ReviewInputs:
    """Read only the two explicitly supplied local files; never follow stored links."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        _require(isinstance(document, dict) and set(document) == {"payload", "payload_sha256"})
        payload = document["payload"]
        _require(document["payload_sha256"] == _digest(payload))
        return _validate(payload, Path(deck_path).read_bytes())
    except _READ_ERRORS:
        raise ReviewInputsError("Review inputs could not be validated. Export the deck and review inputs again.") from None


def write_review_inputs(path, deck_path, *, spec: RequestSpec, outline: Outline,
                        deck: Deck, source: SelectedPdf, chunks, visual_reviews=()) -> None:
    """Validate before writing a self-contained manifest without PDF bytes or paths."""
    try:
        deck_bytes = Path(deck_path).read_bytes()
        groups = [list(group) for group in chunks]
        payload = {"version": 1, "deck_sha256": sha256(deck_bytes).hexdigest(),
                   "spec": spec.model_dump(), "outline": outline.model_dump(), "deck": deck.model_dump(),
                   "source": {key: getattr(source, key) for key in
                              ("filename", "title", "sha256", "warnings", "extractor", "page_count")},
                   "chunks": groups, "chunks_sha256": _digest(groups),
                   "visual_reviews": [asdict(review) for review in visual_reviews]}
        payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        _validate(payload, deck_bytes)
        Path(path).write_text(json.dumps({"payload": payload, "payload_sha256": _digest(payload)},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    except _READ_ERRORS:
        raise ReviewInputsError("Review inputs could not be saved. Check the deck and retained source evidence.") from None
