import json
import time
from types import SimpleNamespace

import httpx
import pytest
import respx

from litlens.config import ModelPrice, settings
from litlens.generation.research import Research, fuse
from litlens.providers.base import Generation
from litlens.providers.remote import RemoteProvider
from litlens.providers.tracking import Accounting, DeadlineExceeded, active_request
from litlens.retrieval.search import Hit

QUOTE = "Hybrid retrieval combines keyword and vector candidates."
PLAN = {"queries": ["hybrid retrieval"], "document_needs": [], "answer_requirements": []}
DRAFT = {
    "claims": [
        {
            "text": "It combines keyword and vector candidates.",
            "evidence": [{"source": 1, "quote": QUOTE}],
        }
    ]
}


def verdict(status="supported"):
    return {
        "results": [
            {
                "claim_id": "c1",
                "status": status,
                "evidence_refs": [1],
                "explanation": "Both candidate types are stated.",
            }
        ],
        "revision_query": "candidate types",
    }


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    hit = Hit(
        SimpleNamespace(id="chunk", text=QUOTE, page=1, section=None),
        SimpleNamespace(id="doc", title="Study"),
        0.8,
        "Study p.1",
    )
    monkeypatch.setattr("litlens.generation.research.search", lambda *a, **kw: [hit])
    session = SimpleNamespace(execute=lambda *a, **kw: None)
    accounting = Accounting(time.monotonic() + 180)
    research = Research(session, "lib", "alice", "hybrid", accounting)
    return research


def run(graph, monkeypatch, outputs):
    outputs = iter(outputs)
    monkeypatch.setattr(
        RemoteProvider, "generate", lambda *a, **kw: Generation(json.dumps(next(outputs)))
    )
    state = graph.graph().invoke({"question": "How?", "round": 0, "gaps": []})
    return graph.result(state)


def test_success_and_role_bounds(graph, monkeypatch):
    result = run(graph, monkeypatch, [PLAN, DRAFT, verdict()])
    assert result["supported"] and result["claim_verification"][0]["claim_id"] == "c1"
    assert graph.accounting.logical_calls == 3
    assert result["evidence_rounds"] == 1
    assert result["first_round_evidence"] == ["chunk"]


@pytest.mark.parametrize("final_status,expected", [("supported", True), ("contradicted", False)])
def test_revision_reverifies_and_stops(graph, monkeypatch, final_status, expected):
    result = run(
        graph, monkeypatch, [PLAN, DRAFT, verdict("unsupported"), DRAFT, verdict(final_status)]
    )
    assert result["supported"] == expected
    assert result["evidence_rounds"] == 2
    assert graph.accounting.logical_calls == 5
    assert len(result["verification_history"]) == 2
    if not expected:
        assert result["answer"].startswith("I do not have enough") and not result["citations"]
        assert result["evidence_gaps"]


@pytest.mark.parametrize(
    "bad",
    [
        {"results": [], "revision_query": None},
        {
            "results": [
                {
                    "claim_id": "c2",
                    "status": "supported",
                    "evidence_refs": [1],
                    "explanation": "yes",
                }
            ]
        },
        {
            "results": [
                {
                    "claim_id": "c1",
                    "status": "supported",
                    "evidence_refs": [2],
                    "explanation": "yes",
                }
            ]
        },
        {
            "results": [
                {
                    "claim_id": "c1",
                    "status": "supported",
                    "evidence_refs": [True],
                    "explanation": "yes",
                }
            ]
        },
    ],
)
def test_malformed_verdict_fails_closed(graph, monkeypatch, bad):
    with pytest.raises(ValueError):
        run(graph, monkeypatch, [PLAN, DRAFT, bad])
    assert not graph.last_state.get("passed")


def test_invalid_provenance_never_reaches_verifier(graph, monkeypatch):
    invalid = {
        "claims": [
            {
                "text": "Fabricated",
                "evidence": [{"source": 1, "quote": "This quotation was fabricated by the model."}],
            }
        ]
    }
    result = run(graph, monkeypatch, [PLAN, invalid])
    assert not result["supported"] and graph.accounting.logical_calls == 2
    assert result["reason"] == "unverified_claim"


