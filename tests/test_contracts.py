from src.contracts import Slide
import pytest
from pydantic import ValidationError
from src.contracts import Citation

def test_valid_slide_constructs():
    s = Slide(title="Tourniquet application", bullets=["High and tight"],
              speaker_notes="Demonstrate on manikin.",
              time_minutes=5, citations=["cpg-hem-s3-p12"])
    assert s.citations == ["cpg-hem-s3-p12"]

def test_slide_rejects_empty_citations():
    with pytest.raises(ValidationError):
        Slide(title="t", bullets=["b"], speaker_notes="n",
              time_minutes=5, citations=[])
        
def test_slide_rejects_negative_time():
    with pytest.raises(ValidationError):
        Slide(title="t", bullets=["b"], speaker_notes="n",
              time_minutes=-1, citations=["cpg-hem-s3-p12"])
        
def test_slide_rejects_zero_time():
    with pytest.raises(ValidationError):
        Slide(title="t", bullets=["b"], speaker_notes="n",
              time_minutes=0, citations=["cpg-hem-s3-p12"])
        
def test_slide_rejects_missing_citations():
    with pytest.raises(ValidationError):
        Slide(title="t", bullets=["b"], speaker_notes="n",
              time_minutes=5)
        
def test_sources_default_and_append():
    slide = Slide(title="t", bullets=["b"], speaker_notes="n",
                  time_minutes=2, citations=["bat-vacc-2020-s0"])
    assert slide.sources == []
    cite = Citation(chunk_id="bat-vacc-2020-s0", source="doc.pdf",
                    section="Background", pages="2-3")
    slide.sources.append(cite)
    assert slide.sources[0].pages == "2-3"