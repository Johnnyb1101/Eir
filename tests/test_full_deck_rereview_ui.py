from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import llm, ui_jobs
from src.contracts import RequestSpec, SlideGrade, SlideNote
from src.pipeline import RunArtifacts


@pytest.fixture
def review_app(tmp_path, monkeypatch):
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    bundle = tmp_path / "saved-bundle"
    bundle.mkdir()
    artifacts = RunArtifacts(*(bundle / name for name in ("deck.pptx", "review_packet.md", "run.json")))
    for path in (artifacts.deck, artifacts.packet, artifacts.log):
        path.write_bytes(b"existing artifact")
    initial = ui_jobs.RunSnapshot(
        id="original-worker",
        spec=RequestSpec(topic="Assessment", audience="Medics", duration_minutes=4),
        status="completed",
        message="Draft complete.",
        completed_slides=2,
        total_slides=2,
        flags=(SlideNote(slide_index=0, note="Original flag"),),
        artifacts=artifacts,
        source_extractor="pypdf",
        source_pages=3,
        rereview_available=True,
    )
    saved = ui_jobs.SavedRun(
        id=bundle.name, title="Assessment", created_at="", artifacts=artifacts,
        flags=initial.flags, source_mode="selected_pdf", rereview_available=True,
    )
    manager = Mock()
    manager.active_id.return_value = None
    manager.snapshot.return_value = initial
    manager.history.return_value = (saved,)

    def start_review(_bundle_id):
        manager.active_id.return_value = "review-worker"
        manager.snapshot.return_value = replace(
            initial, id="review-worker", status="rereviewing", completed_slides=1,
            message="Checking the saved deck.", repair_pages_available=False,
        )
        return "review-worker"

    manager.review_deck.side_effect = start_review
    monkeypatch.setattr(ui_jobs, "RunManager", lambda _: manager)
    monkeypatch.setitem(llm.config, "source_folder", str(tmp_path))
    streamlit.cache_resource.clear()
    try:
        yield AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")), manager, initial, saved
    finally:
        streamlit.cache_resource.clear()


def rereview_buttons(app):
    return [button for button in app.button if button.label == "Full-deck re-review"]


@pytest.mark.parametrize("from_current", [False, True])
def test_deck_rereview_starts_from_current_run_or_history(review_app, from_current):
    app, manager, initial, saved = review_app
    if from_current:
        app.session_state["selected_run_id"] = initial.id

    app.run()
    assert not app.exception
    button, = rereview_buttons(app)
    assert not button.disabled
    button.click().run()

    assert not app.exception
    manager.review_deck.assert_called_once_with(saved.id)
    assert app.session_state["selected_run_id"] == "review-worker"
    assert any(progress.proto.text == "Slides re-reviewed: 1 of 2" for progress in app.get("progress"))
    history_button, = rereview_buttons(app)
    assert history_button.disabled
    assert len(app.get("download_button")) == 6
    assert any("Original flag" in item.value for item in app.text)

    manager.active_id.return_value = None
    manager.snapshot.return_value = replace(
        manager.snapshot.return_value,
        status="completed", completed_slides=2, message="Re-review finished.",
        review_verdicts=(SlideGrade(passed=True), SlideGrade(passed=False, problems=["Missing citation."])),
    )
    app.run()

    assert not app.exception
    assert "1 of 2 slides passed · 1 flagged" in [item.value for item in app.text]
    assert "Missing citation." in [item.value for item in app.text]
    assert not app.multiselect
    assert any(button.label == "Fix and re-review" for button in app.button)
    assert len(rereview_buttons(app)) == 1
    assert len(app.get("download_button")) == 6


def test_legacy_saved_run_keeps_flags_and_downloads(review_app):
    app, manager, _, saved = review_app
    manager.history.return_value = (replace(saved, rereview_available=False),)

    app.run()

    assert not app.exception
    assert not rereview_buttons(app)
    assert any("saved slide and evidence snapshots" in item.value for item in app.caption)
    assert any("Original flag" in item.value for item in app.text)
    assert len(app.get("download_button")) == 3