def test_deadline_stops_before_model(graph):
    graph.accounting.deadline = time.monotonic() - 1
    with pytest.raises(DeadlineExceeded):
        graph.graph().invoke({"question": "How?", "round": 0, "gaps": []})
    assert not graph.accounting.logical_calls


def test_rrf_dedup_and_limit():
    hits = [SimpleNamespace(chunk=SimpleNamespace(id=str(i))) for i in range(20)]
    fused = fuse([hits, list(reversed(hits))])
    assert len(fused) == 10 and len({h.chunk.id for h in fused}) == 10


def test_usage_and_unknown_cost(monkeypatch):
    monkeypatch.setattr(settings, "model_prices", {"model": ModelPrice(input=2, output=4)})
    acc = Accounting(time.monotonic() + 180)
    acc.record("model", "openai", 100, 50, 1, "success")
    assert acc.summary()["estimated_cost_usd"] == 0.0004
    acc.record("unpriced", "openai", 10, 0, 1, "success")
    assert acc.summary()["estimated_cost_usd"] is None
    acc.record("model", "openai", None, None, 2, "upstream_error")
    assert acc.summary()["usage"]["input_tokens"] is None
    assert acc.summary()["retry_count"] == 1
    monkeypatch.setattr(settings, "model_prices", {})
    local = Accounting(time.monotonic() + 180)
    local.record("model", "ollama", 100, 50, 1, "success")
    assert local.summary()["estimated_cost_usd"] is None
    monkeypatch.setattr(settings, "model_prices", {"model": ModelPrice(input=2, output=4)})
    assert local.summary()["estimated_cost_usd"] == 0.0004


@respx.mock
def test_roles_overrides_retries_and_missing_usage(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    monkeypatch.setattr("litlens.providers.remote.time.sleep", lambda _: None)
    route = respx.post("http://models.test/chat/completions").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}),
        ]
    )
    accounting = Accounting(time.monotonic() + 30, role="planner", prompt_version="planner-v1")
    token = active_request.set(accounting)
    try:
        result = RemoteProvider().generate("test", system="custom role", model="planner-model")
    finally:
        active_request.reset(token)
    assert result.input_tokens is None and result.output_tokens is None
    payload = json.loads(route.calls[-1].request.content)
    assert (
        payload["model"] == "planner-model" and payload["messages"][0]["content"] == "custom role"
    )
    assert accounting.summary()["retry_count"] == 1
    assert all(c["role"] == "planner" for c in accounting.calls)
    assert route.calls[-1].request.extensions["timeout"]["read"] <= 30


@respx.mock
def test_wall_clock_timeout_cancels_slow_response(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")

    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={})

    respx.post("http://models.test/chat/completions").mock(side_effect=slow)
    acc = Accounting(time.monotonic() + 0.05)
    token = active_request.set(acc)
    started = time.monotonic()
    try:
        with pytest.raises(DeadlineExceeded):
            RemoteProvider().generate("test")
    finally:
        active_request.reset(token)
    assert time.monotonic() - started < 0.18
    assert len(acc.calls) == 1
    assert acc.calls[0]["outcome"] == "deadline_exhausted"


@respx.mock
def test_embedding_accounting_and_role_restore(monkeypatch):
    from litlens.providers.remote import remote_embeddings

    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    respx.post("http://models.test/embeddings").respond(
        200, json={"data": [{"index": 0, "embedding": [1, 0]}], "usage": {"prompt_tokens": 7}}
    )
    acc = Accounting(time.monotonic() + 30, role="planner", prompt_version="planner-v1")
    token = active_request.set(acc)
    try:
        assert remote_embeddings(["test"]) == [[1, 0]]
    finally:
        active_request.reset(token)
    assert acc.calls[0]["role"] == "query_embedding"
    assert acc.calls[0]["input_tokens"] == 7
    assert acc.calls[0]["output_tokens"] == 0
    assert acc.role == "planner" and acc.prompt_version == "planner-v1"


def test_challenge_covers_required_categories():
    from litlens.evaluation.claim_support import load_challenge

    challenge = load_challenge()
    assert len(challenge["cases"]) == 15
    assert {c["category"] for c in challenge["cases"]} == {
        "paraphrase",
        "numerical",
        "qualification",
        "contradiction",
        "misleading-quotation",
    }


