import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import llm
from src.agents import critic
from src.contracts import OutlineEntry, Slide, SlideGrade


SOURCE = {
    "id": "source", "text": "Check airway patency. Assess breathing effort. The signs suggest bleeding.",
}


def objective(point_id, excerpt, *, status="covered"):
    return critic.ObjectiveCheck(point_id=point_id, status=status, lesson_excerpt=excerpt, explanation="")


def content(unit_id, excerpt, *, strength="preserved", chunk_id="source"):
    return critic.ContentCheck(
        unit_id=unit_id, grounding="supported", claim_strength=strength,
        evidence=[critic.SourceExcerpt(chunk_id=chunk_id, excerpt=excerpt)], explanation="",
    )


def review(monkeypatch, *, bullets, notes="", points, objectives, evidence, chunks=(SOURCE,)):
    entry = OutlineEntry(title="Trauma assessment", objective="Explain both parts.",
                         time_minutes=2, required_points=points)
    slide = Slide(title=entry.title, bullets=bullets, speaker_notes=notes, time_minutes=2, citations=["source"])
    title_check = critic.ContentCheck(unit_id="title", grounding="no_claim", claim_strength="not_applicable",
                                      evidence=[], explanation="Descriptive title")
    holistic_checks = [objective(len(points) + offset, bullets[0]) for offset in (1, 2)]
    assessment = critic.SlideAssessment(objective_checks=[*objectives, *holistic_checks],
                                        content_checks=[title_check, *evidence], additional_problems=[])
    monkeypatch.setattr(critic, "generate", lambda *_, **__: assessment)
    return critic.critique_slide(slide, entry, list(chunks))


def test_pass_requires_teaching_every_required_point(monkeypatch):
    inputs = dict(
        points=["Airway", "Breathing"],
        bullets=["Check airway patency."],
        evidence=[content("bullet 1", "Check airway patency.")],
    )
    airway = objective(1, "Check airway patency.")
    omitted = review(monkeypatch, objectives=[airway], **inputs)
    assert not omitted.passed and "omitted the objective check for 2" in omitted.problems[0]
    missing = review(monkeypatch, objectives=[airway, objective(2, "", status="missing")], **inputs)
    assert not missing.passed and "Objective 2 (Breathing)" in missing.problems[0]
    claimed = review(monkeypatch, objectives=[airway, objective(2, "Assess breathing effort.")], **inputs)
    assert not claimed.passed and "Review did not identify an excerpt" in claimed.problems[0]


def test_all_required_points_and_source_excerpts_can_pass(monkeypatch):
    result = review(
        monkeypatch, points=["Airway", "Breathing"], bullets=["Check airway patency."], notes="Assess breathing effort.",
        objectives=[objective(1, "Check airway patency."), objective(2, "Assess breathing effort.")],
        evidence=[content("bullet 1", "Check airway patency."), content("note 1", "Assess   breathing effort.")],
    )
    assert result == SlideGrade(passed=True)


def test_qualified_notes_do_not_rescue_an_overstated_bullet(monkeypatch):
    qualified = "The signs suggest bleeding."
    inputs = dict(points=["Explain what the signs suggest"], notes=qualified,
                  objectives=[objective(1, qualified)])
    failed = review(
        monkeypatch, bullets=["The signs = bleeding"], **inputs,
        evidence=[content("bullet 1", qualified, strength="overstated"), content("note 1", qualified)],
    )
    assert not failed.passed and failed.problems == ["Bullet 1: Claim is unsupported, overstated, or unclear."]
    corrected = review(monkeypatch, bullets=[qualified], **inputs,
                       evidence=[content("bullet 1", qualified), content("note 1", qualified)])
    assert corrected.passed


@pytest.mark.parametrize("invalid", [
    [],
    [content("bullet 1", "Check airway patency."), content("bullet 1", "Check airway patency.")],
    [content("bullet 1", "This phrase is invented.")],
    [content("bullet 1", "Check airway patency.", chunk_id="uncited")],
])
def test_unverifiable_or_incomplete_content_rows_cannot_pass(monkeypatch, invalid):
    result = review(
        monkeypatch, points=["Airway"], bullets=["Check airway patency."],
        objectives=[objective(1, "Check airway patency.")], evidence=invalid,
        chunks=(SOURCE, {"id": "uncited", "text": SOURCE["text"]}),
    )
    assert not result.passed


def test_legacy_outline_reviews_title_and_objective(monkeypatch):
    entry = OutlineEntry(title="Full ABCDE", objective="Describe circulation.", time_minutes=2)
    slide = Slide(title=entry.title, bullets=["Check airway patency."], speaker_notes="",
                  time_minutes=2, citations=["source"])
    prompts = []

    def truncated(prompt, *_, **__):
        prompts.append(prompt)
        raise json.JSONDecodeError("truncated", "", 0)

    monkeypatch.setattr(critic, "generate", truncated)
    assert not critic.critique_slide(slide, entry, [SOURCE]).passed
    assert 'Required objective checks: {"1": "Full ABCDE", "2": "Describe circulation."}' in prompts[0]


def test_structured_anthropic_review_treats_truncation_as_needs_review(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "claude-sonnet-5"})
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(stop_reason="max_tokens", content=[])
    monkeypatch.setattr(llm, "client", client)
    entry = OutlineEntry(title="Airway", objective="Assess airway patency.", time_minutes=2)
    slide = Slide(title=entry.title, bullets=["Check airway patency."], speaker_notes="",
                  time_minutes=2, citations=["source"])

    result = critic.critique_slide(slide, entry, [SOURCE])

    assert not result.passed and "valid complete assessment" in result.problems[0]
    request = client.messages.create.call_args.kwargs
    assert request["max_tokens"] == 16000
    assert request["output_config"]["effort"] == "medium"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert set(request["output_config"]["format"]["schema"]["required"]) == {
        "objective_checks", "content_checks", "additional_problems",
    }
