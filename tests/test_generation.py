import pytest

from litlens.db.models import Chunk, Document
from litlens.generation.answer import PROMPT_VERSION, answer
from litlens.providers.base import Generation
from litlens.retrieval.search import Hit


@pytest.fixture
def hit():
    return Hit(
        Chunk(
            id="chunk-1",
            document_id="doc-1",
            text="Hybrid retrieval combines keyword and vector candidates.",
            page=3,
            section="METHODS",
        ),
        Document(id="doc-1", title="Study"),
        0.9,
        "Study, p. 3",
    )


def test_cited_answer_and_refusal(hit):
    result = answer("What does hybrid retrieval combine?", [hit])
    assert result["supported"]
    assert result["citations"][0]["chunk_id"] == "chunk-1"
    assert result["citations"][0]["page"] == 3
    assert result["prompt_version"] == PROMPT_VERSION == "evidence-quotes-v1"
    result = answer("Unknown?", [])
    assert not result["supported"] and result["citations"] == []


@pytest.mark.parametrize(
    "output",
    [
        '{"claims":[]}',
        "I do not have enough evidence.",
        '{"claims":[{"quote":"Invented evidence with enough characters.","source":1}]}',
        '{"claims":[{"quote":"Hybrid retrieval combines keyword and vector candidates.","source":9}]}',
        '{"claims":[{"quote":"Hybrid retrieval combines keyword and vector candidates.","source":true}]}',
        '{"claims":[{"quote":"retrieval combines keyword and vector candidates.","source":1}]}',
    ],
)
def test_unverified_provider_output_fails_closed(monkeypatch, hit, output):
    class Fake:
        def generate(self, prompt):
            return Generation(output, 12, 5)

    monkeypatch.setattr("litlens.generation.answer.provider", lambda: Fake())
    result = answer("What does hybrid retrieval combine?", [hit])
    assert not result["supported"] and result["citations"] == []
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 5}


@pytest.mark.parametrize("provider_name", ["ollama", "openai"])
def test_synthesis_generates_paraphrase_with_source(monkeypatch, hit, provider_name):
    import json

    import httpx
    import respx

    from litlens.config import settings

    monkeypatch.setattr(settings, "answer_mode", "synthesis")
    monkeypatch.setattr(settings, "llm_provider", provider_name)
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    output = json.dumps(
        {
            "claims": [
                {
                    "text": "The method merges two candidate sets.",
                    "evidence": [{"quote": hit.chunk.text, "source": 1}],
                }
            ]
        }
    )
    path = "/api/chat" if provider_name == "ollama" else "/chat/completions"
    payload = (
        {"message": {"content": output}}
        if provider_name == "ollama"
        else {"choices": [{"message": {"content": output}}]}
    )
    hit.score = 0  # Paraphrases must not be rejected by a lexical threshold.
    with respx.mock:
        route = respx.post("http://models.test" + path).mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = answer("How are the two candidate sets merged?", [hit])
        request = json.loads(route.calls[0].request.content)
        assert "own words" in request["messages"][0]["content"]
        assert hit.chunk.text in request["messages"][1]["content"]
    assert result["answer"] == "The method merges two candidate sets. [1]"
    assert result["citations"][0]["quote"] == hit.chunk.text
    assert result["prompt_version"] == "evidence-synthesis-v1"
    assert result["reason"] == "generated_answer_with_verified_quotes"


@pytest.mark.parametrize(
    "claim",
    [
        {"text": "An unsupported claim", "evidence": []},
        {
            "text": "An unsupported claim",
            "evidence": [{"quote": "Fabricated passage of sufficient length.", "source": 1}],
        },
        {
            "text": "An unsupported claim",
            "evidence": [
                {"quote": "Hybrid retrieval combines keyword and vector candidates.", "source": 2}
            ],
        },
        {
            "text": "Invented marker [99]",
            "evidence": [
                {"quote": "Hybrid retrieval combines keyword and vector candidates.", "source": 1}
            ],
        },
    ],
)
def test_synthesis_invalid_evidence_fails_closed(monkeypatch, hit, claim):
    import json

    from litlens.config import settings

    monkeypatch.setattr(settings, "answer_mode", "synthesis")
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    class Fake:
        def generate(self, prompt):
            return Generation(json.dumps({"claims": [claim]}), 12, 5)

    monkeypatch.setattr("litlens.generation.answer.provider", lambda: Fake())
    result = answer("Explain the method", [hit])
    assert not result["supported"]
    assert result["citations"] == []
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 5}


def test_synthesis_requires_model(monkeypatch):
    from litlens.config import settings

    monkeypatch.setattr(settings, "answer_mode", "synthesis")
    monkeypatch.setattr(settings, "llm_provider", "extractive")
    with pytest.raises(ValueError, match="Synthesis requires"):
        answer("Explain the method", [])
