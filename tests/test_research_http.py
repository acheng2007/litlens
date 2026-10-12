"""True HTTP API/CLI acceptance with live DB and a controlled HTTP model transport.

This tests orchestration and transport, not model quality.
"""

import json
import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from litlens.cli import app as cli
from litlens.config import settings
from litlens.evaluation.corpus import pdf_bytes
from litlens.main import app
from litlens.worker import run_once

QUOTE = "Hybrid retrieval combines keyword and vector candidates."


class ControlledModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = payload["messages"][0]["content"]
        prompt = json.loads(payload["messages"][1]["content"])
        if "Plan evidence" in system:
            output = {
                "queries": ["hybrid retrieval"],
                "document_needs": [],
                "answer_requirements": [],
            }
        elif "Verify each COMPLETE" in system:
            claim = prompt["claims"][0]
            passed = "wrong" not in claim["text"]
            output = {
                "results": [
                    {
                        "claim_id": "c1",
                        "status": "supported" if passed else "unsupported",
                        "evidence_refs": [1],
                        "explanation": "Candidate types must match the passage.",
                    }
                ],
                "revision_query": "hybrid candidate types",
            }
        else:
            question = prompt["question"]
            wrong = "refusal" in question or (
                "corrected" in question and not prompt["revision_feedback"]
            )
            quote_source = next(s for s in prompt["sources"] if QUOTE in s["text"])
            output = {
                "claims": [
                    {
                        "text": "The wrong candidate types are used."
                        if wrong
                        else "Keyword and vector candidates are combined.",
                        "evidence": [{"source": quote_source["source"], "quote": QUOTE}],
                    }
                ]
            }
        body = json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def live_api():
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.mark.skipif(
    os.getenv("LITLENS_RESEARCH_HTTP") != "1", reason="Opt in to local HTTP acceptance"
)
def test_live_research_api_and_cli(api, monkeypatch):
    # The api fixture supplies isolated, migrated PostgreSQL and authenticated dependencies.
    model = ThreadingHTTPServer(("127.0.0.1", 0), ControlledModel)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "model_base_url", f"http://127.0.0.1:{model.server_port}")
    try:
        with (
            live_api() as url,
            httpx.Client(base_url=url, headers={"Authorization": "Bearer alice-token"}) as http,
        ):
            library_id = http.post("/api/v1/libraries", json={"name": "HTTP acceptance"}).json()[
                "id"
            ]
            uploaded = http.post(
                "/api/v1/documents/upload",
                data={"library_id": library_id},
                files={
                    "file": (
                        "Study.pdf",
                        pdf_bytes({"section": "Methods", "facts": [{"text": QUOTE}]}),
                    )
                },
            )
            assert uploaded.status_code == 202
            assert run_once()
            monkeypatch.setenv("LITLENS_API_URL", url)
            monkeypatch.setenv("LITLENS_API_TOKEN", "alice-token")
            for question, expected, rounds in [
                ("successful hybrid retrieval", True, 1),
                ("corrected hybrid retrieval", True, 2),
                ("final refusal hybrid retrieval", False, 2),
            ]:
                result = CliRunner().invoke(
                    cli, ["ask", question, "--library-id", library_id, "--workflow", "research"]
                )
                assert result.exit_code == 0, result.output
                response = json.loads(result.output)
                assert response["supported"] == expected
                assert response["evidence_rounds"] == rounds
                assert response["logical_model_calls"] == (3 if rounds == 1 else 5)
                details = http.get(
                    f"/api/v1/requests/{response['request_id']}", params={"library_id": library_id}
                )
                assert details.status_code == 200
                assert details.json()["summary"]["supported"] == expected
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=10)
