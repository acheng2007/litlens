from pathlib import Path

import pymupdf

from litlens.config import settings

from .chunking import chunk_pages


class InvalidPDF(ValueError):
    pass


def extract_pdf(path: str | Path):
    try:
        with pymupdf.open(str(path)) as doc:
            if not doc.is_pdf or doc.needs_pass or len(doc) > settings.max_pdf_pages:
                raise InvalidPDF("PDF is encrypted, invalid, or exceeds the page limit")
            pages, chars = [], 0
            for number, page in enumerate(doc, 1):
                # Blocks preserve paragraph boundaries that plain text extraction discards.
                text = "\n\n".join(
                    b[4].strip() for b in page.get_text("blocks", sort=True) if b[6] == 0
                )
                chars += len(text)
                if chars > settings.max_pdf_chars:
                    raise InvalidPDF("PDF exceeds the extracted text limit")
                pages.append((number, text))
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
        raise InvalidPDF("Unreadable PDF") from exc
    chunks = chunk_pages(pages)
    if not chunks:
        raise InvalidPDF("No extractable text; scanned PDFs require OCR before upload")
    return chunks
