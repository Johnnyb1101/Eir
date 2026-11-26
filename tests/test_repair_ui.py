from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import llm, ui_jobs
from src.contracts import Outline, OutlineEntry, RequestSpec, SlideNote
from src.pipeline import RunArtifacts


@pytest.mark.parametrize(
    ("extractor", "provider", "show_pages", "show_url"),
    [
        ("pypdf", "anthropic", True, True),
        ("pypdf", "ollama", True, False),
        (None, "anthropic", False, False),
    ],
)
def test_current_run_submits_flagged_slide_repair_and_displays_outcome(
    tmp_path, monkeypatch, extractor, provider, show_pages, show_url,
):
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    artifacts = RunArtifacts(*(tmp_path / name for name in ("deck.pptx", "packet.md", "run.json")))
    for path in (artifacts.deck, artifacts.packet, artifacts.log):
        path.write_bytes(b"existing artifact")
    initial = ui_jobs.RunSnapshot(
        id="run-id",
        spec=RequestSpec(topic="Assessment", audience="Medics", duration_minutes=6),
        status="completed",
        revision=3,
        total_slides=3,
        outline=Outline(topic="Assessment", entries=[
            OutlineEntry(title=title, objective="Cover the source guidance", time_minutes=2)
            for title in ("First", "Second", "Third")
        ]),
        flags=tuple(SlideNote(slide_index=index, note="Needs review") for index in (0, 0, 2)),
        artifacts=artifacts,
        source_extractor=extractor,
        source_pages=5 if extractor else 0,
    )
    state = initial
    manager = Mock()
    manager.active_id.side_effect = lambda: state.id if state.status == "repairing" else None
    manager.snapshot.side_effect = lambda _: state
    manager.history.return_value = ()

    def start_repair(*_args, **_kwargs):
        nonlocal state
        state = replace(initial, status="repairing", repairing_slide=3, message="Fixing and re-reviewing slide 3.")

    manager.repair_slide.side_effect = start_repair
    monkeypatch.setattr(ui_jobs, "RunManager", lambda _: manager)
    monkeypatch.setitem(llm.config, "provider", provider)
    monkeypatch.setitem(llm.config, "source_folder", str(tmp_path))
    streamlit.cache_resource.clear()

    def control(widgets, label):
        return next(widget for widget in widgets if widget.label == label)

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path))
    app.session_state["selected_run_id"] = initial.id
    try:
        app.run()
        assert not app.exception
        picker = control(app.selectbox, "Flagged slide to repair")
        assert picker.options == ["1. First", "3. Third"]
        picker.set_value(3)
        control(app.text_area, "Correction feedback (optional)").set_value("  Clarify the criteria.  ")
        reference_url = "https://example.org/guide" if show_url else ""
        pages = (2, 5) if show_pages else ()
        if show_pages:
            page_picker = control(app.multiselect, "Additional CPG PDF pages (optional)")
            assert page_picker.options == ["1", "2", "3", "4", "5"]
            assert page_picker.proto.max_selections == 3
            page_picker.set_value(list(pages))
        else:
            assert not app.multiselect
        if show_url:
            control(app.text_input, "Public reference URL (optional)").set_value(reference_url)
        else:
            assert all(widget.label != "Public reference URL (optional)" for widget in app.text_input)
        control(app.button, "Fix and re-review").click().run()
        assert not app.exception
        manager.repair_slide.assert_called_once_with(
            "run-id", 3, 3, "Clarify the criteria.", page_numbers=pages, reference_url=reference_url,
        )
        assert any(element.value == "Fixing and re-reviewing slide 3." for element in app.text)
        assert all(button.label != "Fix and re-review" for button in app.button)
        assert len(app.get("download_button")) == 3

        state = replace(
            initial, revision=4, repair_message="The repair did not pass review.",
            repair_findings=("A criterion still needs a citation.",),
        )
        app.run()
        assert not app.exception
        assert any(element.value == state.repair_message for element in app.text)
        assert any(element.value == state.repair_findings[0] for element in app.text)
        assert len(app.get("download_button")) == 3
    finally:
        streamlit.cache_resource.clear()
