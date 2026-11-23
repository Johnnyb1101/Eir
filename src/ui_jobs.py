from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from src.contracts import Outline, RequestSpec, SlideNote
from src.pipeline import (
    OutlineEvidence, PreparedOutline, RunArtifacts, build_deck, export_draft,
    prepare_outline, write_run_log,
)


class RunConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class RunSnapshot:
    id: str
    spec: RequestSpec
    status: str = "outlining"
    revision: int = 1
    message: str = "Preparing the outline."
    outline: Outline | None = None
    sources: tuple[OutlineEvidence, ...] = ()
    completed_slides: int = 0
    total_slides: int = 0
    flags: tuple[SlideNote, ...] = ()
    artifacts: RunArtifacts | None = None


@dataclass(frozen=True)
class SavedRun:
    id: str
    title: str
    created_at: str
    artifacts: RunArtifacts
    flags: tuple[SlideNote, ...] = ()


@dataclass
class _Run:
    snapshot: RunSnapshot
    directory: Path
    log: list[dict] = field(default_factory=list)
    prepared: PreparedOutline | None = None


_ACTIVE = {"outlining", "awaiting_approval", "generating"}
_LOGGER = logging.getLogger(__name__)


def _event(agent, action, **details):
    return {"agent": agent, "action": action, **details,
            "time": datetime.now().astimezone().isoformat(timespec="seconds")}


def _paths(directory):
    return RunArtifacts(directory / "deck.pptx", directory / "review_packet.md",
                        directory / "run.json")


