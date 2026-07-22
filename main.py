from src.parser import parse_request
from src.contracts import Deck, SlideNote, CriticVerdict
from src.render import render_deck
from src.agents.outliner import outline_deck
from src.agents.writer import add_sources, write_slide
from src.retrieve import retrieve
from src.agents.critic import critique_slide

request = input("What training do you need? ")
spec = parse_request(request)
outline = outline_deck(spec)

print(f"\nProposed outline for '{outline.topic}':")
for e in outline.entries:
    print(f"  {e.time_minutes} min - {e.title}")

answer = input("\nApprove this outline? (y/n) ")
if answer.lower() != "y":
    print("Outline rejected. Stopping.")
    raise SystemExit
slides = []
failed_notes = []
for i, e in enumerate(outline.entries):
    chunks = retrieve(f"{e.title}. {e.objective}")
    feedback = None
    for attempt in range(3):
        slide = write_slide(e, chunks, feedback)
        grade = critique_slide(slide, e, chunks)
        if grade.passed:
            break
        feedback = grade.problems
        print(f"  Attempt {attempt + 1} failed: {grade.problems}")
    if not grade.passed:
        failed_notes.append(SlideNote(slide_index=i, note="; ".join(grade.problems)))
    add_sources(slide, chunks)
    slides.append(slide)

verdict = CriticVerdict(passed=not failed_notes, notes=failed_notes)
if not verdict.passed:
    print("\nFLAGGED FOR HUMAN REVIEW:")
    for note in verdict.notes:
        print(f"  Slide {note.slide_index}: {note.note}")

render_deck(Deck(title=outline.topic, slides=slides), "output/deck.pptx")
print(f"Rendered {len(slides)} slides to output/deck.pptx")

from datetime import datetime
from src.llm import usage_log
import json

stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
with open(f"logs/run_{stamp}.json", "w") as f:
    json.dump(usage_log, f, indent=2)