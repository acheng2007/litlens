import json

import httpx
import pytest
import respx

from litlens.config import settings
from litlens.providers.remote import RemoteProvider, remote_embeddings
from litlens.retrieval.embeddings import embed_many


@respx.mock
@pytest.mark.parametrize(
    "provider,path,payload",
    [
        (
            "openai",
            "/chat/completions",
            {
                "choices": [{"message": {"content": '{"claims":[]}'}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        ),
        (
            "ollama",
            "/api/chat",
            {"message": {"content": '{"claims":[]}'}, "prompt_eval_count": 4, "eval_count": 2},
        ),
    ],
)
def test_generation_adapters(monkeypatch, provider, path, payload):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    route = respx.post("http://models.test" + path).mock(
        return_value=httpx.Response(200, json=payload)
    )
    result = RemoteProvider().generate("question")
    assert result.input_tokens == 4 and result.output_tokens == 2
    assert b'"role":"system"' in route.calls[0].request.content
    if provider == "openai":
        assert b'"max_completion_tokens":1000' in route.calls[0].request.content
        assert b'"temperature"' not in route.calls[0].request.content
        assert b'"response_format"' not in route.calls[0].request.content


@respx.mock
@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_configured_json_mode_and_output_budget(monkeypatch, provider):
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    monkeypatch.setattr(settings, "model_max_tokens", 4096)
    monkeypatch.setattr(settings, "model_json_mode", True)
    monkeypatch.setattr(settings, "completion_token_parameter", "max_tokens")
    path = "/chat/completions" if provider == "openai" else "/api/chat"
    payload = (
        {"choices": [{"message": {"content": '{"claims":[]}'}}]}
        if provider == "openai"
        else {"message": {"content": '{"claims":[]}'}}
    )
    route = respx.post("http://models.test" + path).mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert RemoteProvider().generate("Return JSON claims").text == '{"claims":[]}'
    body = json.loads(route.calls[0].request.content)
    if provider == "openai":
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 4096
        assert "max_completion_tokens" not in body
    else:
        assert body["format"] == "json"
        assert body["options"]["num_predict"] == 4096
        assert "response_format" not in body


@respx.mock
def test_retry_and_embedding_validation(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    monkeypatch.setattr(settings, "embedding_dimensions", 2)
    monkeypatch.setattr("litlens.providers.remote.time.sleep", lambda _: None)
    route = respx.post("http://models.test/embeddings").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                json={
                    "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]
                },
            ),
        ]
    )
    assert embed_many(["first", "second"]) == [[1, 0], [0, 1]]
    assert route.call_count == 2
    route.mock(return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]}))
    with pytest.raises(ValueError, match="dimensions"):
        embed_many(["wrong"])


@respx.mock
def test_ollama_embeddings_and_no_auth_retry(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    route = respx.post("http://models.test/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[1, 0]]})
    )
    assert remote_embeddings(["hi"]) == [[1, 0]]
    route.mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        remote_embeddings(["hi"])
    assert route.call_count == 2
