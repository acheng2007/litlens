from litlens.config import settings
from litlens.providers.local import LocalProvider
from litlens.providers.openai_compatible import OpenAICompatibleProvider

def provider():
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAICompatibleProvider(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
    return LocalProvider()

def answer(question, hits):
    if not hits or hits[0].score < 0.12:
        return {"answer": "I do not have enough evidence in the selected corpus to answer that question.", "citations": [], "supported": False, "usage": {"input_tokens": 0, "output_tokens": 0}}
    context = "\n\n".join(f"[{i+1}] {h.chunk.text}" for i, h in enumerate(hits))
    generated = provider().generate("Answer only from the context. Cite claims with [n]. If unsupported, say so.\n\nQuestion: " + question + "\n\nContext:\n" + context)
    return {"answer": generated.text, "citations": [{"marker": f"[{i+1}]", "source": h.citation, "chunk_id": h.chunk.id} for i, h in enumerate(hits)], "supported": True, "usage": {"input_tokens": generated.input_tokens, "output_tokens": generated.output_tokens}}

