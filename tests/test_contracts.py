from src.contracts import Slide

def test_valid_slide_constructs():
    s = Slide(title="Tourniquet application", bullets=["High and tight"],
              speaker_notes="Demonstrate on manikin.",
              time_minutes=5, citations=["cpg-hem-s3-p12"])
    assert s.citations == ["cpg-hem-s3-p12"]

import pytest
from pydantic import ValidationError

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