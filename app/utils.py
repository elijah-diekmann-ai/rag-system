import anyio
from io import BytesIO
from typing import List, Dict, Any
import re

import pdfplumber
import nltk
from nltk.tokenize import sent_tokenize

from app import settings

PARAGRAPH = "paragraph"
TABLE = "table"
HEADER = "header"

def extract_text_from_pdf(file_path: str) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []

    with pdfplumber.open(file_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            # 1. Detect tables and keep their bounding boxes
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]  # (x0, top, x1, bottom)

            # 2. Filter function to remove objects that lie inside any table bbox
            def not_within_tables(obj: Dict[str, Any]) -> bool:
                x0 = obj.get("x0")
                x1 = obj.get("x1")
                top = obj.get("top")
                bottom = obj.get("bottom")

                # If coordinates are missing (e.g. some non-text objects), keep them
                if x0 is None or x1 is None or top is None or bottom is None:
                    return True

                for (tx0, ttop, tx1, tbottom) in table_bboxes:
                    # Basic bbox containment check
                    if x0 >= tx0 and x1 <= tx1 and top >= ttop and bottom <= tbottom:
                        return False
                return True

            # 3. Extract text with tables filtered out (no duplicate ingestion)
            filtered_page = page.filter(not_within_tables)
            text = filtered_page.extract_text(layout=True, x_tolerance=2, y_tolerance=2)

            if text:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                current_para_lines: List[str] = []

                def flush_paragraph():
                    if current_para_lines:
                        elements.append({
                            "type": PARAGRAPH,
                            "text": " ".join(current_para_lines),
                            "page": page_number,
                        })
                        current_para_lines.clear()

                for line in lines:
                    if _looks_like_heading(line):
                        flush_paragraph()
                        elements.append({
                            "type": HEADER,
                            "text": line,
                            "page": page_number,
                        })
                    else:
                        current_para_lines.append(line)

                flush_paragraph()

            # 4. Extract tables once, as Markdown
            for tbl in tables:
                table_rows = tbl.extract()  # list[list[str]]
                table_md = _table_to_markdown(table_rows)
                if table_md:
                    elements.append({
                        "type": TABLE,
                        "text": table_md,
                        "page": page_number,
                    })

    return elements


async def extract_text_from_pdf_async(file_path: str) -> List[Dict[str, Any]]:
    return await anyio.to_thread.run_sync(extract_text_from_pdf, file_path)


def _looks_like_heading(line: str) -> bool:
    # Simple heuristic: short, few punctuation marks, many caps or trailing colon
    if len(line) > 80:
        return False
    if sum(ch in line for ch in ".;!?") > 1:
        return False

    words = line.split()
    if not words:
        return False

    upper_ratio = sum(1 for w in words if w.isupper()) / len(words)
    if upper_ratio >= 0.6:
        return True

    if line.endswith(":"):
        return True

    # Title Case check
    title_case_ratio = sum(1 for w in words if w[:1].isupper()) / len(words)
    return title_case_ratio >= 0.8


def _table_to_markdown(table_rows: List[List[str]]) -> str:
    """
    Serialize a table into Markdown to preserve row/column context.
    """
    normalized = [
        [(_cell or "").strip() for _cell in row]
        for row in table_rows
        if any(cell for cell in row)
    ]
    if not normalized:
        return ""

    header = normalized[0]
    body = normalized[1:] if len(normalized) > 1 else []

    def row_to_md(row: List[str]) -> str:
        return "| " + " | ".join(cell if cell else " " for cell in row) + " |"

    md_lines = [
        row_to_md(header),
        "| " + " | ".join("---" for _ in header) + " |"
    ]
    for row in body:
        md_lines.append(row_to_md(row))

    return "\n".join(md_lines)


def build_chunks(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    buffer: List[str] = []
    buffer_chars = 0
    buffer_page = None

    current_section_title: str | None = None
    section_path: List[str] = []

    def flush_buffer():
        nonlocal buffer, buffer_chars, buffer_page
        if buffer:
            chunk_text = " ".join(buffer).strip()
            if chunk_text:
                chunks.append({
                    "text": chunk_text,
                    "page": buffer_page,
                    "chunk_type": PARAGRAPH,
                    "section_title": current_section_title,
                    "section_path": " > ".join(section_path) if section_path else None,
                    "table_name": None,
                })
            buffer = []
            buffer_chars = 0
            buffer_page = None

    for element in elements:
        etype = element["type"]
        current_page = element.get("page")

        if etype == HEADER:
            flush_buffer()
            title = element["text"].strip()
            current_section_title = title
            section_path.append(title)
            continue

        if etype == TABLE:
            flush_buffer()
            table_text = f"[Table p{current_page}]\n{element['text']}"
            chunks.append({
                "text": table_text,
                "page": current_page,
                "chunk_type": TABLE,
                "section_title": current_section_title,
                "section_path": " > ".join(section_path) if section_path else None,
                "table_name": current_section_title,  # simple heuristic
            })
            continue

        # Paragraph / page text
        paragraph = element["text"]
        for sent in sent_tokenize(paragraph):
            s = sent.strip()
            if not s:
                continue

            if buffer_page is None:
                buffer_page = current_page

            too_many_sentences = len(buffer) >= settings.CHUNK_MAX_SENTENCES
            too_many_chars = buffer_chars + len(s) + 1 > settings.CHUNK_MAX_CHARS

            if buffer and (too_many_sentences or too_many_chars):
                overlap_n = max(0, settings.CHUNK_SENTENCE_OVERLAP)
                overlap = buffer[-overlap_n:] if overlap_n > 0 else []

                flush_buffer()

                if overlap:
                    buffer.extend(overlap)
                    buffer_chars = sum(len(x) + 1 for x in buffer)
                    buffer_page = current_page

            buffer.append(s)
            buffer_chars += len(s) + 1

            if buffer_page is None:
                buffer_page = current_page

    flush_buffer()
    return chunks


def extract_sentences(elements: List[Dict[str, Any]]) -> List[str]:
    """
    DEPRECATED: Use build_chunks instead.
    Accepts structured elements (paragraphs/tables) and flattens them into a list of sentences/chunks.
    """
    sentences: List[str] = []

    for element in elements:
        if element["type"] == TABLE:
            table_text = f"[Table p{element['page']}]\n{element['text']}"
            sentences.append(table_text)
            continue

        paragraph = element["text"]
        paragraph_sentences = sent_tokenize(paragraph)
        sentences.extend(paragraph_sentences)

    return sentences
