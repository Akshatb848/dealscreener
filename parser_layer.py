"""Modular document parser returning schema-consistent chunks."""

from __future__ import annotations

import io
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Union

# Optional advanced parsers — reuse existing repo dependencies when available
try:
    from markitdown import MarkItDown
except ImportError:  # pragma: no cover
    MarkItDown = None  # type: ignore

try:
    import pymupdf4llm
except ImportError:  # pragma: no cover
    pymupdf4llm = None  # type: ignore


FileInput = Union[str, os.PathLike, BinaryIO, bytes]


@dataclass
class ParsedChunk:
    source_file: str
    page_index: int
    raw_text: str
    extraction_method: str
    confidence_score: float

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_basename(path_or_name: str) -> str:
    name = os.path.basename(path_or_name.replace("\\", "/"))
    name = re.sub(r"[^\w.\- ]", "_", name)
    return name or "document"


def _split_pages(text: str, method: str, confidence: float, source_file: str) -> list[ParsedChunk]:
    if not text or not text.strip():
        return []
    parts = re.split(r"\n(?=#+\s*Page\s+\d+|\f)", text)
    if len(parts) <= 1:
        return [
            ParsedChunk(
                source_file=source_file,
                page_index=0,
                raw_text=text.strip(),
                extraction_method=method,
                confidence_score=confidence,
            )
        ]
    chunks: list[ParsedChunk] = []
    for idx, part in enumerate(parts):
        cleaned = part.strip()
        if cleaned:
            chunks.append(
                ParsedChunk(
                    source_file=source_file,
                    page_index=idx,
                    raw_text=cleaned,
                    extraction_method=method,
                    confidence_score=confidence,
                )
            )
    return chunks


def _parse_plain_text(content: str, source_file: str) -> list[ParsedChunk]:
    return _split_pages(content, "plain_text", 1.0, source_file)


def _parse_with_markitdown(path: str, source_file: str) -> list[ParsedChunk]:
    if MarkItDown is None:
        return []
    try:
        result = MarkItDown().convert(path)
        text = getattr(result, "text_content", "") or ""
        return _split_pages(text, "markitdown", 0.85, source_file)
    except Exception:
        return []


def _parse_pdf_pages_native(path: str, source_file: str) -> list[ParsedChunk]:
    """Extract one chunk per PDF page using PyMuPDF (preferred for pitch decks)."""
    try:
        import fitz  # pymupdf

        doc = fitz.open(path)
        chunks: list[ParsedChunk] = []
        for page_index in range(len(doc)):
            text = doc[page_index].get_text("text").strip()
            if not text:
                continue
            chunks.append(
                ParsedChunk(
                    source_file=source_file,
                    page_index=page_index,
                    raw_text=text,
                    extraction_method="pymupdf_page",
                    confidence_score=0.92,
                )
            )
        doc.close()
        return chunks
    except Exception:
        return []


def _parse_pdf(path: str, source_file: str) -> list[ParsedChunk]:
    page_chunks = _parse_pdf_pages_native(path, source_file)
    if page_chunks:
        return page_chunks

    if pymupdf4llm is not None:
        try:
            text = pymupdf4llm.to_markdown(path, show_progress=False)
            chunks = _split_pages(text, "pymupdf4llm", 0.9, source_file)
            if chunks:
                return chunks
        except Exception:
            pass
    # Fallback: read as binary and attempt plain decode (low confidence)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        decoded = raw.decode("utf-8", errors="ignore")
        if decoded.strip():
            return _split_pages(decoded[:50000], "pdf_fallback", 0.3, source_file)
    except Exception:
        pass
    return []


def parse_document(source: FileInput, filename: str | None = None) -> list[ParsedChunk]:
    """
    Parse a file path or file-like payload into chunks.

    Returns empty list for invalid/empty input. Never executes uploaded scripts.
    """
    temp_path: str | None = None
    cleanup = False

    if isinstance(source, bytes):
        if not source.strip():
            return []
        text = source.decode("utf-8", errors="ignore")
        name = _safe_basename(filename or "upload.txt")
        return _parse_plain_text(text, name)

    if isinstance(source, (io.IOBase,)):
        data = source.read()
        if not data:
            return []
        name = _safe_basename(filename or getattr(source, "name", "upload.bin"))
        if isinstance(data, str):
            return _parse_plain_text(data, name)
        try:
            text = data.decode("utf-8")
            if text.strip():
                return _parse_plain_text(text, name)
        except Exception:
            pass
        return []

    path = os.fspath(source)
    if not path or not os.path.isfile(path):
        return []

    source_file = _safe_basename(filename or path)
    ext = Path(path).suffix.lower()

    if ext in {".txt", ".md", ".csv"}:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return _parse_plain_text(f.read(), source_file)
        except Exception:
            return []

    if ext in {".docx", ".pptx", ".doc", ".ppt"}:
        chunks = _parse_with_markitdown(path, source_file)
        if chunks:
            return chunks
        return []

    if ext == ".pdf":
        return _parse_pdf(path, source_file)

    # Unknown extension: safe plain-text attempt only
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        if text.strip():
            return _parse_plain_text(text, source_file)
    except Exception:
        pass
    return []