@respx.mock
def test_research_api_isolated_summary_and_upstream_failures(api, monkeypatch):
    from litlens.db.models import RequestSummary
    from litlens.evaluation.corpus import pdf_bytes
    from litlens.worker import run_once

    client, factory = api
    lib = client.post("/api/v1/libraries", json={"name": "Evidence"}).json()["id"]
    other = client.post("/api/v1/libraries", json={"name": "Other"}).json()["id"]
    for library_id, text in [
        (lib, QUOTE),
        (other, "Secret other-library hybrid evidence must not leak."),
    ]:
        uploaded = client.post(
            "/api/v1/documents/upload",
            data={"library_id": library_id},
            files={
                "file": ("Study.pdf", pdf_bytes({"section": "Methods", "facts": [{"text": text}]}))
            },
        )
        assert uploaded.status_code == 202
    while run_once():
        pass
    requires_model = client.post(
        "/api/v1/answers", json={"library_id": lib, "query": "How?", "workflow": "research"}
    )
    assert requires_model.status_code == 409
    assert "Research requires" in requires_model.json()["detail"]
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    responses = [
        {
            "choices": [{"message": {"content": json.dumps(output)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        for output in [PLAN, DRAFT, verdict()]
    ]
    route = respx.post("http://models.test/chat/completions").mock(
        side_effect=[httpx.Response(200, json=r) for r in responses]
    )
    payload = {"library_id": lib, "query": "Explain hybrid retrieval", "workflow": "research"}
    result = client.post("/api/v1/answers", json=payload)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["supported"] and body["usage"]["input_tokens"] == 30
    assert body["request_id"] == result.headers["X-Request-ID"]
    assert "Secret other-library" not in route.calls[1].request.content.decode()
    assert "passage" in json.loads(route.calls[2].request.content)["messages"][1]["content"]
    detail_url = f"/api/v1/requests/{body['request_id']}"
    details = client.get(detail_url, params={"library_id": lib}).json()["summary"]
    assert details["logical_model_calls"] == 3
    assert QUOTE not in json.dumps(details) and "Explain hybrid" not in json.dumps(details)
    assert client.get(detail_url, params={"library_id": other}).status_code == 404
    assert (
        client.get(
            detail_url, params={"library_id": lib}, headers={"Authorization": "Bearer bob-token"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/answers", json=payload, headers={"Authorization": "Bearer bob-token"}
        ).status_code
        == 404
    )
    route.mock(return_value=httpx.Response(401))
    failure = client.post("/api/v1/answers", json=payload)
    assert failure.status_code == 502
    assert failure.json()["reason"] == "upstream_error"
    with factory() as session:
        assert (
            session.get(RequestSummary, failure.json()["request_id"]).summary["reason"]
            == "upstream_error"
        )
    route.mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "bad"}}]})
    )
    malformed = client.post("/api/v1/answers", json=payload)
    assert malformed.json()["reason"] == "invalid_provider_output"
    assert malformed.json()["usage"]["input_tokens"] is None


@respx.mock
def test_challenge_evaluation_uses_fixture_labels(api, monkeypatch):
    from litlens.evaluation.claim_support import load_challenge

    client, _ = api
    lib = client.post("/api/v1/libraries", json={"name": "Challenge"}).json()["id"]
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    respx.post("http://models.test/chat/completions").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(verdict(case["label"]))}}]},
            )
            for case in load_challenge()["cases"]
        ]
    )
    result = client.post("/api/v1/evaluations/claim-support", json={"library_id": lib})
    assert result.status_code == 201, result.text
    metrics = result.json()["metrics"]
    assert metrics["accuracy"] == 1 and metrics["questions"] == 15
    assert all(c["request_id"] for c in metrics["cases"])


@respx.mock
@pytest.mark.parametrize(
    "envelope",
    [{"choices": []}, {"choices": [{}]}, [], {"choices": [{"message": {"content": None}}]}],
)
def test_invalid_provider_envelope_is_a_validation_error(monkeypatch, envelope):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")
    respx.post("http://models.test/chat/completions").respond(200, json=envelope)
    with pytest.raises(ValueError, match="envelope"):
        RemoteProvider().generate("test")


