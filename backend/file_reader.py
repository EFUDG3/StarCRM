"""Extract readable text from uploaded files for chat context.

Supports: plain text, CSV, Markdown, PDF, DOCX, XLSX, PPTX.
Images are not text-extractable; returns a stub message instead.
"""
import csv
import io
import logging

log = logging.getLogger("uvicorn.error")

_MAX_TEXT_CHARS = 30_000


def extract_text(data: bytes, content_type: str, filename: str) -> str:
    """Return plain text from file bytes. Truncates to _MAX_TEXT_CHARS."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        text = _extract(data, content_type, ext)
    except Exception as e:
        log.warning("Text extraction failed for %s: %s", filename, e)
        return f"[Could not extract text from {filename}: {e}]"

    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + f"\n\n[Truncated at {_MAX_TEXT_CHARS:,} characters]"
    return text


def _extract(data: bytes, content_type: str, ext: str) -> str:
    if ext in ("txt", "md", "csv") or content_type in ("text/plain", "text/csv", "text/markdown"):
        text = data.decode("utf-8", errors="replace")
        if ext == "csv":
            return _format_csv(text)
        return text

    if ext == "pdf" or content_type == "application/pdf":
        return _extract_pdf(data)

    if ext == "docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx(data)

    if ext in ("xlsx", "xls") or content_type in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ):
        return _extract_xlsx(data)

    if ext == "pptx" or content_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return _extract_pptx(data)

    if ext in ("jpg", "jpeg", "png", "webp") or content_type.startswith("image/"):
        return f"[{filename} is an image file. Text extraction is not available for images.]"

    return f"[Unsupported file type: {ext or content_type}]"


def _format_csv(text: str) -> str:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return "(empty CSV)"
    lines = []
    for i, row in enumerate(rows):
        lines.append(" | ".join(row))
        if i == 0:
            lines.append("-" * len(lines[0]))
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i + 1} ---\n{text.strip()}")
    return "\n\n".join(pages) if pages else "(PDF has no extractable text)"


def _extract_docx(data: bytes) -> str:
    import docx
    doc = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        return "(Document is empty)"
    return "\n\n".join(paragraphs)


def _extract_xlsx(data: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            header = f"=== Sheet: {ws.title} ==="
            sheets.append(header + "\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(sheets) if sheets else "(Spreadsheet is empty)"


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    slides = []
    for i, slide in enumerate(prs.slides):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        texts.append(t)
        if texts:
            slides.append(f"--- Slide {i + 1} ---\n" + "\n".join(texts))
    return "\n\n".join(slides) if slides else "(Presentation has no text)"
