from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from pydantic import ValidationError

from src.contracts import Deck, Outline, SlideGrade, SlideNote


@dataclass(frozen=True)
class EvidenceSource:
    id: str
    source: str
    section: str
    pages: str


@dataclass(frozen=True)
class OutlineEvidence:
    state: str
    retrieved: tuple[EvidenceSource, ...]
    fabricated: tuple[str, ...]
    unmatched: tuple[str, ...]


@dataclass(frozen=True)
class PreparedOutline:
    outline: Outline
    evidence: tuple[OutlineEvidence, ...]
    chunks: tuple[list[dict], ...]


@dataclass(frozen=True)
class DeckDraft:
    deck: Deck
    grades: list[SlideGrade]
    attempts: list[int]
    flags: tuple[SlideNote, ...]


@dataclass(frozen=True)
class RunArtifacts:
    deck: Path
    packet: Path
    log: Path


def prepare_outline(spec, feedback=None):
    from src.agents.outliner import classify_entry, outline_deck
    from src.retrieve import document_sections, entry_query, retrieve

    shown = document_sections(retrieve(spec.topic))
    outline = outline_deck(spec, shown, feedback)
    entry_chunks = tuple(retrieve(entry_query(entry)) for entry in outline.entries)
    evidence = []
    for entry, chunks in zip(outline.entries, entry_chunks, strict=True):
        classification = classify_entry(entry, shown, chunks)
        sources = tuple(EvidenceSource(chunk["id"], chunk["source"], chunk["section"],
                                       chunk["pages"]) for chunk in chunks)
        evidence.append(OutlineEvidence(
            classification["state"], sources, tuple(classification["fabricated"]),
            tuple(classification["unmatched"]),
        ))
    return PreparedOutline(outline, tuple(evidence), entry_chunks)


def build_deck(prepared, *, on_retry=None, on_slide=None, refresh_sources=False):
    from src.agents.critic import critique_slide
    from src.agents.writer import add_sources, write_slide
    from src.llm import record_usage

    if refresh_sources:
        from src.retrieve import entry_query, retrieve

    slides = []
    grades = []
    attempts = []
    flags = []
    for index, (entry, chunks) in enumerate(zip(prepared.outline.entries, prepared.chunks, strict=True)):
        if refresh_sources:
            chunks = retrieve(entry_query(entry))
        feedback = None
        slide = None
        for attempt in range(1, 4):
            try:
                slide = write_slide(entry, chunks, feedback, slide_index=index, attempt=attempt)
            except (ValidationError, json.JSONDecodeError) as error:
                feedback = [f"Your previous reply was rejected: {error}"]
                if on_retry:
                    on_retry(attempt, None)
                continue
            grade = critique_slide(slide, entry, chunks, slide_index=index, attempt=attempt)
            record_usage({
                "agent": "verdict", "slide_index": index, "attempt": attempt,
                "passed": grade.passed, "problems": grade.problems,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
            if grade.passed:
                break
            feedback = grade.problems
            if on_retry:
                on_retry(attempt, feedback)
        if slide is None:
            raise ValueError(f"Slide {index} failed contract 3 times - escalating to human review")
        flag = None
        if not grade.passed:
            flag = SlideNote(slide_index=index, note="; ".join(grade.problems))
            flags.append(flag)
        add_sources(slide, chunks)
        slides.append(slide)
        grades.append(grade)
        attempts.append(attempt)
        if on_slide:
            on_slide(index + 1, flag)
    return DeckDraft(Deck(title=prepared.outline.topic, slides=slides), grades, attempts, tuple(flags))


def write_run_log(path, log):
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")


def export_draft(draft, artifacts, log, *, on_rendered=None):
    from src.packet import write_packet
    from src.render import render_deck

    render_deck(draft.deck, artifacts.deck)
    if on_rendered:
        on_rendered()
    write_run_log(artifacts.log, log)
    write_packet(draft.deck, draft.grades, draft.attempts, artifacts.packet)
