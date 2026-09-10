import re
from dataclasses import dataclass

CHUNKING_VERSION = "sections-v2"


@dataclass
class TextChunk:
    text: str
    page: int = 1
    section: str | None = None


def heading(text: str) -> bool:
    return (
        len(text) < 100
        and "\n" not in text
        and (
            text.isupper()
            or bool(
                re.match(
                    r"^(?:\d+(?:\.\d+)*\.?\s+\S|abstract$|introduction$|methods$|results$|discussion$|conclusions?$)",
                    text,
                    re.I,
                )
            )
        )
    )


def chunk_pages(pages: list[tuple[int, str]], max_chars=1400, overlap=180) -> list[TextChunk]:
    if not 0 <= overlap < max_chars:
        raise ValueError("Require 0 <= overlap < max_chars")
    result, section = [], None
    for page_no, raw in pages:
        current = ""
        for paragraph in filter(None, (p.strip() for p in re.split(r"\n\s*\n", raw))):
            if heading(paragraph):
                if current:
                    result.append(TextChunk(current, page_no, section))
                    current = ""
                section = paragraph
                continue
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if current and len(current) + len(paragraph) + 1 > max_chars:
                result.append(TextChunk(current, page_no, section))
                current = current[-overlap:] if overlap else ""
            current = f"{current} {paragraph}".strip()
            while len(current) > max_chars:
                cut = current.rfind(" ", max_chars // 2, max_chars + 1)
                cut = cut if cut > 0 else max_chars
                result.append(TextChunk(current[:cut], page_no, section))
                current = current[max(1, cut - overlap) :].strip()
        if current:
            result.append(TextChunk(current, page_no, section))
    return result
