from dataclasses import dataclass, replace
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
    url: str | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class OutlineEvidence:
    state: str
    retrieved: tuple[EvidenceSource, ...]
    fabricated: tuple[str, ...]
    unmatched: tuple[str, ...]
    supplemental: tuple[EvidenceSource, ...] = ()
    supplemental_leads: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedOutline:
    outline: Outline
    evidence: tuple[OutlineEvidence, ...]
    chunks: tuple[list[dict], ...]
    pinned_source: bool = False


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


def prepare_outline(spec, feedback=None, *, selected_source=None):
    from src.agents.outliner import outline_deck

    if selected_source is not None:
        shown = selected_source.outline_evidence()
        outline = outline_deck(spec, shown, feedback)
        return _prepare_evidence(outline, shown, selected_source.evidence_for, pinned_source=True)

    from src.retrieve import document_sections, entry_query, retrieve

    shown = document_sections(retrieve(spec.topic))
    outline = outline_deck(spec, shown, feedback)
    return _prepare_evidence(outline, shown, lambda entry: retrieve(entry_query(entry)))


def prepare_approved_outline(outline, *, selected_source=None, indexed_source=None):
    if (selected_source is None) == (indexed_source is None):
        raise ValueError("Provide exactly one source for the approved outline")
    if selected_source is not None:
        return _prepare_evidence(outline, selected_source.outline_evidence(),
                                 selected_source.evidence_for, pinned_source=True)

    from src.retrieve import document_sections, entry_query, retrieve

    hits = retrieve(outline.topic, source=indexed_source)
    shown = document_sections(hits)
    return _prepare_evidence(outline, shown,
                             lambda entry: retrieve(entry_query(entry), source=indexed_source),
                             pinned_source=True)


def _prepare_evidence(outline, shown, evidence_for, *, pinned_source=False):
    from src.agents.outliner import classify_entry

    entry_chunks = tuple(evidence_for(entry) for entry in outline.entries)
    evidence = []
    for entry, chunks in zip(outline.entries, entry_chunks, strict=True):
        classification = classify_entry(entry, shown, chunks)
        sources = tuple(EvidenceSource(chunk["id"], chunk["source"], chunk["section"],
                                       chunk["pages"]) for chunk in chunks)
        evidence.append(OutlineEvidence(
            classification["state"], sources, tuple(classification["fabricated"]),
            tuple(classification["unmatched"]),
        ))
    return PreparedOutline(outline, tuple(evidence), entry_chunks, pinned_source)


def attach_supplemental(prepared, slide_index, result):
    evidence = list(prepared.evidence)
    chunks = list(prepared.chunks)
    selected = evidence[slide_index]
    if result.chunks:
        # Each successful lookup replaces the previous supplement for this slide.
        primary = [chunk for chunk in chunks[slide_index] if chunk.get("kind") != "supplemental"]
        chunks[slide_index] = [*primary, *result.chunks]
        selected = replace(selected, supplemental=tuple(
            EvidenceSource(chunk["id"], chunk["source"], chunk["section"], chunk["pages"],
                           url=chunk["url"], excerpt=chunk["text"])
            for chunk in result.chunks
        ))
    evidence[slide_index] = replace(selected, supplemental_leads=result.leads)
    return replace(prepared, evidence=tuple(evidence), chunks=tuple(chunks))


def build_deck(prepared, *, on_retry=None, on_slide=None, refresh_sources=False):
    from src.agents.critic import critique_slide
    from src.agents.writer import add_sources, write_slide
    from src.llm import record_usage

    if refresh_sources:
        if prepared.pinned_source:
            raise ValueError("Cannot refresh evidence for a pinned source")
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


def repair_draft_slide(draft, entry, slide_index, chunks, *, correction, attempt):
    from src.agents.critic import critique_slide
    from src.agents.writer import add_sources, write_slide

    primary_ids = {chunk["id"] for chunk in chunks if chunk.get("kind") != "supplemental"}
    if not primary_ids:
        return None, SlideGrade(passed=False, problems=["Add a relevant primary CPG page to repair this slide."])

    slide = write_slide(entry, chunks, draft.grades[slide_index].problems,
                        slide_index=slide_index, attempt=attempt,
                        current_slide=draft.deck.slides[slide_index], correction=correction)
    if slide.time_minutes != entry.time_minutes or not any(bullet.strip() for bullet in slide.bullets):
        return None, SlideGrade(passed=False, problems=["The revision must keep the approved timing and include slide content."])
    if not primary_ids.intersection(slide.citations):
        return None, SlideGrade(passed=False, problems=["The revision needs a citation to the primary CPG."])

    grade = critique_slide(slide, entry, chunks, slide_index=slide_index, attempt=attempt)
    if not grade.passed:
        return None, grade

    add_sources(slide, chunks)
    slides = draft.deck.slides.copy()
    slides[slide_index] = slide
    grades = draft.grades.copy()
    grades[slide_index] = grade
    attempts = draft.attempts.copy()
    attempts[slide_index] = attempt
    flags = tuple(flag for flag in draft.flags if flag.slide_index != slide_index)
    return DeckDraft(draft.deck.model_copy(update={"slides": slides}), grades, attempts, flags), grade


def write_run_log(path, log):
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")


def export_draft(draft, artifacts, log, *, on_rendered=None, selected_source=None, visual_reviews=()):
    from src.packet import write_packet
    from src.render import render_deck

    render_deck(draft.deck, artifacts.deck)
    if on_rendered:
        on_rendered()
    write_run_log(artifacts.log, log)
    write_packet(draft.deck, draft.grades, draft.attempts, artifacts.packet,
                 selected_source=selected_source, visual_reviews=visual_reviews)
