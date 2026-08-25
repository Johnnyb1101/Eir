from src.contracts import OutlineEntry
from src.agents.outliner import classify_entry

def test_id_the_outliner_saw_is_not_fabricated():
    entry = OutlineEntry(
        title="Population of Interest",
        objective="Describe which patients this guideline applies to.",
        time_minutes=2,
        chunk_ids=["s10"],
    )
    result = classify_entry(entry, shown=[{"id": "s10"}], retrieved=[])
    assert result["fabricated"] == []
    assert result["unmatched"] == ["s10"]
    assert result["state"] == "UNSOURCED"

def test_id_never_shown_is_fabricated():
    entry = OutlineEntry(
        title="Splenic Injury Grading",
        objective="Grade splenic injuries using the AAST scale.",
        time_minutes=2,
        chunk_ids=["s99"],
    )
    result = classify_entry(entry, shown=[{"id": "s10"}], retrieved=[])
    assert result["fabricated"] == ["s99"]
    assert result["unmatched"] == []

def test_state_comes_from_retrieval_not_from_the_claim():
    entry = OutlineEntry(
        title="Overwhelming Post-Splenectomy Infection",
        objective="Describe the risk period and mortality of OPSI.",
        time_minutes=2,
    )
    result = classify_entry(entry, shown=[], retrieved=[{"id": "s1"}])
    assert result["state"] == "SOURCED"
    assert result["retrieved"] == ["s1"]

def test_material_plus_outside_source_is_partly_sourced():
    entry = OutlineEntry(
        title="FAST Ultrasound in Abdominal Trauma",
        objective="Explain the role and limitations of the FAST exam.",
        time_minutes=2,
        outside_sources=["EAST Practice Management Guidelines"],
    )
    result = classify_entry(entry, shown=[], retrieved=[{"id": "s0"}])
    assert result["state"] == "PARTLY SOURCED"