from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from src.contracts import OutlineEntry
from src.ingest import (
    MIN_PAGE_TEXT_CHARS, doc_id, doc_title, find_furniture, find_headings, furniture_key, read_toc,
)


_STOP_WORDS = frozenset({
    "about", "and", "are", "can", "describe", "for", "from", "how", "identify", "into",
    "its", "may", "recognize", "state", "that", "the", "their", "these", "this", "those",
    "understand", "use", "what", "when", "which", "with", "would",
})


def _terms(text):
    return {term for term in re.findall(r"[a-z0-9]{3,}", text.casefold()) if term not in _STOP_WORDS}


class SourcePdfError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePage:
    id: str
    number: int
    section: str
    text: str
    preview_text: str


@dataclass(frozen=True)
class SelectedPdf:
    filename: str
    title: str
    sha256: str
    pages: tuple[SourcePage, ...]
    warnings: tuple[str, ...]
    extractor: str
    page_count: int
    pdf_bytes: bytes = field(repr=False)

    def as_evidence(self, page):
        return {"id": page.id, "source": self.title, "section": page.section,
                "pages": str(page.number), "text": page.text}

    def outline_evidence(self):
        return [{**self.as_evidence(page), "text": page.preview_text} for page in self.pages]

    def evidence_for(self, entry: OutlineEntry, limit=3):
        requested = set(entry.chunk_ids)
        query = _terms(f"{entry.title} {entry.objective}")
        overlaps = [query & _terms(f"{page.section}\n{page.preview_text}") for page in self.pages]
        frequency = Counter(term for overlap in overlaps for term in overlap)
        matches = []
        for page, overlap in zip(self.pages, overlaps, strict=True):
            selected = page.id in requested
            if selected or overlap:
                score = sum(1 / frequency[term] for term in sorted(overlap))
                matches.append((not selected, -score, page.number, page))
        return [self.as_evidence(match[-1]) for match in sorted(matches)[:limit]]


def list_source_pdfs(folder):
    root = Path(folder).resolve(strict=True)
    files = (path for path in root.iterdir() if path.suffix.casefold() == ".pdf" and path.is_file()
             and path.resolve().parent == root)
    return tuple(sorted(files, key=lambda path: (path.name.casefold(), path.name)))


def _extract_pypdf(data):
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise SourcePdfError("The selected PDF is encrypted. Choose a readable copy.")
    for number, page in enumerate(reader.pages, 1):
        yield number, (page.extract_text() or "").strip(), lambda: bool(page.images)


def _extract_pdfium(data):
    import pypdfium2 as pdfium

    from src.visual_extract import PDFIUM_LOCK

    with PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as error:
            if error.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
                raise SourcePdfError("The selected PDF is encrypted. Choose a readable copy.") from error
            raise SourcePdfError("Eir could not read the selected PDF. Check that the file is available locally.") from error
        try:
            for number in range(1, len(document) + 1):
                page = document[number - 1]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_bounded().strip()
                    finally:
                        textpage.close()
                    yield number, text, lambda: any(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
                finally:
                    page.close()
        except pdfium.PdfiumError as error:
            raise SourcePdfError("Eir could not read the selected PDF. Check that the file is available locally.") from error
        finally:
            document.close()


def read_selected_pdf(path, *, extractor="pypdf"):
    path = Path(path)
    if path.suffix.casefold() != ".pdf":
        raise SourcePdfError("Choose a PDF as the selected source.")
    if extractor not in {"pypdf", "pdfium"}:
        raise SourcePdfError("Choose 'pypdf' or 'pdfium' as the PDF extractor.")

    try:
        data = path.read_bytes()
        extracted = []
        warnings = []
        extract = _extract_pypdf if extractor == "pypdf" else _extract_pdfium
        for number, text, has_images in extract(data):
            extracted.append((number, text))
            if not text:
                warnings.append(f"PDF page {number} has no extractable text. Review the original page.")
            elif len("".join(text.split())) < MIN_PAGE_TEXT_CHARS and has_images():
                warnings.append(f"PDF page {number} has images and little extractable text. Review the original page.")
    except (OSError, PdfReadError) as error:
        raise SourcePdfError("Eir could not read the selected PDF. Check that the file is available locally.") from error

    lines = [(number, line) for number, text in extracted for line in text.splitlines()]
    furniture = set(find_furniture(lines)) if len(extracted) > 2 and lines else set()
    headings = defaultdict(list)
    for index, section in find_headings(lines, read_toc(lines)).items():
        headings[lines[index][0]].append(section)

    pages = []
    current_section = "PDF page"
    prefix = doc_id(path) + "-pdf"
    for number, text in extracted:
        page_headings = headings[number]
        section = current_section
        if page_headings:
            # A page that covers several sections is cited by page only.
            section = page_headings[0] if len(page_headings) == 1 else "PDF page"
            current_section = page_headings[-1]
        if text:
            preview_text = "\n".join(line for line in text.splitlines() if furniture_key(line) not in furniture)
            pages.append(SourcePage(f"{prefix}-s{number}", number, section, text, preview_text))
    if not pages:
        raise SourcePdfError("The selected PDF has no extractable text. Review the original pages.")
    return SelectedPdf(path.name, doc_title(path), sha256(data).hexdigest(), tuple(pages),
                       tuple(warnings), extractor, len(extracted), data)
