from pathlib import Path
from .chunking import chunk_pages

def extract_pdf(path: str | Path):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install the pdf extra with: pip install -e '.[pdf]'") from exc
    doc = fitz.open(str(path))
    return chunk_pages([(i + 1, page.get_text("text")) for i, page in enumerate(doc)])

