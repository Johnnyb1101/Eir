from src.contracts import Deck, Slide, SlideGrade
from src.packet import write_packet

def test_packet_includes_slide_content(tmp_path):
    path = tmp_path / "packet.md"
    slide = Slide(title="Tourniquet application",
                  bullets=["High and tight", "Reassess distal pulse"],
                  speaker_notes="Demonstrate on manikin.",
                  time_minutes=5, citations=["cpg-hem-s3-p12"])
    deck = Deck(title="Test Deck", slides=[slide])
    verdicts = [SlideGrade(passed=True)]
    write_packet(deck, verdicts, path)
    text = path.read_text(encoding="utf-8")
    assert "High and tight" in text
    assert "Reassess distal pulse" in text
    assert "Demonstrate on manikin." in text