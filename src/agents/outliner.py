from src.contracts import Outline
from src.llm import generate
from pydantic import ValidationError
import json

SYSTEM = """You are a curriculum designer for military medical training.
You return ONLY valid JSON - no other text."""

def sort_key(chunk):
    doc, index = chunk["id"].rsplit("-s", 1)
    return (doc, int(index))

def corpus_view(chunks):
    lines = []
    for c in sorted(chunks, key=sort_key):
        lines.append(f"- {c['id']}  ({c['section']})")
    return "\n".join(lines)

def outline_deck(spec, chunks, human_feedback=None):
    instructor_note = ""
    if human_feedback:
        instructor_note = (f"\nAn instructor rejected the previous outline for this reason:"
                    f"\n{human_feedback}\nWrite a new outline that addresses it.")
    feedback = ""
    n_entries = spec.duration_minutes // 2
    available = corpus_view(chunks)
    for attempt in range(3):
        prompt = f"""Design a slide outline as JSON with keys:
topic (string), entries (list of objects, each with: title (string),
objective (string), time_minutes (integer), provenance (string)).
Return exactly {n_entries} entries, each with time_minutes of 2.

Source material available to the writer:
{available}

Design the outline this audience needs. Do not narrow it to fit the list above.
If the audience needs something these sources do not cover, include it anyway.

For each entry, provenance states where its content comes from.
If it is built on the source material above, list the chunk IDs you used.
If the source material does not cover it, say so plainly and name what you
are drawing on instead - a specific guideline, or your own general knowledge.

Topic: {spec.topic}. Audience: {spec.audience}. Duration: {spec.duration_minutes} minutes.{instructor_note}{feedback}"""
        try:
            outline = generate(prompt, Outline, system=SYSTEM, agent="outliner", attempt=attempt)
        except (ValidationError, json.JSONDecodeError) as err:
            feedback = f"\nYour previous reply was rejected: {err}"
            continue
        total = sum(e.time_minutes for e in outline.entries)
        if abs(total - spec.duration_minutes) <= 1:
            return outline
        feedback = f"\nYour previous outline summed to {total} minutes. It must sum to {spec.duration_minutes}."
    raise ValueError(f"Outline failed timing check 3 times - escalating to human review")