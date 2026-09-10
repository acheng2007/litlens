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

def cosine(a: list[float], b: list[float]) -> float: return sum(x * y for x, y in zip(a, b))

