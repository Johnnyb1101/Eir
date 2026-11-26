import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter

from src import llm, ui_jobs
from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, Slide, SlideGrade
from src.source_pdf import SelectedPdf, SourcePage
from src.supplemental import SupplementalResult
from src.ui_jobs import RunConflict, RunManager
from src.visual_extract import PageReview


def test_ui_attaches_verified_material_only_to_requested_slide(tmp_path, monkeypatch):
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    pdf = tmp_path / "clinical_source.pdf"
    document = PdfWriter()
    document.add_blank_page(width=200, height=200)
    document.write(pdf)
    page = SourcePage("guide-s1", 1, "Source section", "Primary CPG text", "Primary CPG text")
    selected = SelectedPdf(pdf.name, "Primary guide", "a" * 64, (page,), (), "pdfium", 1, pdf.read_bytes())
    supplemental = {
        "id": "supplement-a", "source": "Publisher guideline", "section": "Initial evaluation",
        "pages": "", "text": "Verified supporting text", "kind": "supplemental", "url": "https://example.com/guide",
    }
    started = Event()
    release = Event()

    def lookup(entry, *, reference_url):
        assert entry.title == "Improve coverage"
        assert reference_url == "https://www.resus.org.uk/library/abcde-approach"
        started.set()
        assert release.wait(8)
        return SupplementalResult((supplemental,), leads=("Unverified suggestion",))

    def write(entry, chunks, *_args, **_kwargs):
        expected = [selected.as_evidence(page)]
        if entry.title == "Improve coverage":
            expected.append(supplemental)
        assert chunks == expected
        return Slide(title=entry.title, bullets=["Source-backed cue"], speaker_notes="Speaker notes",
                     time_minutes=entry.time_minutes, citations=[chunk["id"] for chunk in chunks])

    def wait(check):
        until = monotonic() + 8
        while monotonic() < until:
            snapshot = manager.snapshot(run_id)
            assert snapshot.status != "failed", snapshot.message
            if check(snapshot):
                return snapshot
            sleep(0.02)
        pytest.fail("Eir did not reach the expected state")

    def control(widgets, label):
        return next(widget for widget in widgets if widget.label == label)

    manager = RunManager(tmp_path / "output")
    research = Mock(side_effect=lookup)
    monkeypatch.setitem(llm.config, "source_folder", str(tmp_path))
    monkeypatch.setitem(llm.config, "provider", "anthropic")
    monkeypatch.setitem(llm.config, "visual", {"provider": "anthropic", "model": "visual"})
    monkeypatch.setattr(ui_jobs, "RunManager", lambda _: manager)
    monkeypatch.setattr(ui_jobs, "read_selected_pdf", lambda *_, **__: selected)
    monkeypatch.setattr(ui_jobs, "find_supplemental_sources", research)
    monkeypatch.setattr(ui_jobs, "review_extracted_page", lambda source, number: PageReview(
        number, source.filename, source.sha256, "b" * 64, "anthropic", "visual", ("Visual note only",), (),
    ))
    monkeypatch.setattr(outliner, "outline_deck", lambda spec, *_: Outline(topic=spec.topic, entries=[
        OutlineEntry(title=title, objective="Identify relevant evidence", time_minutes=2, chunk_ids=[page.id])
        for title in ("Keep CPG only", "Improve coverage")
    ]))
    monkeypatch.setattr(writer, "write_slide", write)
    monkeypatch.setattr(critic, "critique_slide", lambda *_, **__: SlideGrade(passed=True))
    streamlit.cache_resource.clear()
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=10)
    try:
        app.run()
        assert not app.exception
        assert not any(button.label == "Find supplemental references" for button in app.button)
        control(app.text_input, "Topic").set_value("Assessment")
        control(app.text_input, "Audience").set_value("Medics")
        control(app.radio, "Source").set_value("One selected PDF")
        control(app.selectbox, "Original PDF").set_value(pdf)
        control(app.selectbox, "Selected PDF text extractor").set_value("PDFium")
        control(app.button, "Create outline").click().run()
        run_id = manager.active_id()
        first = wait(lambda state: state.status == "awaiting_approval")
        app.run()
        research.assert_not_called()
        control(app.button, "Check this page").click().run()
        with_visual = wait(lambda state: state.visual_reviews)
        assert with_visual.sources == first.sources
        app.run()
        control(app.selectbox, "Slide to supplement").set_value(2)
        control(app.text_input, "Reference URL (optional)").set_value("https://www.resus.org.uk/library/abcde-approach")
        control(app.button, "Find supplemental references").click().run()
        assert started.wait(8)
        assert control(app.button, "Approve outline").disabled
        assert control(app.button, "Check this page").disabled
        with pytest.raises(RunConflict, match="active reference lookup"):
            manager.approve(run_id, first.revision)
        release.set()
        researched = wait(lambda state: state.supplementing_slide is None)
        assert researched.outline == first.outline and researched.visual_reviews == with_visual.visual_reviews
        assert researched.sources[0] == first.sources[0]
        assert researched.sources[1].retrieved == first.sources[1].retrieved
        assert researched.sources[1].supplemental[0].url == supplemental["url"]
        with pytest.raises(RunConflict, match="outline has changed"):
            manager.approve(run_id, first.revision)
        app.run()
        assert any("Verified supplemental excerpt" in element.value for element in app.text)
        assert any("Unverified supplemental lead" in element.value for element in app.text)
        control(app.button, "Approve outline").click().run()
        completed = wait(lambda state: state.status == "completed")
        packet = completed.artifacts.packet.read_text(encoding="utf-8")
        visual_section, _, slide_sections = packet.partition("## Slide 0:")
        assert "Visual note only" in visual_section and "Visual note only" not in slide_sections
        assert supplemental["url"] in slide_sections
        assert "Unverified suggestion" not in slide_sections
        events = json.loads(completed.artifacts.log.read_text(encoding="utf-8"))
        result, = [event for event in events if event.get("action") == "supplemental_lookup"]
        assert result["slide_number"] == 2 and result["chunks"] == [supplemental]
        assert result["reference_url"] == "https://www.resus.org.uk/library/abcde-approach"
        assert not app.exception
    finally:
        release.set()
        manager._executor.shutdown(wait=True)
        streamlit.cache_resource.clear()