class RunManager:
    def __init__(self, output_root):
        self._output = Path(output_root).resolve()
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eir-run")
        self._current: _Run | None = None

    def active_id(self):
        with self._lock:
            if self._current and self._current.snapshot.status in _ACTIVE:
                return self._current.snapshot.id
        return None

    def snapshot(self, run_id):
        with self._lock:
            return self._get(run_id).snapshot

    def start(self, spec):
        with self._lock:
            if self._current and self._current.snapshot.status in _ACTIVE:
                raise RunConflict("Finish or discard the current run before starting another.")
            run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:12]}"
            directory = self._output / "runs" / run_id
            directory.mkdir(parents=True)
            run = _Run(RunSnapshot(run_id, spec.model_copy(deep=True)), directory)
            run.log.append(_event("human", "run_requested", request=spec.model_dump(mode="json")))
            self._current = run
            self._executor.submit(self._outline, run, None)
            return run_id

    def revise(self, run_id, revision, feedback):
        feedback = feedback.strip()
        if not feedback:
            raise RunConflict("Enter revision feedback before requesting a new outline.")
        with self._lock:
            run = self._awaiting(run_id, revision)
            run.log.append(_event("human", "outline_rejected", note=feedback, revision=revision))
            run.prepared = None
            run.snapshot = replace(run.snapshot, status="outlining", revision=revision + 1,
                                   outline=None, sources=(), total_slides=0,
                                   message="Revising the outline.")
            self._executor.submit(self._outline, run, feedback)

    def approve(self, run_id, revision):
        with self._lock:
            run = self._awaiting(run_id, revision)
            run.log.append(_event("human", "outline_approved", revision=revision))
            run.snapshot = replace(run.snapshot, status="generating",
                                   message=f"Writing slide 1 of {run.snapshot.total_slides}.")
            self._executor.submit(self._generate, run)

    def discard(self, run_id, revision):
        with self._lock:
            run = self._awaiting(run_id, revision)
            run.log.append(_event("human", "outline_discarded", revision=revision))
            self._save_partial_log(run)
            run.prepared = None
            run.snapshot = replace(run.snapshot, status="discarded", message="Outline discarded.")

    def _get(self, run_id):
        if self._current is None or self._current.snapshot.id != run_id:
            raise KeyError(run_id)
        return self._current

    def _awaiting(self, run_id, revision):
        try:
            run = self._get(run_id)
        except KeyError:
            raise RunConflict("This run is no longer active.") from None
        if run.snapshot.status != "awaiting_approval" or run.snapshot.revision != revision:
            raise RunConflict("The outline has changed or was already acted on. Refresh and review it again.")
        return run

    def _outline(self, run, feedback):
        try:
            from src.llm import capture_usage_log, config

            if feedback is None:
                run.log.append(_event("eir", "provider_selected", provider=config["provider"],
                                      model=config["model"]))
            with capture_usage_log(run.log):
                prepared = prepare_outline(run.snapshot.spec, feedback)
        except Exception as error:
            self._fail(run, "preparing the outline", error)
            return
        with self._lock:
            run.prepared = prepared
            run.snapshot = replace(run.snapshot, status="awaiting_approval", outline=prepared.outline,
                                   sources=prepared.evidence, total_slides=len(prepared.outline.entries),
                                   message="Review and approve the outline to continue.")

    def _generate(self, run):
        def update_progress(completed, flag):
            with self._lock:
                total = run.snapshot.total_slides
                message = f"Writing slide {completed + 1} of {total}."
                if completed == total:
                    message = "Exporting the PowerPoint and review files."
                flags = run.snapshot.flags + ((flag,) if flag else ())
                run.snapshot = replace(run.snapshot, completed_slides=completed,
                                       flags=flags, message=message)

        stage = "generating slides"
        try:
            from src.llm import capture_usage_log

            with capture_usage_log(run.log):
                draft = build_deck(run.prepared, on_slide=update_progress)
            stage = "exporting the review files"
            artifacts = _paths(run.directory)
            with TemporaryDirectory(dir=run.directory) as staging:
                staged = _paths(Path(staging))
                export_draft(draft, staged, run.log)
                staged.deck.replace(artifacts.deck)
                staged.packet.replace(artifacts.packet)
                staged.log.replace(artifacts.log)
        except Exception as error:
            self._fail(run, stage, error)
            return
        with self._lock:
            run.prepared = None
            run.snapshot = replace(run.snapshot, status="completed", message="Draft files are ready.",
                                   artifacts=artifacts, flags=draft.flags)

    def _save_partial_log(self, run):
        try:
            write_run_log(run.directory / "partial-usage.json", run.log)
        except OSError as error:
            _LOGGER.error("Could not save Eir run %s log (%s)", run.snapshot.id, type(error).__name__)

    def _fail(self, run, stage, error):
        error_type = type(error).__name__
        _LOGGER.error("Eir run %s stopped while %s (%s)", run.snapshot.id, stage, error_type)
        run.log.append(_event("eir", "run_failed", stage=stage, error_type=error_type))
        self._save_partial_log(run)
        message = f"Eir stopped while {stage} ({error_type})."
        if error_type == "AuthenticationError":
            message = "Anthropic rejected the configured key. Check the Windows user environment and restart Eir."
        elif error_type in {"APIConnectionError", "APITimeoutError", "ConnectionError", "ReadTimeout"}:
            message = "Eir lost its connection to the configured provider. Check that the provider is reachable."
        with self._lock:
            run.prepared = None
            run.snapshot = replace(run.snapshot, status="failed", message=message)

    def history(self):
        saved = []
        runs = self._output / "runs"
        if runs.resolve().parent == self._output:
            for directory in runs.iterdir() if runs.is_dir() else ():
                if directory.is_dir() and directory.resolve().parent == runs:
                    self._collect(saved, directory, _paths(directory))
        for directory in self._output.glob("benchmark-*"):
            if not directory.is_dir() or directory.resolve().parent != self._output:
                continue
            for deck in directory.glob("deck_*.pptx"):
                stamp = deck.stem.removeprefix("deck_")
                artifacts = RunArtifacts(deck, directory / f"review_packet_{stamp}.md",
                                         directory / f"run_{stamp}.json")
                self._collect(saved, directory, artifacts, f"{directory.name}/{stamp}")
        return [entry for _, entry in sorted(saved, key=lambda pair: pair[0], reverse=True)]

    def _collect(self, saved, directory, artifacts, run_id=None):
        files = (artifacts.deck, artifacts.packet, artifacts.log)
        try:
            if not all(path.is_file() and path.resolve().parent == directory.resolve()
                       for path in files):
                return
            with artifacts.packet.open(encoding="utf-8") as packet:
                heading = packet.readline(512).strip()
            title = directory.name
            if heading.startswith("# Review Packet: "):
                title = heading.removeprefix("# Review Packet: ")
            modified = artifacts.log.stat().st_mtime
        except (OSError, UnicodeError):
            return
        date = datetime.fromtimestamp(modified).astimezone().strftime("%Y-%m-%d %H:%M")
        flags = self._read_flags(artifacts.log)
        saved.append((modified, SavedRun(run_id or directory.name, title, date, artifacts, flags)))

    @staticmethod
    def _read_flags(log_path):
        try:
            events = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        last_verdicts = {}
        for event in events if isinstance(events, list) else ():
            if isinstance(event, dict) and event.get("agent") == "verdict":
                index = event.get("slide_index")
                if isinstance(index, int) and index >= 0:
                    last_verdicts[index] = event
        return tuple(
            SlideNote(slide_index=index, note="; ".join(map(str, verdict["problems"])))
            for index, verdict in sorted(last_verdicts.items())
            if verdict.get("passed") is False and isinstance(verdict.get("problems"), list)
        )
