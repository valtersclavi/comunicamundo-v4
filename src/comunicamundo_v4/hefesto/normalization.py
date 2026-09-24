from __future__ import annotations

from collections import Counter
from io import BytesIO
import re

from pypdf import PdfReader


_BLANKS = re.compile(r"\n[ \t]*\n+")
_SPACES = re.compile(r"[ \t]+")
_HYPHENATED_WRAP = re.compile(r"(?<=\w)-\s*\n\s*(?=[a-zà-öø-ÿ])")


def extract_pdf(data: bytes) -> tuple[str, int]:
    if not data.startswith(b"%PDF"):
        raise ValueError("source is not a PDF")
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ValueError("password required")
        except Exception as error:
            raise ValueError("encrypted PDF cannot be read") from error
    raw_pages = [
        (page.extract_text(extraction_mode="layout") or "").replace("\r", "")
        for page in reader.pages
    ]
    text = normalize_pdf_pages(raw_pages)
    if not text:
        raise ValueError("PDF contains no extractable text; OCR is required")
    return text, len(reader.pages)


def normalize_pdf_pages(raw_pages: list[str]) -> str:
    return "\n\n".join(
        block for blocks in pdf_page_blocks(raw_pages) for block in blocks
    ).strip()


def pdf_page_blocks(raw_pages: list[str]) -> list[list[str]]:
    """Keep native PDF text in reading order without losing page provenance."""
    pages = [_clean_lines(value) for value in raw_pages]
    repeated = _repeated_boundaries(pages)
    result: list[list[str]] = []
    for lines in pages:
        kept = [line for line in lines if _canonical_line(line) not in repeated]
        page_text = "\n".join(kept).strip()
        paragraphs: list[str] = []
        for raw_block in _BLANKS.split(page_text):
            block = _join_wrapped_lines(raw_block)
            if block:
                paragraphs.append(block)
        result.append(paragraphs)
    return result


def normalize_acquired_text(value: str) -> str:
    """Remove transport noise without rewriting acquired source content."""
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = []
    for raw_block in _BLANKS.split(value):
        lines = [line.rstrip() for line in raw_block.splitlines()]
        block = "\n".join(lines).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _clean_lines(value: str) -> list[str]:
    return [line.rstrip() for line in value.splitlines()]


def _repeated_boundaries(pages: list[list[str]]) -> set[str]:
    candidates: list[str] = []
    for lines in pages:
        nonempty = [line for line in lines if line.strip()]
        if nonempty:
            candidates.extend((_canonical_line(nonempty[0]), _canonical_line(nonempty[-1])))
    threshold = max(3, (len(pages) + 1) // 2)
    return {
        value for value, count in Counter(candidates).items()
        if value and count >= threshold
    }


def _canonical_line(value: str) -> str:
    return _SPACES.sub(" ", value.strip())


def _join_wrapped_lines(value: str) -> str:
    value = _HYPHENATED_WRAP.sub("", value.strip())
    lines = [_canonical_line(line) for line in value.splitlines() if line.strip()]
    return " ".join(lines).strip()
