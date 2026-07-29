from src.ingest import is_heading

def test_real_headings_are_detected():
    assert is_heading("VACCINE   DOSING ")
    assert is_heading("VACCINATION  AD MINISTRATION  TIME  ")
    assert is_heading("HAEMOPHILUS   INFLUENZAE  TYPE  B  (HIB)  ")

def test_body_lines_are_not_headings():
    assert not is_heading("1. Prevnar 13 (PCV13) AND Pneumovax 23 (PPSV23) are both recommended.8 ")
    assert not is_heading("Guideline Only/Not a Substitute for Clinical Judgment 4 ")
    assert not is_heading("   ")