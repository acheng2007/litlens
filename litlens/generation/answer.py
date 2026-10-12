import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from litlens.config import settings
from litlens.observability import event, span
from litlens.providers.local import LocalProvider
from litlens.providers.remote import RemoteProvider
from litlens.providers.tracking import active_request
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


class SynthesizedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=2000, pattern=r"^[^\[\]]+$")
    evidence: list[Claim] = Field(min_length=1, max_length=5)


class Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[SynthesizedClaim] = Field(max_length=5)


def provider():
    return LocalProvider() if settings.llm_provider == "extractive" else RemoteProvider()


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def answer(question, hits):
    synthesis = settings.answer_mode == "synthesis"
    if synthesis and settings.llm_provider == "extractive":
        raise ValueError("Synthesis requires LLM_PROVIDER=ollama or openai")
    prompt_version = "evidence-synthesis-v1" if synthesis else PROMPT_VERSION
    usage = {"input_tokens": 0, "output_tokens": 0}
    result = {
        "answer": REFUSAL,
        "citations": [],
        "supported": False,
        "usage": usage,
        "reason": "insufficient_evidence",
        "prompt_version": prompt_version,
    }
    # Lexical overlap is not a semantic sufficiency test for paraphrased questions.
    hits = list(hits) if synthesis else [h for h in hits if h.score >= settings.evidence_threshold]
    if not hits:
        return result
    prompt = json.dumps(
        {
            "question": question,
            "sources": [{"source": i, "text": h.chunk.text} for i, h in enumerate(hits, 1)],
        }
    )
    with span("generation", provider=settings.llm_provider, prompt_version=prompt_version):
        accounting = active_request.get()
        if accounting:
            accounting.logical_calls += 1
        generated = provider().generate(prompt)
        usage.update(input_tokens=generated.input_tokens, output_tokens=generated.output_tokens)
        event("generation.usage", **usage)
        if synthesis:
            return synthesis_result(generated.text, hits, result)
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


def synthesis_result(output, hits, result):
    """Verify citation provenance; this does not prove semantic entailment."""
    try:
        claims = Synthesis.model_validate_json(output).claims
    except (ValidationError, ValueError):
        return {**result, "reason": "invalid_provider_output"}
    reason = synthesis_provenance(claims, hits)
    if reason:
        return {**result, "reason": reason}
    return render_synthesis(claims, hits, result)


def synthesis_provenance(claims, hits):
    if not claims:
        return "insufficient_evidence"
    for claim in claims:
        for evidence in claim.evidence:
            if evidence.source > len(hits):
                return "invalid_citation"
            quote = normalize(evidence.quote)
            if len(quote) < 20 or quote not in normalize(hits[evidence.source - 1].chunk.text):
                return "unverified_claim"
    return None


def render_synthesis(claims, hits, result):
    """Render already validated structured claims, preserving evidence order."""
    citations, paragraphs = [], []
    for claim in claims:
        markers = []
        for evidence in claim.evidence:
            hit = hits[evidence.source - 1]
            quote = normalize(evidence.quote)
            marker = f"[{len(citations) + 1}]"
            markers.append(marker)
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
        paragraphs.append(f"{claim.text} {' '.join(markers)}")
    return {
        **result,
        "answer": "\n\n".join(paragraphs),
        "citations": citations,
        "supported": True,
        "reason": "generated_answer_with_verified_quotes",
    }
