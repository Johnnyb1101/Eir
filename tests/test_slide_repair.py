import json
from threading import Event
from unittest.mock import Mock

import pytest
from pptx import Presentation

from src import llm, ui_jobs
from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, RequestSpec, Slide, SlideGrade
from src.source_pdf import SelectedPdf, SourcePage
from src.supplemental import SupplementalResult
from src.ui_jobs import RunConflict, RunManager
from src.visual_extract import PageReview

from test_ui_workflow import await_status


def test_only_passing_repair_replaces_flagged_slide_and_exports_bundle(tmp_path, monkeypatch):
    first = SourcePage("cpg-page-1", 1, "A", "Original page text.", "Original page text.")
    extra = SourcePage("cpg-page-2", 2, "B", "Additional page text.", "Additional page text.")
    selected = SelectedPdf("guide.pdf", "Test CPG", "source-hash", (first, extra), (), "pdfium", 3, b"")
    visual = PageReview(2, selected.filename, selected.sha256, "image-hash", "anthropic", "vision-test",
                        ("Visual-only diagram finding",), ("Visual-only uncertainty",))
    supplement = {"id": "verified-source", "source": "Outside reference", "text": "Verified content.",
                  "section": "Verified excerpt", "pages": "", "kind": "supplemental",
                  "url": "https://www.resus.org.uk/guidance"}
    reference_url = supplement["url"]
    entries = [OutlineEntry(title=title, objective="Teach triage", time_minutes=2, chunk_ids=[first.id])
               for title in ("Triage", "Communication", "Summary")]
    monkeypatch.setattr(outliner, "outline_deck", lambda spec, *_: Outline(topic=spec.topic, entries=entries))
    source_reader = Mock(return_value=selected)
    monkeypatch.setattr(ui_jobs, "read_selected_pdf", source_reader)
    monkeypatch.setattr(ui_jobs, "review_extracted_page", lambda *_: visual)
    look_up = Mock(return_value=SupplementalResult((supplement,), leads=("Unverified lead",)))
    monkeypatch.setattr(ui_jobs, "find_supplemental_sources", look_up)
    monkeypatch.setitem(llm.config, "provider", "anthropic")
    monkeypatch.setitem(llm.config, "visual", {"provider": "anthropic", "model": "vision-test"})

    repairing = Event()
    release = Event()
    writer_inputs = []
    critic_inputs = []

    def write(entry, chunks, feedback=None, *, slide_index, attempt, current_slide=None, correction=None):
        if current_slide is None:
            return Slide(title=entry.title, bullets=[f"Original slide {slide_index + 1}"],
                         speaker_notes="Original notes", time_minutes=2, citations=[first.id])
        writer_inputs.append((entry, current_slide, chunks, feedback, correction, attempt))
        if correction == "First repair":
            repairing.set()
            assert release.wait(5)
        return Slide(title=entry.title, bullets=[correction], speaker_notes="Revised notes",
                     time_minutes=2, citations=[chunk["id"] for chunk in chunks])

    def review(slide, entry, chunks, *, slide_index, attempt):
        if slide.bullets[0].startswith("Original"):
            passed = slide_index == 2
            return SlideGrade(passed=passed, problems=[] if passed else ["Original flag"])
        critic_inputs.append((slide, entry, chunks, slide_index, attempt))
        passed = slide.bullets[0] != "First repair"
        return SlideGrade(passed=passed, problems=[] if passed else ["Still too strong"])

    monkeypatch.setattr(writer, "write_slide", write)
    monkeypatch.setattr(critic, "critique_slide", review)
    manager = RunManager(tmp_path / "output")
    try:
        spec = RequestSpec(topic="Training", audience="Medics", duration_minutes=6)
        run = manager.start(spec, source_path=tmp_path / selected.filename, source_extractor="pdfium")
        outline = await_status(manager, run, "awaiting_approval")
        manager.review_page(run, outline.revision, 2)
        manager._executor.submit(lambda: None).result(timeout=5)
        manager.approve(run, outline.revision)
        original = await_status(manager, run, "completed")
        old_files = {path.name: path.read_bytes() for path in vars(original.artifacts).values()}
        with pytest.raises(RunConflict, match="still needs review"):
            manager.repair_slide(run, original.revision, 3)
        with pytest.raises(RunConflict, match="unavailable as extracted text"):
            manager.repair_slide(run, original.revision, 1, page_numbers=(3,))

        manager.repair_slide(run, original.revision, 1, "First repair")
        assert repairing.wait(5)
        assert manager.active_id() == run
        with pytest.raises(RunConflict):
            manager.repair_slide(run, original.revision, 1)
        release.set()
        rejected = await_status(manager, run, "completed")
        assert rejected.repair_findings == ("Still too strong",)
        assert rejected.artifacts == original.artifacts and rejected.flags == original.flags
        with pytest.raises(RunConflict, match="Refresh"):
            manager.repair_slide(run, original.revision, 1)

        def fail_export(_draft, artifacts, *_args, **_kwargs):
            artifacts.deck.write_bytes(b"partial staged deck")
            raise OSError("Packet unavailable")

        with monkeypatch.context() as broken:
            broken.setattr(ui_jobs, "export_draft", fail_export)
            manager.repair_slide(run, rejected.revision, 1, "Second repair")
            failed_export = await_status(manager, run, "completed")
        assert failed_export.repair_findings == ("Eir stopped while saving the revised files (OSError).",)
        assert failed_export.artifacts == original.artifacts and failed_export.flags == original.flags
        assert len(manager.history()) == 1

        manager.repair_slide(run, failed_export.revision, 1, "  Second repair  ",
                             page_numbers=(2,), reference_url=reference_url)
        updated = await_status(manager, run, "completed")
        assert updated.flags == (original.flags[1],) and updated.repair_findings == ()
        assert updated.artifacts != original.artifacts
        assert updated.visual_reviews == original.visual_reviews == (visual,)
        new_deck = Presentation(updated.artifacts.deck)
        old_deck = Presentation(original.artifacts.deck)
        assert new_deck.slides[0].placeholders[1].text == "Second repair"
        assert new_deck.slides[1].placeholders[1].text == old_deck.slides[1].placeholders[1].text
        assert {path.name: path.read_bytes() for path in vars(original.artifacts).values()} == old_files
        assert {p.name for p in updated.artifacts.deck.parent.iterdir()} == set(old_files)

        _, supplied_slide, used_chunks, findings, correction, attempt = writer_inputs[-1]
        assert supplied_slide.bullets == ["Original slide 1"]
        assert (findings, correction, attempt) == (["Original flag"], "Second repair", 6)
        assert used_chunks == [selected.as_evidence(first), selected.as_evidence(extra), supplement]
        assert critic_inputs[-1][1:] == (entries[0], used_chunks, 0, 6)
        look_up.assert_called_once_with(entries[0], reference_url=reference_url)
        source_reader.assert_called_once_with(tmp_path / selected.filename, extractor="pdfium")

        packet = updated.artifacts.packet.read_text(encoding="utf-8")
        visual_section, slide_section = packet.split("## Slide 0:", 1)
        assert all(note in visual_section and note not in slide_section for note in (*visual.missing, *visual.uncertainties))
        assert "Primary CPG: Test CPG, B, 2" in slide_section and "Supplemental source: Outside reference" in slide_section
        events = json.loads(updated.artifacts.log.read_text(encoding="utf-8"))
        reviews = [event["passed"] for event in events if event.get("action") == "slide_repair_reviewed"]
        assert reviews == [False, True, True]

        manager.repair_slide(run, updated.revision, 2, "Fix communication")
        final = await_status(manager, run, "completed")
        assert not final.flags
        final_deck = Presentation(final.artifacts.deck)
        assert [slide.placeholders[1].text for slide in list(final_deck.slides)[:3]] == [
            "Second repair", "Fix communication", "Original slide 3",
        ]
        assert {saved.artifacts: saved.flags for saved in manager.history()} == {
            original.artifacts: original.flags, updated.artifacts: updated.flags, final.artifacts: (),
        }
    finally:
        release.set()
        manager._executor.shutdown(wait=True)
