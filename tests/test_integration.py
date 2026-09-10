from datetime import timedelta

import httpx
from sqlalchemy import select, text

from litlens.config import settings
from litlens.db.models import Chunk, Job, now
from litlens.evaluation.corpus import load_corpus, pdf_bytes
from litlens.ingestion.arxiv import Paper
from litlens.worker import claim_job, process, run_once


def library(client):
    response = client.post("/api/v1/libraries", json={"name": "Test"})
    assert response.status_code == 201
    return response.json()["id"]


def upload(client, library_id, content=None):
    response = client.post(
        "/api/v1/documents/upload",
        data={"library_id": library_id},
        files={
            "file": (
                "Study.pdf",
                content or pdf_bytes(load_corpus()["documents"][3]),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_upload_worker_search_answer_and_permissions(api):
    client, factory = api
    lib = library(client)
    job = upload(client, lib)
    assert job["status"] == "queued"
    assert run_once()
    done = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert done["status"] == "completed" and done["attempts"] == 1
    details = client.get(f"/api/v1/documents/{job['document_id']}").json()
    assert details["chunks"] == 5 and details["status"] == "ready"
    body = {"library_id": lib, "query": "What candidates does hybrid retrieval combine?"}
    for mode in ["keyword", "vector", "hybrid"]:
        result = client.post("/api/v1/search", json={**body, "mode": mode})
        assert result.status_code == 200, result.text
        assert result.json()["results"][0]["document_id"] == job["document_id"]
    response = client.post("/api/v1/answers", json=body)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["supported"] and answer["citations"]
    with factory() as session:
        for citation in answer["citations"]:
            chunk = session.get(Chunk, citation["chunk_id"])
            assert citation["quote"] in chunk.text and chunk.page == citation["page"]
    empty = library(client)
    assert client.post("/api/v1/search", json={**body, "library_id": empty}).json()["results"] == []
    assert not client.post(
        "/api/v1/answers", json={**body, "query": "What is the mass of Jupiter?"}
    ).json()["supported"]
    bob = {"Authorization": "Bearer bob-token"}
    for path in [
        f"/api/v1/documents/{job['document_id']}",
        f"/api/v1/jobs/{job['job_id']}",
        f"/api/v1/documents?library_id={lib}",
        f"/api/v1/evaluations?library_id={lib}",
    ]:
        assert client.get(path, headers=bob).status_code == 404
    for path in ["/api/v1/search", "/api/v1/answers"]:
        assert client.post(path, json=body, headers=bob).status_code == 404
    assert (
        client.post(
            "/api/v1/documents/arxiv", json={"library_id": lib, "query": "RAG"}, headers=bob
        ).status_code
        == 404
    )
    assert (
        client.post("/api/v1/evaluations", json={"library_id": lib}, headers=bob).status_code == 404
    )
    assert (
        client.post("/api/v1/libraries", json={"name": "x", "owner_id": "bob"}).status_code == 422
    )
    assert (
        client.get(
            "/api/v1/documents",
            params={"library_id": lib},
            headers={"Authorization": "Bearer invalid", "X-User-ID": "alice"},
        ).status_code
        == 401
    )
    assert client.post("/api/v1/answers", json={**body, "query": "  "}).status_code == 422


def test_deduplication_and_failed_pdf(api):
    client, factory = api
    lib = library(client)
    content = pdf_bytes(load_corpus()["documents"][3])
    first = upload(client, lib, content)
    second = upload(client, lib, content)
    assert first == second
    assert run_once()
    invalid = upload(client, lib, b"%PDF-1.7\ninvalid")
    assert run_once()
    job = client.get(f"/api/v1/jobs/{invalid['job_id']}").json()
    assert job["status"] == "failed" and job["attempts"] == 1
    assert "PDF" in job["error"]
    with factory() as session:
        assert not session.scalars(
            select(Chunk).where(Chunk.document_id == invalid["document_id"])
        ).all()


def test_retry_exhaustion_and_stale_lease(api, monkeypatch):
    client, factory = api
    lib = library(client)
    queued = upload(client, lib)
    monkeypatch.setattr(
        "litlens.worker.embed_many",
        lambda _: (_ for _ in ()).throw(httpx.ConnectError("private URL secret")),
    )
    for attempt in range(1, 4):
        assert run_once()
        status = client.get(f"/api/v1/jobs/{queued['job_id']}").json()
        assert status["attempts"] == attempt
        assert status["status"] == ("failed" if attempt == 3 else "retrying")
        assert "secret" not in status["error"]
    other = upload(client, library(client))
    old = claim_job()
    with factory.begin() as session:
        session.get(Job, other["job_id"]).available_at = now() - timedelta(seconds=1)
    new = claim_job()
    assert new.attempts == 2 and new.lease_token != old.lease_token
    process(old)
    with factory() as session:
        current = session.get(Job, new.id)
        assert current.lease_token == new.lease_token and current.status == "processing"
    process(new)


def test_arxiv_parent_and_pdf_children(api, monkeypatch):
    client, factory = api
    lib = library(client)
    monkeypatch.setattr(
        "litlens.worker.search_arxiv", lambda *_: [Paper("1706.03762v7", "Attention")]
    )
    content = pdf_bytes(load_corpus()["documents"][0])

    def download(identifier, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    monkeypatch.setattr("litlens.worker.download_pdf", download)
    for _ in range(2):
        result = client.post(
            "/api/v1/documents/arxiv", json={"library_id": lib, "query": "attention"}
        )
        assert result.status_code == 202
        while run_once():
            pass
    documents = client.get("/api/v1/documents", params={"library_id": lib}).json()
    assert len(documents) == 1 and documents[0]["status"] == "ready"
    assert documents[0]["arxiv_id"] == "1706.03762v7"


def test_readiness_and_embedding_mismatch(api, monkeypatch):
    client, factory = api
    lib = library(client)
    monkeypatch.setattr(settings, "embedding_model", "different")
    # Hash space is deliberately independent of a remote model name.
    assert (
        client.post("/api/v1/search", json={"library_id": lib, "query": "test"}).status_code == 200
    )
    monkeypatch.setattr(settings, "embedding_provider", "ollama")
    assert (
        client.post("/api/v1/search", json={"library_id": lib, "query": "test"}).status_code == 409
    )
    monkeypatch.setattr(
        "litlens.main.redis_client", lambda: (_ for _ in ()).throw(ConnectionError())
    )
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503


def test_indexes_and_full_evaluation(api):
    client, factory = api
    lib = library(client)
    for doc in load_corpus()["documents"]:
        result = client.post(
            "/api/v1/documents/upload",
            data={"library_id": lib},
            files={"file": (doc["title"] + ".pdf", pdf_bytes(doc), "application/pdf")},
        )
        assert result.status_code == 202
    while run_once():
        pass
    response = client.post("/api/v1/evaluations", json={"library_id": lib})
    assert response.status_code == 201, response.text
    metrics = response.json()["metrics"]
    assert metrics["questions"] == 60
    for mode, result in metrics["modes"].items():
        assert result["recall_at_5"] >= 0.8, (mode, result["recall_at_5"])
        assert result["citation_correctness"] == 1
        assert result["quote_faithfulness"] == 1
        assert result["unsupported_answer_rate"] == 0
    assert len(client.get("/api/v1/evaluations", params={"library_id": lib}).json()) == 1
    with factory() as session:
        indexes = (
            session.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='chunks'"))
            .scalars()
            .all()
        )
        assert {"ix_chunks_vector", "ix_chunks_fts"} <= set(indexes)


def test_retry_recovers_and_redis_outage_does_not_lose_job(api, monkeypatch):
    from redis import RedisError

    from litlens.retrieval.embeddings import embed_many

    client, factory = api
    monkeypatch.setattr("litlens.worker.redis_client", lambda: (_ for _ in ()).throw(RedisError()))
    job = upload(client, library(client))
    calls = 0

    def transient(texts):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("transient")
        return embed_many(texts)

    monkeypatch.setattr("litlens.worker.embed_many", transient)
    assert run_once() and run_once()
    result = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert result["status"] == "completed" and result["attempts"] == 2


def test_upload_limits_and_authentication(api, monkeypatch):
    client, factory = api
    lib = library(client)
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    response = client.post(
        "/api/v1/documents/upload",
        data={"library_id": lib},
        files={"file": ("large.pdf", b"%PDF-" + b"x" * 200)},
    )
    assert response.status_code == 413
    response = client.post(
        "/api/v1/documents/upload",
        data={"library_id": lib},
        files={"file": ("fake.pdf", b"not pdf")},
    )
    assert response.status_code == 400


def test_chunked_request_is_bounded_before_parsing(api):
    client, _ = api
    response = client.post(
        "/api/v1/libraries",
        content=iter([b'{"name":"', b"x" * 70000, b'"}']),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_explicit_retry_preserves_history_and_permissions(api, monkeypatch):
    from litlens.retrieval.embeddings import embed_many

    client, factory = api
    original = upload(client, library(client))
    monkeypatch.setattr(
        "litlens.worker.embed_many", lambda _: (_ for _ in ()).throw(ValueError("config"))
    )
    assert run_once()
    path = f"/api/v1/jobs/{original['job_id']}/retry"
    assert client.post(path, headers={"Authorization": "Bearer bob-token"}).status_code == 404
    retry = client.post(path)
    assert retry.status_code == 202
    assert retry.json()["job_id"] != original["job_id"]
    assert client.post(path).json()["job_id"] == retry.json()["job_id"]
    monkeypatch.setattr("litlens.worker.embed_many", embed_many)
    assert run_once()
    assert client.get(f"/api/v1/jobs/{original['job_id']}").json()["status"] == "failed"
    assert client.get(f"/api/v1/jobs/{retry.json()['job_id']}").json()["status"] == "completed"
    assert client.post(path).status_code == 409
