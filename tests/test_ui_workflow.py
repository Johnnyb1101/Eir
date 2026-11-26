import json
from pathlib import Path
import sys
from time import monotonic, sleep
from types import ModuleType

import pytest

from src.agents import critic, outliner, writer
from src.contracts import Outline, OutlineEntry, RequestSpec, Slide, SlideGrade
from src.llm import record_usage
from src.ui_jobs import RunConflict, RunManager


def await_status(manager, run_id, status):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        result = manager.snapshot(run_id)
        if result.status in {status, "failed"}:
            assert result.status == status, result.message
            return result
        sleep(0.01)
    pytest.fail(f"Expected {status}, found {manager.snapshot(run_id).status}")


@pytest.fixture
def example_pipeline(monkeypatch):
    retriever = ModuleType("src.retrieve")
    source = {"id": "source-s0", "source": "Training guideline", "section": "Assessment",
              "pages": "2-3", "text": "Example content"}
    retriever.retrieve = lambda _: [source]
    retriever.document_sections = lambda chunks: chunks
    retriever.entry_query = lambda entry: entry.objective
    monkeypatch.setitem(sys.modules, "src.retrieve", retriever)

    def outline(spec, chunks, feedback):
        title = "Updated assessment" if feedback else "Initial assessment"
        record_usage({"agent": "outliner", "input_tokens": 3})
        return Outline(topic=spec.topic, entries=[OutlineEntry(
            title=title, objective="Describe the assessment", time_minutes=2, chunk_ids=[source["id"]],
        )])

    def write(entry, chunks, feedback=None, **_):
        record_usage({"agent": "writer", "input_tokens": 5})
        return Slide(title=entry.title, bullets=["Assessment cue"], speaker_notes="Example notes",
                     time_minutes=2, citations=[chunks[0]["id"]])

    monkeypatch.setattr(outliner, "outline_deck", outline)
    monkeypatch.setattr(writer, "write_slide", write)
    monkeypatch.setattr(critic, "critique_slide", lambda *_, **__: SlideGrade(
        passed=False, problems=["Assessment objective needs review"],
    ))


@pytest.fixture
def manager(tmp_path):
    instance = RunManager(tmp_path)
    yield instance
    instance._executor.shutdown(wait=True)


def test_review_revision_progress_and_saved_run(manager, example_pipeline):
    spec = RequestSpec(topic="Trauma training", audience="Medics", duration_minutes=2)
    run_id = manager.start(spec)
    first = await_status(manager, run_id, "awaiting_approval")
    assert first.sources[0].retrieved[0].source == "Training guideline"
    assert first.artifacts is None
    with pytest.raises(RunConflict):
        manager.start(spec)

    manager.revise(run_id, first.revision, "Focus on assessment")
    second = await_status(manager, run_id, "awaiting_approval")
    assert second.outline.entries[0].title == "Updated assessment"
    with pytest.raises(RunConflict):
        manager.approve(run_id, first.revision)
    manager.approve(run_id, second.revision)
    with pytest.raises(RunConflict):
        manager.approve(run_id, second.revision)

    result = await_status(manager, run_id, "completed")
    assert result.completed_slides == result.total_slides == 1
    assert result.flags[0].slide_index == 0
    assert result.artifacts.deck.read_bytes().startswith(b"PK")
    packet = result.artifacts.packet.read_text(encoding="utf-8")
    assert "NEEDS REVIEW" in packet
    assert "Training guideline, Assessment, 2-3" in packet
    events = json.loads(result.artifacts.log.read_text(encoding="utf-8"))
    assert [event.get("action") for event in events if event["agent"] == "human"] == [
        "run_requested", "outline_rejected", "outline_approved",
    ]
    assert sum(event["agent"] == "writer" for event in events) == 3
    assert manager.active_id() is None
    assert manager.history()[0].flags == result.flags


def test_failed_render_stays_out_of_history(manager, example_pipeline, monkeypatch, tmp_path):
    def broken_render(*_):
        raise OSError("example render error")

    monkeypatch.setattr("src.render.render_deck", broken_render)
    run_id = manager.start(RequestSpec(topic="Training", audience="Medics", duration_minutes=2))
    outline = await_status(manager, run_id, "awaiting_approval")
    manager.approve(run_id, outline.revision)
    result = await_status(manager, run_id, "failed")
    assert "exporting" in result.message
    assert "example render error" not in result.message
    assert result.artifacts is None
    assert not manager.history()
    assert manager.active_id() is None
    partial = tmp_path / "runs" / run_id / "partial-usage.json"
    assert json.loads(partial.read_text(encoding="utf-8"))[-1]["action"] == "run_failed"


def test_history_keeps_multiple_benchmarks_within_one_folder(manager, tmp_path):
    benchmark = tmp_path / "benchmark-example"
    benchmark.mkdir()
    for stamp in ("first", "second", "partial"):
        (benchmark / f"deck_{stamp}.pptx").touch()
        (benchmark / f"review_packet_{stamp}.md").write_text(f"# Review Packet: {stamp}", encoding="utf-8")
        if stamp != "partial":
            (benchmark / f"run_{stamp}.json").write_text("[]", encoding="utf-8")
    history = manager.history()
    assert {run.title for run in history} == {"first", "second"}
    assert len({run.id for run in history}) == 2


def test_cli_uses_shared_pipeline_and_existing_output_paths(example_pipeline, monkeypatch, tmp_path, capsys):
    import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "logs").mkdir()
    answers = iter(["Create training", "n", "Focus on assessment", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(main, "parse_request", lambda _: RequestSpec(
        topic="Training", audience="Medics", duration_minutes=2,
    ))
    main.main()

    assert (tmp_path / "output" / "deck.pptx").read_bytes().startswith(b"PK")
    (packet,) = (tmp_path / "output").glob("review_packet_*.md")
    (log,) = (tmp_path / "logs").glob("run_*.json")
    assert packet.stem.removeprefix("review_packet_") == log.stem.removeprefix("run_")
    assert "Updated assessment" in packet.read_text(encoding="utf-8")
    assert "FLAGGED FOR HUMAN REVIEW" in capsys.readouterr().out


def test_streamlit_approval_flow(manager, example_pipeline, monkeypatch):
    streamlit = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    streamlit.cache_resource.clear()
    monkeypatch.setattr("src.ui_jobs.RunManager", lambda _: manager)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=10)
    try:
        app.run()
        assert not app.exception
        app.text_input[0].set_value("Test training")
        app.text_input[1].set_value("Medics")
        app.number_input[0].set_value(2)
        next(button for button in app.button if button.label == "Create outline").click().run()
        run_id = manager.active_id()
        assert run_id is not None
        await_status(manager, run_id, "awaiting_approval")
        app.run()
        assert not app.exception
        next(button for button in app.button if button.label == "Approve outline").click().run()
        await_status(manager, run_id, "completed")
        app.run()
        assert not app.exception
        assert {button.label for button in app.get("download_button")} == {
            "PowerPoint deck", "Review packet", "Audit log",
        }
    finally:
        streamlit.cache_resource.clear()
