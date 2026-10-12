import re
from dataclasses import dataclass

from sqlalchemy import select, text

from litlens.config import settings
from litlens.db.models import Chunk, Document, Library
from litlens.observability import event, span
from litlens.providers.tracking import active_request

from .embeddings import embed_many

STOP = set(
    "a an the is are was were how what which when where why does do did can could would should of in on at to for and or with by from it this that about explain describe".split()
)


def terms(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def relevance(query, content):
    query_terms = terms(query)
    return len(query_terms & terms(content)) / max(len(query_terms), 1)


@dataclass
class Hit:
    chunk: Chunk
    document: Document
    score: float
    citation: str
    retrieval_score: float = 0.0


def search(
    session,
    library_id: str,
    query: str,
    limit: int = 8,
    mode: str = "hybrid",
    owner_id: str = "local",
    rerank: bool = True,
) -> list[Hit]:
    def execute(statement, params=None):
        accounting = active_request.get()
        if accounting:
            session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": str(max(1, int(accounting.remaining() * 1000)))},
            )
        result = session.execute(statement, params or {})
        if accounting:
            accounting.remaining()
        return result

    library = execute(
        select(Library).where(Library.id == library_id, Library.owner_id == owner_id)
    ).scalar()
    if library is None:
        raise PermissionError("Library not found")
    if library.embedding_space != settings.embedding_space:
        raise ValueError(
            "Embedding configuration changed; use the original settings or a new library"
        )
    if mode not in {"vector", "keyword", "hybrid"}:
        raise ValueError("Unknown retrieval mode")
    if not terms(query):
        return []
    candidates = min(max(limit * 5, 30), 250)
    params = {"library": library_id, "owner": owner_id, "query": query, "limit": candidates}
    scope = """FROM chunks c JOIN documents d ON d.id = c.document_id
               JOIN libraries l ON l.id = d.library_id
               WHERE d.library_id = :library AND l.owner_id = :owner AND d.status = 'ready'"""
    rankings = []
    with span("retrieval", mode=mode, library_id=library_id):
        if mode in {"vector", "hybrid"}:
            vector = embed_many([query])[0]
            params["vector"] = "[" + ",".join(str(x) for x in vector) + "]"
            # Iterative HNSW scanning avoids starvation after tenant/library filtering.
            execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
            rankings.append(
                execute(
                    text(
                        "SELECT c.id "
                        + scope
                        + " ORDER BY c.embedding <=> CAST(:vector AS vector) LIMIT :limit"
                    ),
                    params,
                )
                .scalars()
                .all()
            )
        if mode in {"keyword", "hybrid"}:
            rankings.append(
                execute(
                    text(
                        "SELECT c.id "
                        + scope
                        + " AND to_tsvector('english', c.text) @@ websearch_to_tsquery('english', :query) "
                        "ORDER BY ts_rank_cd(to_tsvector('english', c.text), websearch_to_tsquery('english', :query)) DESC, c.id LIMIT :limit"
                    ),
                    params,
                )
                .scalars()
                .all()
            )
        fused = {}
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking, 1):
                fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (60 + rank)
        if not fused:
            return []
        rows = execute(select(Chunk, Document).join(Document).where(Chunk.id.in_(fused))).all()
        hits = [
            Hit(
                c,
                d,
                relevance(query, c.text),
                f"{d.title}, p. {c.page}, {c.section or 'Unsectioned'}, chunk {c.id}",
                fused[c.id],
            )
            for c, d in rows
        ]
        # Transparent lexical reranker. Candidate retrieval remains bounded and indexed.
        hits.sort(
            key=lambda h: (h.score if rerank else 0, h.retrieval_score, h.chunk.id), reverse=True
        )
        event("retrieval.results", candidates=len(hits), returned=min(limit, len(hits)))
        return hits[:limit]
