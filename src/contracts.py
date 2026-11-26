from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from typing import Literal

class RequestSpec(BaseModel):
    topic: str
    duration_minutes: int = Field(gt=0)
    output_format: Literal["pptx", "docx", "pdf"] = "pptx"
    audience: str

class Citation(BaseModel):
    chunk_id: str
    source: str
    section: str
    pages: str
    kind: Literal["primary", "supplemental"] = "primary"
    url: str | None = None

class Slide(BaseModel):
    title: str
    bullets: list[str]
    speaker_notes: str
    time_minutes: int = Field(gt=0)
    citations: list[str] = Field(min_length=1)
    sources: list[Citation] = []

class Deck(BaseModel):
    title: str
    slides: list[Slide] = Field(min_length=1)

class OutlineEntry(BaseModel):
    model_config = {"extra": "forbid"}
    title: str
    objective: str
    required_points: list[str] = Field(default_factory=list)
    time_minutes: int = Field(gt=0, le=3)
    chunk_ids: list[str] = []
    outside_sources: list[str] = []

    @field_validator("required_points")
    @classmethod
    def valid_required_points(cls, points: list[str]) -> list[str]:
        cleaned = [point.strip() for point in points]
        if any(not point for point in cleaned):
            raise ValueError("Required points must contain substantive text")
        if len({point.casefold() for point in cleaned}) != len(cleaned):
            raise ValueError("Required points must be distinct")
        return cleaned

class Outline(BaseModel):
    topic: str
    entries: list[OutlineEntry] = Field(min_length=1)

class SlideNote(BaseModel):
    slide_index: int = Field(ge=0)
    note: str

class SlideGrade(BaseModel):
    passed: bool
    problems: list[str] = []

class CriticVerdict(BaseModel):
    passed: bool
    notes: list[SlideNote] = []


class VisualTable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    columns: list[str] = Field(min_length=1)
    rows: list[list[str]]
    footnotes: list[str]

    @model_validator(mode="after")
    def matching_columns(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every table row must match its column count")
        return self


class VisualNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class VisualConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    label: str


class VisualInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    text: list[str]
    tables: list[VisualTable]
    nodes: list[VisualNode]
    connections: list[VisualConnection]
    chart_descriptions: list[str]
    uncertainties: list[str]

    @model_validator(mode="after")
    def valid_connections(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Flowchart node IDs must be unique")
        if any(edge.from_id not in ids or edge.to_id not in ids
               for edge in self.connections):
            raise ValueError("Flowchart connections must reference existing nodes")
        if not any((self.text, self.tables, self.nodes,
                    self.chart_descriptions, self.uncertainties)):
            raise ValueError("Return extracted content or explain uncertainty")
        return self
