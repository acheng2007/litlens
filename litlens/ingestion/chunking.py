import re
from dataclasses import dataclass

@dataclass
class TextChunk:
    text: str
    page: int = 1
    section: str | None = None

def chunk_pages(pages: list[tuple[int, str]], max_chars: int = 1400, overlap: int = 180) -> list[TextChunk]:
    result = []
    for page_no, raw in pages:
        section, current = None, ""
        for paragraph in (p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()):
            if len(paragraph) < 100 and (paragraph.isupper() or re.match(r"^(\d+\.?\s|abstract|introduction|methods|results|discussion|conclusion)", paragraph, re.I)):
                section = paragraph
            if len(current) + len(paragraph) + 1 > max_chars and current:
                result.append(TextChunk(current.strip(), page_no, section))
                current = current[-overlap:] + " " + paragraph
            else:
                current += " " + paragraph
        if current.strip(): result.append(TextChunk(current.strip(), page_no, section))
    return result

