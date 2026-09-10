"""True HTTP-to-worker acceptance test, opt in against a running Compose stack."""

import os
import time
from uuid import uuid4

import httpx
import pytest

from litlens.evaluation.corpus import load_corpus, pdf_bytes


@pytest.mark.skipif(
    os.getenv("LITLENS_LIVE") != "1", reason="Set LITLENS_LIVE=1 for the running stack"
)
def test_live_pdf_to_cited_answer():
    with httpx.Client(
        base_url=os.getenv("LITLENS_API_URL", "http://localhost:8000"),
        headers={"Authorization": "Bearer " + os.getenv("LITLENS_API_TOKEN", "local-dev-token")},
        timeout=30,
    ) as client:
        assert client.get("/ready").status_code == 200
        response = client.post("/api/v1/libraries", json={"name": "Acceptance " + uuid4().hex[:8]})
        assert response.status_code == 201
        library = response.json()["id"]
        response = client.post(
            "/api/v1/documents/upload",
            data={"library_id": library},
            files={
                "file": ("Study.pdf", pdf_bytes(load_corpus()["documents"][3]), "application/pdf")
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 90
        while True:
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            assert job["status"] != "failed", job
            if job["status"] == "completed":
                break
            assert time.monotonic() < deadline, job
            time.sleep(0.25)
        request = {"library_id": library, "query": "What candidates does hybrid retrieval combine?"}
        hits = client.post("/api/v1/search", json=request).json()["results"]
        answer = client.post("/api/v1/answers", json=request).json()
        assert answer["supported"]
        for citation in answer["citations"]:
            hit = next(h for h in hits if h["chunk_id"] == citation["chunk_id"])
            assert citation["quote"] in hit["text"] and citation["page"] == hit["page"]
        refusal = client.post(
            "/api/v1/answers", json={**request, "query": "How far is Neptune from Earth?"}
        ).json()
        assert not refusal["supported"] and not refusal["citations"]
        assert client.get("/openapi.json").json()["info"]["version"] == "1.0.0"
