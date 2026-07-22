from src.contracts import Slide
from src.contracts import Citation
from src.llm import generate

SYSTEM = """You write one slide of medical training material at a time,
for the audience specified in the request.

Rules:
1. Use ONLY the source chunks provided. If the chunks do not support
   a statement, do not write it. If the chunks are thin, write a
   shorter slide. Never add information from your own knowledge,
   even if you are confident it is correct.
2. Each citation must be exactly one chunk ID, copied exactly as
   provided. Cite every chunk you used and no chunk you did not use.
   Never invent a citation.
3. Write speaker notes telling the instructor what to say for this
   slide. Speaker notes follow rule 1: grounded in the chunks only.
4. Return ONLY a single valid JSON object with exactly these fields:
   title, bullets, speaker_notes, time_minutes, citations. No other
   text.
5. Bullets are short cues, not sentences: at most ~10 words each
   no more than 5 bullets per slide. The full detail goes in the speaker
   notes, never on the slide face."""

def write_slide(entry, chunks, feedback=None):
    sources = ""
    for chunk in chunks:
        sources += f"{chunk['id']}: {chunk['text']}\n\n"
    prompt = f"""Write one training slide as JSON with keys:
    title (string), bullets (list of strings), speaker_notes (string),
    time_minutes (integer),
    citations (list of strings)

Title: {entry.title}. Objective: {entry.objective}. Duration: time_minutes must be exactly {entry.time_minutes}. Source chunks: {sources}"""
    if feedback:
        prompt += ("\nA previous attempt at this slide failed review "
                "for these reasons:\n- " + "\n- ".join(feedback) +
                "\nWrite a corrected slide that fixes every problem listed.")
    slide = generate(prompt, Slide, system=SYSTEM, agent="writer")
    return slide

def check_citations(slide, chunks):
    ids = []
    for chunk in chunks:
        ids.append(chunk["id"])
    for cite in slide.citations:
        if cite not in ids:
            raise ValueError(f"Slide cites {cite}, which was not received")
        
def add_sources(slide, chunks):
    for cite in slide.citations:
        for chunk in chunks:
            if chunk["id"] == cite:
                slide.sources.append(Citation(
                    chunk_id=cite,
                    source=chunk["source"],
                    section=chunk["section"],
                    pages=chunk["pages"],
                ))