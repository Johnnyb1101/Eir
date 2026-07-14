from src.parser import parse_request
from src.contracts import Deck, Slide
from src.render import render_deck
from src.agents.outliner import outline_deck
from src.agents.writer import add_sources
from src.agents.writer import write_slide
from src.agents.writer import check_citations
from src.retrieve import retrieve

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
for e in outline.entries:
    chunks = retrieve(f"{e.title}. {e.objective}")
    slide = write_slide(e, chunks)
    check_citations(slide, chunks)
    add_sources(slide, chunks)
    slides.append(slide)
render_deck(Deck(title=outline.topic, slides=slides), "output/deck.pptx")
print(f"Rendered {len(slides)} slides to output/deck.pptx")