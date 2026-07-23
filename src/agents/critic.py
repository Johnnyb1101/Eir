from pathlib import Path
from src.llm import generate
from src.contracts import SlideGrade

RUBRIC = (Path(__file__).parent / "rubric.md").read_text()

SYSTEM = """You grade one slide of medical training material against the rubric at a time.

RULES:
1.  You do not rewrite it, only grade it.
2.  Judge ONLY from the provided slide, outline entry, and chunk text. Outside knowledge
    does not count, even if the slide says something true, if the cited chunk does not state
    it, that is a "no" on Q2.
3.  Your verdict will be based on the rubric's pass rule, all six yes = passed true;
    any no = passed false, one problem string per "no", naming the question number and what
    specifically failed.
4.  Return ONLY a single valid JSON object with exactly these fields:
    - "passed": a boolean (true or false)
    - "problems": a JSON array of strings, one string per failed question. Use an empty array
    [] if nothing failed. Even a single problem must be inside the array:
    {"problems": ["Q1: ..."]}, never {"problems": "Q1: ..."}"""

def code_checks(slide, chunks):
    problems = []
    ids = [chunk["id"] for chunk in chunks]
    for cite in slide.citations:
        if cite not in ids:
            problems.append(f"Cites {cite}, which is not in the retrieved chunks")
    return problems

def grade_slide(slide, entry, chunks, slide_index=None, attempt=None):
    sources = ""
    for chunk in chunks:
        sources += f"{chunk['id']}: {chunk['text']}\n\n"
    prompt = f"""Grade this slide against the rubric.

Rubric:
{RUBRIC}

Outline entry - title: {entry.title}. Objective: {entry.objective}.
Slide: {slide.model_dump_json()}
Source chunks: {sources}"""
    return generate(prompt, SlideGrade, system=SYSTEM, agent="critic", slide_index=slide_index, attempt=attempt)

def critique_slide(slide, entry, chunks, slide_index=None, attempt=None):
    problems = code_checks(slide, chunks)
    if problems:
        return SlideGrade(passed=False, problems=problems)
    return grade_slide(slide, entry, chunks, slide_index=slide_index, attempt=attempt)