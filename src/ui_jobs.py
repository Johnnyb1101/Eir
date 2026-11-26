from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from src.contracts import Outline, RequestSpec, SlideNote
from src.pipeline import (
    OutlineEvidence, PreparedOutline, RunArtifacts, attach_supplemental, build_deck, export_draft,
    prepare_outline, write_run_log,
)
from src.source_pdf import SelectedPdf, SourcePdfError, read_selected_pdf
from src.supplemental import find_supplemental_sources, validate_reference_url
from src.visual_extract import PageReview, review_extracted_page


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
    source_name: str | None = None
    source_warnings: tuple[str, ...] = ()
    source_extractor: str | None = None
    source_pages: int = 0
    reviewing_page: int | None = None
    visual_message: str | None = None
    visual_reviews: tuple[PageReview, ...] = ()
    supplementing_slide: int | None = None
    supplemental_message: str | None = None


@dataclass(frozen=True)
class SavedRun:
    id: str
    title: str
    created_at: str
    artifacts: RunArtifacts
    flags: tuple[SlideNote, ...] = ()
    source_mode: str | None = None


@dataclass
class _Run:
    snapshot: RunSnapshot
    directory: Path
    log: list[dict] = field(default_factory=list)
    prepared: PreparedOutline | None = None
    source_path: Path | None = None
    selected_source: SelectedPdf | None = None


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

    def start(self, spec, *, source_path=None, source_extractor="pypdf"):
        with self._lock:
            if self._current and self._current.snapshot.status in _ACTIVE:
                raise RunConflict("Finish or discard the current run before starting another.")
            if source_path is None and source_extractor != "pypdf":
                raise RunConflict("Choose an original PDF to use a PDF text extractor.")
            run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:12]}"
            directory = self._output / "runs" / run_id
            directory.mkdir(parents=True)
            source_path = Path(source_path) if source_path is not None else None
            source_name = source_path.name if source_path is not None else None
            run = _Run(RunSnapshot(run_id, spec.model_copy(deep=True), source_name=source_name,
                                   source_extractor=source_extractor if source_path is not None else None),
                       directory, source_path=source_path)
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
                                   outline=None, sources=(), total_slides=0, supplemental_message=None,
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
            run.selected_source = None
            run.snapshot = replace(run.snapshot, status="discarded", message="Outline discarded.")

    def review_page(self, run_id, revision, page_number):
        from src.llm import config

        with self._lock:
            run = self._awaiting(run_id, revision)
            if run.selected_source is None:
                raise RunConflict("Select an original PDF to review a page visually.")
            if not config.get("visual"):
                raise RunConflict("Configure a visual model before requesting a page review.")
            if type(page_number) is not int or not 1 <= page_number <= run.selected_source.page_count:
                raise RunConflict(f"Choose a PDF page from 1 to {run.selected_source.page_count}.")
            run.snapshot = replace(run.snapshot, reviewing_page=page_number,
                                   visual_message=f"Checking PDF page {page_number} against extracted text.")
            self._executor.submit(self._review, run, page_number)

    def find_references(self, run_id, revision, slide_number, reference_url=""):
        from src.llm import config

        with self._lock:
            run = self._awaiting(run_id, revision)
            if run.selected_source is None or config["provider"] != "anthropic":
                raise RunConflict("Select an original PDF and configure Anthropic to look up references.")
            if type(slide_number) is not int or not 1 <= slide_number <= run.snapshot.total_slides:
                raise RunConflict(f"Choose a slide from 1 to {run.snapshot.total_slides}.")
            url = validate_reference_url(reference_url)
            run.snapshot = replace(run.snapshot, supplementing_slide=slide_number,
                                   supplemental_message=f"Looking for references for slide {slide_number}.")
            self._executor.submit(self._find_references, run, slide_number, url)

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
        if run.snapshot.reviewing_page is not None:
            raise RunConflict("Wait for the active visual check to finish.")
        if run.snapshot.supplementing_slide is not None:
            raise RunConflict("Wait for the active reference lookup to finish.")
        return run

    def _outline(self, run, feedback):
        try:
            from src.llm import capture_usage_log, config

            if feedback is None:
                run.log.append(_event("eir", "provider_selected", provider=config["provider"],
                                      model=config["model"]))
                if run.source_path is not None:
                    run.selected_source = read_selected_pdf(run.source_path, extractor=run.snapshot.source_extractor)
                    run.log.append(_event("eir", "source_selected", source_mode="selected_pdf",
                                          filename=run.selected_source.filename, sha256=run.selected_source.sha256,
                                          extractor=run.selected_source.extractor,
                                          warnings=run.selected_source.warnings))
                    with self._lock:
                        run.snapshot = replace(run.snapshot, source_warnings=run.selected_source.warnings,
                                               source_pages=run.selected_source.page_count)
                else:
                    run.log.append(_event("eir", "source_selected", source_mode="indexed"))
            with capture_usage_log(run.log):
                prepared = prepare_outline(run.snapshot.spec, feedback, selected_source=run.selected_source)
        except Exception as error:
            self._fail(run, "preparing the outline", error)
            return
        with self._lock:
            run.prepared = prepared
            run.snapshot = replace(run.snapshot, status="awaiting_approval", outline=prepared.outline,
                                   sources=prepared.evidence, total_slides=len(prepared.outline.entries),
                                   message="Review and approve the outline to continue.")

    def _review(self, run, page_number):
        from src.llm import capture_usage_log

        try:
            with capture_usage_log(run.log):
                review = review_extracted_page(run.selected_source, page_number)
        except Exception as error:
            kind = type(error).__name__
            _LOGGER.error("Visual review failed for Eir run %s page %s (%s)", run.snapshot.id, page_number, kind)
            run.log.append(_event("eir", "visual_review_failed", page_number=page_number, error_type=kind))
            with self._lock:
                run.snapshot = replace(run.snapshot, reviewing_page=None,
                                       visual_message=f"Visual check for PDF page {page_number} failed ({kind}).")
            return

        run.log.append(_event("eir", "visual_review", **asdict(review)))
        with self._lock:
            reviews = {item.page_number: item for item in run.snapshot.visual_reviews}
            reviews[page_number] = review
            run.snapshot = replace(run.snapshot, reviewing_page=None,
                                   visual_reviews=tuple(reviews[number] for number in sorted(reviews)),
                                   visual_message=f"Unreviewed findings are ready for PDF page {page_number}.")

    def _find_references(self, run, slide_number, reference_url):
        from src.llm import capture_usage_log

        slide_index = slide_number - 1
        entry = run.prepared.outline.entries[slide_index]
        try:
            with capture_usage_log(run.log):
                result = find_supplemental_sources(entry, reference_url=reference_url)
            prepared = attach_supplemental(run.prepared, slide_index, result)
        except Exception as error:
            kind = type(error).__name__
            _LOGGER.error("Reference lookup failed for Eir run %s slide %s (%s)",
                          run.snapshot.id, slide_number, kind)
            run.log.append(_event("eir", "supplemental_lookup_failed", slide_number=slide_number, error_type=kind))
            with self._lock:
                run.snapshot = replace(run.snapshot, supplementing_slide=None,
                                       supplemental_message=f"Reference lookup for slide {slide_number} failed ({kind}).")
            return

        run.log.append(_event("eir", "supplemental_lookup", slide_number=slide_number,
                              revision=run.snapshot.revision, reference_url=reference_url, chunks=result.chunks,
                              leads=result.leads, errors=result.errors))
        if result.chunks:
            message = f"Verified evidence for slide {slide_number} is ready to review."
        elif result.errors:
            message = f"Reference lookup for slide {slide_number} was incomplete. Try again or approve the outline."
        else:
            message = f"No source excerpts could be verified for slide {slide_number}."
        with self._lock:
            run.prepared = prepared
            run.snapshot = replace(run.snapshot, sources=prepared.evidence, revision=run.snapshot.revision + 1,
                                   supplementing_slide=None, supplemental_message=message)

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
                export_draft(draft, staged, run.log, selected_source=run.selected_source,
                             visual_reviews=run.snapshot.visual_reviews)
                staged.deck.replace(artifacts.deck)
                staged.packet.replace(artifacts.packet)
                staged.log.replace(artifacts.log)
        except Exception as error:
            self._fail(run, stage, error)
            return
        with self._lock:
            run.prepared = None
            run.selected_source = None
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
        if isinstance(error, SourcePdfError):
            message = str(error)
        elif error_type == "AuthenticationError":
            message = "Anthropic rejected the configured key. Check the Windows user environment and restart Eir."
        elif error_type in {"APIConnectionError", "APITimeoutError", "ConnectionError", "ReadTimeout"}:
            message = "Eir lost its connection to the configured provider. Check that the provider is reachable."
        with self._lock:
            run.prepared = None
            run.selected_source = None
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
        events = self._read_events(artifacts.log)
        flags = self._flags(events)
        mode = next((event["source_mode"].replace("-", "_") for event in events
                     if isinstance(event.get("source_mode"), str)), None)
        saved.append((modified, SavedRun(run_id or directory.name, title, date, artifacts, flags, mode)))

    @staticmethod
    def _read_events(log_path):
        try:
            events = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        return tuple(event for event in events if isinstance(event, dict)) if isinstance(events, list) else ()

    @staticmethod
    def _flags(events):
        last_verdicts = {}
        for event in events:
            if event.get("agent") == "verdict":
                index = event.get("slide_index")
                if isinstance(index, int) and index >= 0:
                    last_verdicts[index] = event
        return tuple(
            SlideNote(slide_index=index, note="; ".join(map(str, verdict["problems"])))
            for index, verdict in sorted(last_verdicts.items())
            if verdict.get("passed") is False and isinstance(verdict.get("problems"), list)
        )
