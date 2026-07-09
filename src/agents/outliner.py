from src.contracts import Outline
from src.llm import generate

SYSTEM = """You are a curriculum designer for military medical training.
You return ONLY valid JSON - no other text."""

def outline_deck(spec):
    prompt = f"""Design a slide outline as JSON with keys:
topic (string), entries (list of objects, each with: title (string),
objective (string), time_minutes (integer)).
Each entry's time_minutes must be between 1 and 3, typically 2.
The time_minutes of all entries must sum to exactly {spec.duration_minutes}.

Topic: {spec.topic}. Audience: {spec.audience}. Duration: {spec.duration_minutes} minutes."""
    outline = generate(prompt, Outline, system=SYSTEM)
    total = sum(e.time_minutes for e in outline.entries)
    if total != spec.duration_minutes:
        raise ValueError(f"Outline sums to {total} min, need {spec.duration_minutes}")
    return outline