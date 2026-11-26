from pptx import Presentation
from pptx.util import Inches


def _citation_text(c, *, enriched, include_location):
    label = "Supplemental source" if c.kind == "supplemental" else "Primary CPG"
    text = f"{label}: {c.source}" if enriched else c.source
    if include_location:
        text += f", {c.section}"
        if c.pages:
            text += f", pp. {c.pages}"
    return text


def _add_citation(paragraph, citation, *, enriched, include_location=True):
    paragraph.text = _citation_text(citation, enriched=enriched, include_location=include_location)
    if citation.url:
        link = paragraph.add_run()
        link.text = " — Source link"
        link.hyperlink.address = citation.url


def render_deck(deck, path):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    enriched = any(c.kind == "supplemental" for slide in deck.slides for c in slide.sources)
    for s in deck.slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])

        slide.shapes.title.text = s.title
        slide.shapes.title.left = Inches(0.5)
        slide.shapes.title.top = Inches(0.3)
        slide.shapes.title.width = Inches(12.3)
        slide.shapes.title.height = Inches(1.2)

        ph = slide.placeholders[1]
        ph.left = Inches(0.5)
        ph.top = Inches(1.6)
        ph.width = Inches(12.3)
        ph.height = Inches(5.6)

        body = ph.text_frame
        body.text = s.bullets[0]
        for b in s.bullets[1:]:
            body.add_paragraph().text = b
        body.fit_text()

        notes = slide.notes_slide.notes_text_frame
        notes.text = s.speaker_notes
        notes.add_paragraph().text = ""
        for c in s.sources:
            _add_citation(notes.add_paragraph(), c, enriched=enriched)

    refs = []
    seen = set()
    for s in deck.slides:
        for c in s.sources:
            key = (c.kind, c.source, c.url)
            if key not in seen:
                seen.add(key)
                refs.append(c)

    if refs:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "References"
        slide.shapes.title.left = Inches(0.5)
        slide.shapes.title.top = Inches(0.3)
        slide.shapes.title.width = Inches(12.3)
        slide.shapes.title.height = Inches(1.2)

        ph = slide.placeholders[1]
        ph.left = Inches(0.5)
        ph.top = Inches(1.6)
        ph.width = Inches(12.3)
        ph.height = Inches(5.6)

        body = ph.text_frame
        _add_citation(body.paragraphs[0], refs[0], enriched=enriched, include_location=False)
        for r in refs[1:]:
            _add_citation(body.add_paragraph(), r, enriched=enriched, include_location=False)
        body.fit_text()

    prs.save(path)
