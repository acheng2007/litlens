import json
import re
from .base import Generation
from litlens.retrieval.search import relevance


class LocalProvider:
    """Deterministic extractive baseline, explicitly not a language model."""
    def generate(self, prompt: str) -> Generation:
        request = json.loads(prompt)
        choices = []
        for source in request["sources"]:
            for sentence in re.split(r"(?<=[.!?])\s+", source["text"]):
                if len(sentence) > 20:
                    choices.append((relevance(request["question"], sentence),
                                    {"quote": sentence, "source": source["source"]}))
        choices.sort(key=lambda item: item[0], reverse=True)
        claims = [item[1] for item in choices[:1] if item[0] >= 0.45]
        return Generation(json.dumps({"claims": claims}))
