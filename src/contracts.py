from pydantic import BaseModel, Field
from typing import Literal

class RequestSpec(BaseModel):
    topic: str
    duration_minutes: int = Field(gt=0)
    output_format: Literal["pptx", "docx", "pdf"] = "pptx"
    audience: str

class Slide(BaseModel):
    title: str
    bullets: list[str]
    speaker_notes: str
    time_minutes: int = Field(gt=0)
    citations: list[str] = Field(min_length=1)

class Deck(BaseModel):
    title: str
    slides: list[Slide] = Field(min_length=1)

class OutlineEntry(BaseModel):
    title: str
    objective: str
    time_minutes: int = Field(gt=0)

class Outline(BaseModel):
    topic: str
    entries: list[OutlineEntry] = Field(min_length=1)

class SlideNote(BaseModel):
    slide_index: int = Field(ge=0)
    note: str

class CriticVerdict(BaseModel):
    passed: bool
    notes: list[SlideNote] = []