from src.ingest import is_heading, clean_heading, keep_chunk, doc_id, doc_title, read_lines, chunk_document

def test_real_headings_are_detected():
    assert is_heading("VACCINE   DOSING ")
    assert is_heading("VACCINATION  AD MINISTRATION  TIME  ")
    assert is_heading("HAEMOPHILUS   INFLUENZAE  TYPE  B  (HIB)  ")

def test_body_lines_are_not_headings():
    assert not is_heading("1. Prevnar 13 (PCV13) AND Pneumovax 23 (PPSV23) are both recommended.8 ")
    assert not is_heading("Guideline Only/Not a Substitute for Clinical Judgment 4 ")
    assert not is_heading("   ")

def test_heading_whitespace_is_collapsed():
    assert clean_heading("VACCINE   DOSING ") == "VACCINE DOSING"
    assert clean_heading("HAEMOPHILUS   INFLUENZAE  TYPE  B  (HIB)  ") == "HAEMOPHILUS INFLUENZAE TYPE B (HIB)"

def test_broken_words_are_left_alone():
    assert clean_heading("VACCINATION  AD MINISTRATION  TIME  ") == "VACCINATION AD MINISTRATION TIME"

def test_empty_parent_headings_are_dropped():
    assert not keep_chunk({"section": "VACCINE DOSING", "text": ""})
    assert not keep_chunk({"section": "PI MONITORING", "text": "\n\n"})

def test_furniture_and_image_only_sections_dropped():
    assert not keep_chunk({"section": "TABLE OF CONTENTS", "text": "Background ... 2"})
    assert not keep_chunk({"section": "APPENDIX A: ALGORITHM", "text": " \n \n\n"})
    assert keep_chunk({"section": "DATA SOURCES", "text": "Number of patients who received vaccines"})

def test_document_identity_comes_from_the_filename():
    path = "corpus/Blunt_Abdominal_Trauma_Splenectomy_Vaccination_13_May_2020_ID09.pdf"
    assert doc_id(path) == "blunt-abdominal-trauma-splenectomy-vaccination-13-may-2020-id09"
    assert doc_title(path) == "Blunt Abdominal Trauma Splenectomy Vaccination 13 May 2020 ID09"

CPG = "corpus/Blunt_Abdominal_Trauma_Splenectomy_Vaccination_13_May_2020_ID09.pdf"

def test_no_chunk_duplicates_or_contains_another():
    kept = []
    for chunk in chunk_document(read_lines(CPG)):
        if keep_chunk(chunk):
            kept.append(chunk["text"])
    for i, a in enumerate(kept):
        for j, b in enumerate(kept):
            if i != j:
                assert a not in b