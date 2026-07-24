from src.contracts import Outline
from src.llm import generate

SYSTEM = """You are a curriculum designer for military medical training.
You return ONLY valid JSON - no other text."""

def outline_deck(spec):
    feedback = ""
    for attempt in range(3):
        prompt = f"""Design a slide outline as JSON with keys:
topic (string), entries (list of objects, each with: title (string),
objective (string), time_minutes (integer)).
Each entry's time_minutes must be between 1 and 3, typically 2.
The time_minutes of all entries must sum to exactly {spec.duration_minutes}.

Topic: {spec.topic}. Audience: {spec.audience}. Duration: {spec.duration_minutes} minutes.{feedback}"""
        outline = generate(prompt, Outline, system=SYSTEM, agent="outliner", attempt=attempt)
        total = sum(e.time_minutes for e in outline.entries)
        if abs(total - spec.duration_minutes) <= 1:
            return outline
        feedback = f"\nYour previous outline summed to {total} minutes. It must sum to {spec.duration_minutes}."
    raise ValueError(f"Outline failed timing check 3 times - escalating to human review")