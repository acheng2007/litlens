import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from litlens.config import settings
from litlens.observability import event, span
from litlens.providers.local import LocalProvider
from litlens.providers.remote import RemoteProvider
from litlens.retrieval.search import relevance

PROMPT_VERSION = "evidence-quotes-v1"
REFUSAL = "I do not have enough evidence in the selected corpus to answer that question."


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    quote: str = Field(min_length=20, max_length=2000)
    source: int = Field(ge=1)


class Claims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[Claim] = Field(max_length=3)


def provider():
    return LocalProvider() if settings.llm_provider == "extractive" else RemoteProvider()


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def answer(question, hits):
    usage = {"input_tokens": 0, "output_tokens": 0}
    result = {
        "answer": REFUSAL,
        "citations": [],
        "supported": False,
        "usage": usage,
        "reason": "insufficient_evidence",
        "prompt_version": PROMPT_VERSION,
    }
    hits = [h for h in hits if h.score >= settings.evidence_threshold]
    if not hits:
        return result
    prompt = json.dumps(
        {
            "question": question,
            "sources": [{"source": i, "text": h.chunk.text} for i, h in enumerate(hits, 1)],
        }
    )
    with span("generation", provider=settings.llm_provider, prompt_version=PROMPT_VERSION):
        generated = provider().generate(prompt)
        usage.update(input_tokens=generated.input_tokens, output_tokens=generated.output_tokens)
        event("generation.usage", **usage)
        try:
            claims = Claims.model_validate_json(generated.text).claims
        except (ValidationError, ValueError):
            result["reason"] = "invalid_provider_output"
            return result
        if not claims:
            return result
        citations, sentences = [], []
        for claim in claims:
            if claim.source > len(hits):
                result["reason"] = "invalid_citation"
                return result
            hit = hits[claim.source - 1]
            quote = normalize(claim.quote)
            # Fail closed: every sentence must be a full stored source sentence, not a fabricated claim.
            source_sentences = {normalize(s) for s in re.split(r"(?<=[.!?])\s+", hit.chunk.text)}
            if (
                quote not in source_sentences
                or relevance(question, quote) < settings.evidence_threshold
            ):
                result["reason"] = "unverified_claim"
                return result
            marker = f"[{len(citations) + 1}]"
            sentences.append(f"{quote} {marker}")
            citations.append(
                {
                    "marker": marker,
                    "source": hit.citation,
                    "title": hit.document.title,
                    "document_id": hit.document.id,
                    "chunk_id": hit.chunk.id,
                    "page": hit.chunk.page,
                    "section": hit.chunk.section,
                    "quote": quote,
                }
            )
        return {
            **result,
            "answer": "\n\n".join(sentences),
            "citations": citations,
            "supported": True,
            "reason": "verified_source_quotes",
        }
