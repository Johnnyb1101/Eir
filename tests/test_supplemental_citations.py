from pptx import Presentation

from src.agents import critic, writer
from src.agents.writer import add_sources
from src.contracts import Citation, Deck, OutlineEntry, Slide, SlideGrade
from src.packet import write_packet
from src.render import render_deck


def _slide(citations, *, sources=()):
    return Slide(title="Clinical context", bullets=["Cue"], speaker_notes="Supported notes.",
                 time_minutes=2, citations=citations, sources=list(sources))


def test_legacy_citation_defaults_and_source_metadata_is_rebuilt():
    legacy = Citation(chunk_id="cpg", source="Guideline.pdf", section="Care", pages="2")
    assert legacy.kind == "primary"
    assert legacy.url is None

    slide = _slide(["cpg", "web"], sources=[Citation(
        chunk_id="web", source="Forged source", section="Forged section", pages="99",
        kind="primary", url="https://forged.example")])
    chunks = [
        {"id": "cpg", "source": "Guideline.pdf", "section": "Care", "pages": "2"},
        {"id": "web", "source": "Registry", "section": "Findings", "pages": "",
         "kind": "supplemental", "url": "https://example.org/study", "text": "Finding"},
    ]
    add_sources(slide, chunks)

    assert slide.sources == [legacy, Citation(
        chunk_id="web", source="Registry", section="Findings", pages="",
        kind="supplemental", url="https://example.org/study")]


def test_packet_and_pptx_export_labeled_accurate_clickable_references(tmp_path):
    slide = _slide(["cpg", "web"])
    add_sources(slide, [
        {"id": "cpg", "source": "Guideline.pdf", "section": "Care", "pages": "2"},
        {"id": "web", "source": "Registry", "section": "Findings", "pages": "",
         "kind": "supplemental", "url": "https://example.org/" + "study" * 40},
    ])
    deck = Deck(title="Demo", slides=[slide])
    packet_path = tmp_path / "packet.md"
    deck_path = tmp_path / "deck.pptx"
    write_packet(deck, [SlideGrade(passed=True)], [1], packet_path)
    render_deck(deck, deck_path)

    packet = packet_path.read_text(encoding="utf-8")
    assert "Primary CPG: Guideline.pdf, Care, 2" in packet
    url = "https://example.org/" + "study" * 40
    assert f"Supplemental source: Registry, Findings ([source link]({url}))" in packet
    assert "pp. " not in packet

    prs = Presentation(deck_path)
    notes = prs.slides[0].notes_slide.notes_text_frame.text
    refs = prs.slides[-1].placeholders[1].text_frame
    assert "Primary CPG: Guideline.pdf, Care, pp. 2" in notes
    assert "Supplemental source: Registry, Findings — Source link" in notes
    assert "Primary CPG: Guideline.pdf" in refs.text
    assert "Supplemental source: Registry — Source link" in refs.text
    assert [run.hyperlink.address for paragraph in refs.paragraphs for run in paragraph.runs
            if run.hyperlink.address] == [url]


def test_references_list_legacy_documents_once_and_preserve_slide_locators(tmp_path):
    first = _slide(["a"], sources=[Citation(chunk_id="a", source="Guideline.pdf",
                                            section="A", pages="1")])
    second = _slide(["b"], sources=[Citation(chunk_id="b", source="Guideline.pdf",
                                             section="B", pages="2")])
    path = tmp_path / "deck.pptx"
    render_deck(Deck(title="Demo", slides=[first, second]), path)

    slides = Presentation(path).slides
    assert slides[-1].placeholders[1].text_frame.text == "Guideline.pdf"
    assert "Guideline.pdf, A, pp. 1" in slides[0].notes_slide.notes_text_frame.text
    assert "Guideline.pdf, B, pp. 2" in slides[1].notes_slide.notes_text_frame.text


def test_enriched_prompts_identify_roles_and_require_direct_grounding(monkeypatch):
    captured = []

    def fake_generate(prompt, schema, **kwargs):
        captured.append(prompt)
        if schema is Slide:
            return _slide(["cpg", "web"])
        return SlideGrade(passed=True)

    monkeypatch.setattr(writer, "generate", fake_generate)
    monkeypatch.setattr(critic, "generate", fake_generate)
    entry = OutlineEntry(title="Context", objective="Explain evidence", time_minutes=2)
    chunks = [
        {"id": "cpg", "text": "Clinical recommendation", "source": "CPG",
         "section": "Care", "pages": "2"},
        {"id": "web", "text": "Supporting context", "source": "Registry",
         "section": "Finding", "pages": "", "kind": "supplemental",
         "url": "https://example.org/study"},
    ]
    slide = writer.write_slide(entry, chunks)
    critic.grade_slide(slide, entry, chunks)

    assert all("role: Primary CPG" in prompt for prompt in captured)
    assert all("role: Supplemental source" in prompt for prompt in captured)
    assert "Attribute each supplemental claim" in captured[0]
    assert "require source attribution" in captured[1]


def test_ordinary_prompts_keep_original_source_format(monkeypatch):
    prompts = []
    monkeypatch.setattr(writer, "generate", lambda prompt, schema, **kwargs:
                        prompts.append(prompt) or _slide(["cpg"]))
    entry = OutlineEntry(title="Care", objective="Explain care", time_minutes=2)
    writer.write_slide(entry, [{"id": "cpg", "text": "Recommendation"}])

    assert "chunk_id: cpg\ntext: Recommendation" in prompts[0]
    assert "role:" not in prompts[0]


def test_supplement_cannot_displace_primary_evidence():
    primary = {"id": "cpg", "text": "Guideline evidence"}
    supplement = {"id": "web", "kind": "supplemental", "text": "Supplemental evidence"}
    assert critic.code_checks(_slide(["web"]), [primary, supplement]) == [
        "Use and cite a relevant teaching point from the primary CPG.",
    ]
    assert critic.code_checks(_slide(["web", "cpg"]), [primary, supplement]) == []
    assert critic.code_checks(_slide(["web"]), [supplement]) == []
