from pptx import Presentation
from pptx.util import Inches

def render_deck(deck, path):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for s in deck.slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = s.title
        body = slide.placeholders[1].text_frame
        body.text = s.bullets[0]
        for b in s.bullets[1:]:
            body.add_paragraph().text = b
        slide.notes_slide.notes_text_frame.text = s.speaker_notes
        foot = slide.shapes.add_textbox(Inches(0.5), Inches(7.0), Inches(12.3), Inches(0.4))
        foot.text_frame.text = "Sources: " + ", ".join(s.citations)
    prs.save(path)