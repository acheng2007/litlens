import hashlib
import math
import re


def embed(text: str, dimensions: int = 384) -> list[float]:
    values = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        values[int.from_bytes(digest[:4], "big") % dimensions] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed_many(texts: list[str]) -> list[list[float]]:
    from litlens.config import settings
    from litlens.observability import span
    from litlens.providers.remote import remote_embeddings

    with span("embeddings", count=len(texts), provider=settings.embedding_provider):
        vectors = (
            [embed(t, settings.embedding_dimensions) for t in texts]
            if settings.embedding_provider == "hash"
            else remote_embeddings(texts)
        )
    if len(vectors) != len(texts):
        raise ValueError("Embedding provider returned wrong vector count")
    for vector in vectors:
        if len(vector) != settings.embedding_dimensions or not all(
            math.isfinite(x) for x in vector
        ):
            raise ValueError("Embedding provider returned invalid dimensions or non-finite values")
        if not any(vector):
            raise ValueError("Embedding provider returned a zero vector")
    return vectors
