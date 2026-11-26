from src.contracts import Outline
from src.llm import generate
from pydantic import ValidationError
import json

SYSTEM = """You are a curriculum designer for military medical training.
You return ONLY valid JSON - no other text."""

PREVIEW_CHARS = 90

def preview(text):
    return " ".join(text.split())[:PREVIEW_CHARS]

def sort_key(chunk):
    doc, index = chunk["id"].rsplit("-s", 1)
    return (doc, int(index))

def corpus_view(chunks):
    lines = []
    for c in sorted(chunks, key=sort_key):
        lines.append(f"- {c['id']}  ({c['section']}) {preview(c['text'])}")
    return "\n".join(lines)

def classify_entry(entry, shown, retrieved):
    shown_ids = {c["id"] for c in shown}
    retrieved_ids = [c["id"] for c in retrieved]
    fabricated = [cid for cid in entry.chunk_ids if cid not in shown_ids]
    unmatched = [cid for cid in entry.chunk_ids
                 if cid in shown_ids and cid not in retrieved_ids]
    if retrieved and entry.outside_sources:
        state = "PARTLY SOURCED"
    elif retrieved:
        state = "SOURCED"
    else:
        state = "UNSOURCED"
    return {"state": state, "retrieved": retrieved_ids,
            "fabricated": fabricated, "unmatched": unmatched}

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
objective (string), time_minutes (integer), chunk_ids (list of strings),
outside_sources (list of strings), required_points (list of strings)).
Return exactly {n_entries} entries, each with time_minutes of 2.

For every entry, required_points must contain at least one explicit, checkable
content point the slide must cover. Keep separate facts or decisions as separate
points so each can be assessed independently. State distinct, substantive terms
explicitly, and include an acronym expansion when the title or objective implies it.
Do not repeat the objective as a point or use vague instructions such as
"discuss the topic".

Source material available to the writer:
Each line is a chunk ID, its section heading in parentheses, and the opening words of that chunk.
{available}

Design the outline this audience needs. Do not narrow it to fit the list above.
If the audience needs something these sources do not cover, include it anyway.

For each entry, chunk_ids lists the IDs above whose content supports it.
Include an ID only if that chunk genuinely supports the entry.

outside_sources names material you drew on that is NOT in the list above,
and only material a reader could go and find - a specific guideline or
publication, by name. Not "general knowledge", which is never findable.
An empty outside_sources states that this entry rests on the chunks alone.
Both lists may be empty: that states the entry rests on neither.

chunk_ids will be checked mechanically against the list above.
outside_sources cannot be checked and goes to a human reviewer as a lead.

Topic: {spec.topic}. Audience: {spec.audience}. Duration: {spec.duration_minutes} minutes.{instructor_note}{feedback}"""
        try:
            outline = generate(prompt, Outline, system=SYSTEM, agent="outliner", attempt=attempt)
        except (ValidationError, json.JSONDecodeError) as err:
            feedback = f"\nYour previous reply was rejected: {err}"
            continue
        missing_points = [index + 1 for index, entry in enumerate(outline.entries)
                          if not entry.required_points]
        if missing_points:
            feedback = ("\nYour previous outline omitted required_points for entries "
                        f"{missing_points}. Include at least one explicit, checkable "
                        "point for every entry.")
            continue
        total = sum(e.time_minutes for e in outline.entries)
        if abs(total - spec.duration_minutes) <= 1:
            return outline
        feedback = f"\nYour previous outline summed to {total} minutes. It must sum to {spec.duration_minutes}."
    raise ValueError("Outline failed timing or required-points check 3 times - escalating to human review")
