from litlens.ingestion.chunking import chunk_pages
from litlens.retrieval.embeddings import cosine, embed


def test_chunking_preserves_page_and_section():
    chunks = chunk_pages([(3, "INTRODUCTION\n\n" + "research matters " * 100)])
    assert chunks and chunks[0].page == 3 and chunks[0].section == "INTRODUCTION"


def test_embedding_is_deterministic_and_normalized():
    vector = embed("hybrid retrieval")
    assert vector == embed("hybrid retrieval")
    assert abs(cosine(vector, vector) - 1) < 1e-6
