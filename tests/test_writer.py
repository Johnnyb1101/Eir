from src.contracts import Citation, Slide, SlideGrade, Deck
from src.agents.writer import add_sources
from src.packet import write_packet


def test_sources_replace_model_metadata_and_remain_stable(tmp_path):
    slide = Slide(
        title="Demo", bullets=["Cue"], speaker_notes="Notes",
        time_minutes=2, citations=["real-id"],
        sources=[Citation(chunk_id="real-id", source="Invented document",
                          section="Invented section", pages="999")],
    )
    chunks = [
        {"id": "real-id", "source": "Real document",
         "section": "Real section", "pages": "2-3"},
        {"id": "unused-id", "source": "Unused document",
         "section": "Unused section", "pages": "4-5"},
    ]

    add_sources(slide, chunks)
    expected = [Citation(chunk_id="real-id", source="Real document",
                         section="Real section", pages="2-3")]
    assert slide.sources == expected
    add_sources(slide, chunks)
    assert slide.sources == expected

    path = tmp_path / "packet.md"
    write_packet(Deck(title="Demo", slides=[slide]),
                 [SlideGrade(passed=True)], [1], path)
    packet = path.read_text(encoding="utf-8")
    assert "Real document, Real section, 2-3" in packet
    assert "Invented" not in packet
    assert "Unused document" not in packet


def test_unmatched_citation_does_not_preserve_model_metadata():
    slide = Slide(
        title="Demo", bullets=["Cue"], speaker_notes="Notes",
        time_minutes=2, citations=["invented-id"],
        sources=[Citation(chunk_id="invented-id", source="Invented document",
                          section="Invented section", pages="999")],
    )

    add_sources(slide, [])

    assert slide.sources == []
