import json
import re
from dataclasses import dataclass
from sqlalchemy import select
from litlens.db.models import Chunk, Document
from .embeddings import cosine, embed

@dataclass
class Hit:
    chunk: Chunk
    document: Document
    score: float
    citation: str

def search(session, library_id: str, query: str, limit: int = 8) -> list[Hit]:
    rows = session.execute(select(Chunk, Document).join(Document).where(Document.library_id == library_id)).all()
    qv, terms, hits = embed(query), set(re.findall(r"\w+", query.lower())), []
    for chunk, document in rows:
        words = set(re.findall(r"\w+", chunk.text.lower()))
        keyword = len(terms & words) / max(len(terms), 1)
        vector = cosine(qv, json.loads(chunk.embedding or "[]")) if chunk.embedding != "[]" else 0.0
        score = 0.55 * vector + 0.45 * keyword
        citation = f"{document.title}, p. {chunk.page}, {chunk.section or 'Unsectioned'}, chunk {chunk.id}"
        hits.append(Hit(chunk, document, score, citation))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:limit]