def test_revision_reverifies_every_claim(graph, monkeypatch):
    draft = {"claims": DRAFT["claims"] * 2}
    failed = {
        "results": [
            verdict()["results"][0],
            {**verdict("unsupported")["results"][0], "claim_id": "c2"},
        ],
        "revision_query": "candidate types",
    }
    final = {"results": [verdict()["results"][0], {**verdict()["results"][0], "claim_id": "c2"}]}
    outputs = iter([PLAN, draft, failed, draft, final])
    payloads = []

    def generate(self, prompt, **kwargs):
        if "Verify each COMPLETE" in kwargs["system"]:
            payloads.append(json.loads(prompt))
        return Generation(json.dumps(next(outputs)))

    monkeypatch.setattr(RemoteProvider, "generate", generate)
    state = graph.graph().invoke({"question": "How?", "round": 0, "gaps": []})
    assert graph.result(state)["supported"]
    assert len(payloads) == 2
    assert all([c["claim_id"] for c in p["claims"]] == ["c1", "c2"] for p in payloads)


def test_explicit_call_bound_and_planner_limit(graph, monkeypatch):
    from litlens.generation.research import Plan

    graph.accounting.logical_calls = 6
    with pytest.raises(ValueError, match="bound"):
        graph.generate("planner", {})
    with pytest.raises(ValueError):
        Plan.model_validate({**PLAN, "queries": ["one"] * 4})


def test_deadline_response_is_persisted(api, monkeypatch):
    client, _ = api
    lib = client.post("/api/v1/libraries", json={"name": "Deadline"}).json()["id"]
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "research_deadline_seconds", 0.000001)
    response = client.post(
        "/api/v1/answers", json={"library_id": lib, "query": "How?", "workflow": "research"}
    )
    assert response.status_code == 504
    body = response.json()
    assert body["reason"] == "deadline_exhausted" and not body["supported"]
    detail = client.get(f"/api/v1/requests/{body['request_id']}", params={"library_id": lib})
    assert detail.status_code == 200
    assert detail.json()["summary"]["reason"] == "deadline_exhausted"


@respx.mock
def test_research_evaluation_separates_rankings_usage_and_workflows(api, monkeypatch):
    from litlens.evaluation.corpus import pdf_bytes
    from litlens.worker import run_once

    client, _ = api
    lib = client.post("/api/v1/libraries", json={"name": "Research evaluation"}).json()["id"]
    corpus = {
        "version": "test-fixture",
        "documents": [
            {
                "title": "Study",
                "section": "Methods",
                "facts": [{"text": QUOTE, "question": "hybrid retrieval"}],
            }
        ],
        "unsupported_questions": ["unknown planets"],
    }
    monkeypatch.setattr("litlens.evaluation.runner.load_corpus", lambda: corpus)
    client.post(
        "/api/v1/documents/upload",
        data={"library_id": lib},
        files={"file": ("Study.pdf", pdf_bytes(corpus["documents"][0]))},
    )
    assert run_once()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", "http://models.test")

    def model(request):
        payload = json.loads(request.content)
        system = payload["messages"][0]["content"]
        prompt = json.loads(payload["messages"][1]["content"])
        if "Plan evidence" in system:
            output = {**PLAN, "queries": [prompt["question"]]}
        elif "Verify each COMPLETE" in system:
            output = verdict()
        else:
            output = {"claims": []} if "unknown" in prompt["question"] else DRAFT
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    respx.post("http://models.test/chat/completions").mock(side_effect=model)
    response = client.post("/api/v1/evaluations", json={"library_id": lib, "workflow": "research"})
    assert response.status_code == 201, response.text
    metrics = response.json()["metrics"]
    assert metrics["workflow"] == "research" and metrics["workflow_version"] == "research-v1"
    for mode in metrics["modes"].values():
        assert mode["recall_at_5"] == 1 and mode["mrr_at_5"] == 1
        assert mode["first_round_recall_at_5"] == 1
        assert mode["false_refusal_rate"] == mode["unsupported_answer_rate"] == 0
        assert mode["input_tokens"] is None and mode["estimated_cost_usd"] is None
        assert mode["cases"][0]["stage_timings_ms"]["verify"] >= 0
        assert mode["cases"][0]["latency_ms"] >= mode["cases"][0]["workflow_latency_ms"]
    assert len({c["request_id"] for m in metrics["modes"].values() for c in m["cases"]}) == 6
