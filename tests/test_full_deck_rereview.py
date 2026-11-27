from hashlib import sha256
import json
from threading import Event
from unittest.mock import Mock

import pytest

from src import llm, ui_jobs
from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, RequestSpec, Slide, SlideGrade
from src.review_context import ReviewInputsError, read_review_inputs
from src.source_pdf import SelectedPdf, SourcePage
from src.supplemental import SupplementalResult
from src.visual_extract import PageReview

from test_ui_workflow import await_status


@pytest.fixture
def saved_deck(tmp_path, monkeypatch):
    page = SourcePage("guide-page-1", 1, "Assessment", "Verified CPG text", "Verified CPG text")
    extra = SourcePage("guide-page-2", 2, "Additional source", "Approved additional text", "Approved additional text")
    source = SelectedPdf("guide.pdf", "Pinned CPG", sha256(b"guide").hexdigest(),
                         (page, extra), (), "pdfium", 2, b"guide")
    visual = PageReview(2, source.filename, source.sha256, "image-hash", "openai", "vision-tool",
                        ("Visual-only marker",), ("Visual-only uncertainty",))
    supplement = {"id": "verified-extra", "source": "Verified reference", "kind": "supplemental",
                  "section": "Approved passage", "pages": "", "text": "Exact reference excerpt",
                  "url": "https://www.resus.org.uk/guidance"}
    spec = RequestSpec(topic="Benchmark", audience="Medics", duration_minutes=30)
    outline = Outline(topic=spec.topic, entries=[
        OutlineEntry(title=f"Teaching slide {i}", objective=f"Teach item {i}", time_minutes=2,
                     required_points=[f"Cover item {i}"], chunk_ids=[page.id]) for i in range(1, 16)
    ])
    monkeypatch.setitem(llm.config, "provider", "anthropic")
    monkeypatch.setitem(llm.config, "visual", {"provider": "openai", "model": "vision-tool"})
    monkeypatch.setattr(outliner, "outline_deck", lambda *_: outline)
    monkeypatch.setattr(ui_jobs, "read_selected_pdf", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(ui_jobs, "review_extracted_page", lambda *_: visual)
    monkeypatch.setattr(ui_jobs, "find_supplemental_sources", lambda *_args, **_kwargs: SupplementalResult((supplement,)))

    def write(entry, chunks, *_args, **_kwargs):
        return Slide(title=entry.title, bullets=[entry.objective], speaker_notes="Teach the supported content.",
                     time_minutes=entry.time_minutes, citations=[chunk["id"] for chunk in chunks])

    monkeypatch.setattr(writer, "write_slide", write)
    monkeypatch.setattr(critic, "critique_slide", lambda *_args, **_kwargs: SlideGrade(passed=True))
    root = tmp_path / "output"
    manager = ui_jobs.RunManager(root)
    try:
        run_id = manager.start(spec, source_path=tmp_path / source.filename, source_extractor="pdfium")
        state = await_status(manager, run_id, "awaiting_approval")
        manager.review_page(run_id, state.revision, 2)
        manager._executor.submit(lambda: None).result(timeout=5)
        manager.find_references(run_id, state.revision, 1, supplement["url"])
        manager._executor.submit(lambda: None).result(timeout=5)
        manager.approve(run_id, manager.snapshot(run_id).revision)
        completed = await_status(manager, run_id, "completed")
        assert completed.rereview_available
    finally:
        manager._executor.shutdown(wait=True)
    return root, completed, outline, source, supplement, visual


def artifact_bytes(artifacts):
    return {path.name: path.read_bytes() for path in (artifacts.deck, artifacts.packet, artifacts.log)}


def test_saved_run_has_fifteen_fresh_verdicts_after_restart(saved_deck, monkeypatch):
    root, original, outline, source, supplement, visual = saved_deck
    old_files = artifact_bytes(original.artifacts)
    manager = ui_jobs.RunManager(root)
    monkeypatch.setitem(llm.config, "provider", "openai")
    monkeypatch.setitem(llm.config, "model", "gpt-6.2-sol")
    for module, method in ((outliner, "outline_deck"), (writer, "write_slide"),
                           (ui_jobs, "review_extracted_page"), (ui_jobs, "read_selected_pdf"),
                           (ui_jobs, "find_supplemental_sources")):
        monkeypatch.setattr(module, method, Mock(side_effect=AssertionError("A saved review uses its saved inputs")))
    started, release = Event(), Event()
    observed = []

    def review(slide, entry, evidence, *, slide_index, attempt):
        if slide_index == 0:
            started.set()
            assert release.wait(5)
        assert attempt == 1 and entry == outline.entries[slide_index]
        assert slide.title == entry.title
        assert evidence[0] == source.as_evidence(source.pages[0])
        assert all(finding not in str(evidence) and finding not in slide.speaker_notes
                   for finding in (*visual.missing, *visual.uncertainties))
        if slide_index == 0:
            assert evidence[1] == supplement
        observed.append(slide_index)
        passed = slide_index != 14
        return SlideGrade(passed=passed, problems=[] if passed else ["Fresh finding"])

    monkeypatch.setattr(critic, "critique_slide", review)
    try:
        saved, = manager.history()
        assert saved.id == original.id and saved.rereview_available
        run_id = manager.review_deck(saved.id)
        assert started.wait(5)
        pending = manager.snapshot(run_id)
        assert pending.status == "rereviewing" and pending.total_slides == 15
        assert pending.artifacts == original.artifacts and pending.review_verdicts == ()
        with pytest.raises(ui_jobs.RunConflict, match="current run"):
            manager.review_deck(saved.id)
        release.set()
        result = await_status(manager, run_id, "completed")
        assert observed == list(range(15))
        assert len(result.review_verdicts) == 15 and sum(v.passed for v in result.review_verdicts) == 14
        assert len(result.flags) == 1 and result.flags[0].slide_index == 14
        assert result.visual_reviews == (visual,) and result.source_extractor == "pdfium"
        assert result.artifacts.deck.read_bytes() == old_files["deck.pptx"]
        assert artifact_bytes(original.artifacts) == old_files
        assert result.rereview_available and result.repair_available and not result.repair_pages_available
        packet = result.artifacts.packet.read_text(encoding="utf-8")
        separate, teaching = packet.split("## Full-deck re-review", 1)
        assert "Visual-only marker" in separate and "Visual-only marker" not in teaching
        assert "## Slide 15: Teaching slide 15" in teaching and "Fresh finding" in teaching
        events = json.loads(result.artifacts.log.read_text(encoding="utf-8"))
        reviewed = [row for row in events if row.get("action") == "full_deck_slide_verdict"]
        assert [row["slide_index"] for row in reviewed] == observed
        started_event = next(e for e in events if e.get("action") == "full_deck_rereview_started")
        assert (started_event["model"], started_event["extractor"]) == ("gpt-6.2-sol", "pdfium")
        assert started_event["visual_notes_excluded"]
        assert len(manager.history()) == 2 and all(run.rereview_available for run in manager.history())
        next_inputs = read_review_inputs(root / ".review-inputs" / f"{result.id}.json", result.artifacts.deck)
        assert next_inputs.outline == outline and next_inputs.chunks[0][1] == supplement
    finally:
        release.set()
        manager._executor.shutdown(wait=True)


def test_failed_or_stale_review_preserves_previous_downloads(saved_deck, monkeypatch):
    root, original, *_ = saved_deck
    manager = ui_jobs.RunManager(root)
    old_files = artifact_bytes(original.artifacts)
    review = Mock(side_effect=[SlideGrade(passed=True), llm.OpenAIProviderError("OpenAI unavailable.")])
    monkeypatch.setattr(critic, "critique_slide", review)
    try:
        run_id = manager.review_deck(original.id)
        result = await_status(manager, run_id, "completed")
        assert review.call_count == 2 and result.completed_slides == 1
        assert "Full-deck review failed. OpenAI unavailable." == result.message
        assert result.review_verdicts == () and not result.repair_available
        assert result.artifacts == original.artifacts and artifact_bytes(result.artifacts) == old_files
        assert len(manager.history()) == 1
        assert {path.name for path in (root / ".review-inputs").glob("*.json")} == {f"{original.id}.json"}

        original.artifacts.deck.write_bytes(old_files["deck.pptx"] + b"modified")
        with pytest.raises(ReviewInputsError, match="could not be validated"):
            manager.review_deck(original.id)
        assert review.call_count == 2
    finally:
        manager._executor.shutdown(wait=True)
