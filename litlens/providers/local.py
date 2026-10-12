import json
import re

from litlens.retrieval.search import relevance

from .base import Generation
from .tracking import active_request


class LocalProvider:
    """Deterministic extractive baseline, explicitly not a language model."""

    def generate(
        self, prompt: str, *, system: str | None = None, model: str | None = None
    ) -> Generation:
        request = json.loads(prompt)
        choices = []
        for source in request["sources"]:
            for sentence in re.split(r"(?<=[.!?])\s+", source["text"]):
                if len(sentence) > 20:
                    choices.append(
                        (
                            relevance(request["question"], sentence),
                            {"quote": sentence, "source": source["source"]},
                        )
                    )
        choices.sort(key=lambda item: item[0], reverse=True)
        claims = [item[1] for item in choices[:1] if item[0] >= 0.45]
        accounting = active_request.get()
        if accounting:
            accounting.record("extractive-v1", "extractive", 0, 0, 1, "success")
        return Generation(json.dumps({"claims": claims}), 0, 0)
