from src.parser import parse_request
from src.contracts import Deck, Slide
from src.render import render_deck

request = input("What training do you need? ")
spec = parse_request(request)
budget = int(spec.duration_minutes / 1.5)
slides = []
for n in range(budget):
    slides.append(Slide(title=f"Slide {n+1}: {spec.topic}",
                        bullets=["Placeholder content"], speaker_notes="TBD",
                        time_minutes=2, citations=["fake-chunk"]))
render_deck(Deck(title=spec.topic, slides=slides), "output/deck.pptx")
print(f"Rendered {budget} slides on '{spec.topic}' to output/deck.pptx")