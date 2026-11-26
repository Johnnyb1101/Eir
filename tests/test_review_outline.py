import pytest
from pydantic import ValidationError

from src.agents import outliner
from src.contracts import Outline, OutlineEntry, RequestSpec


def entry(**changes):
    values = {"title": "FAST ultrasound", "objective": "Explain the FAST exam",
              "time_minutes": 2}
    values.update(changes)
    return OutlineEntry(**values)


def test_required_points_are_optional_for_saved_outlines_and_validated_when_present():
    assert entry().required_points == []
    assert OutlineEntry.model_validate({"title": "Legacy", "objective": "Describe care",
                                        "time_minutes": 2}).required_points == []
    assert entry(required_points=["  Define FAST  "]).required_points == ["Define FAST"]
    with pytest.raises(ValidationError, match="substantive text"):
        entry(required_points=[" "])
    with pytest.raises(ValidationError, match="distinct"):
        entry(required_points=["Define FAST", "define fast"])


def test_outliner_retries_missing_points_and_requests_specific_coverage(monkeypatch):
    calls = []

    def generate(prompt, schema, **kwargs):
        calls.append((prompt, kwargs["attempt"]))
        points = [] if len(calls) == 1 else ["Expand FAST as Focused Assessment with Sonography in Trauma"]
        return Outline(topic="Trauma", entries=[entry(required_points=points)])

    monkeypatch.setattr(outliner, "generate", generate)
    spec = RequestSpec(topic="Trauma", duration_minutes=2, audience="Medics")
    result = outliner.outline_deck(spec, [])

    assert result.entries[0].required_points
    assert [attempt for _, attempt in calls] == [0, 1]
    assert "required_points must contain at least one" in calls[0][0]
    assert "acronym expansion" in calls[0][0]
    assert "omitted required_points" in calls[1][0]


def test_outliner_exhausts_retries_when_points_remain_missing(monkeypatch):
    attempts = []

    def generate(prompt, schema, **kwargs):
        attempts.append(kwargs["attempt"])
        return Outline(topic="Trauma", entries=[entry()])

    monkeypatch.setattr(outliner, "generate", generate)
    spec = RequestSpec(topic="Trauma", duration_minutes=2, audience="Medics")
    with pytest.raises(ValueError, match="Outline failed timing or required-points check 3 times"):
        outliner.outline_deck(spec, [])
    assert attempts == [0, 1, 2]
