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
