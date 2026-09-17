"""Opt-in test using actual pinned PDFs, the live HTTP API, and background workers."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from litlens.cli import app, request
from litlens.evaluation.datasets import fingerprint, load_real_corpus


@pytest.mark.skipif(
    os.getenv("LITLENS_REAL_PAPERS") != "1",
    reason="Set LITLENS_REAL_PAPERS=1 with a running stack and PDF cache/network",
)
def test_real_paper_seed_upload_worker_evaluation_and_report(tmp_path):
    cache = os.getenv("LITLENS_PAPER_CACHE", "data/evaluation-papers")
    runner = CliRunner()
    seeded = runner.invoke(app, ["seed-papers", "--cache-dir", cache])
    assert seeded.exit_code == 0, seeded.output
    seed = json.loads(seeded.output)
    assert seed["documents"] == 6
    for job in seed["jobs"]:
        status = request("GET", f"/api/v1/jobs/{job['job_id']}")
        assert status["status"] == "completed", status
        doc = request("GET", f"/api/v1/documents/{job['document_id']}")
        assert doc["chunks"] > 0 and doc["status"] == "ready"
    output = Path(os.getenv("LITLENS_REAL_REPORT", str(tmp_path / "real-report.json")))
    evaluated = runner.invoke(
        app,
        [
            "evaluate",
            "--dataset",
            "real-papers",
            "--library-id",
            seed["library_id"],
            "--output",
            str(output),
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    report = json.loads(output.read_text())
    metrics = report["metrics"]
    assert metrics["dataset"] == "real-papers"
    assert metrics["corpus_sha256"] == fingerprint(load_real_corpus().model_dump())
    assert metrics["questions"] == 36
    assert len(metrics["gold_resolution"]) == 26
    assert all(metrics["gold_resolution"].values())
    assert metrics["ingestion"]["completed_documents"] == 6
    for mode in ["vector", "keyword", "hybrid"]:
        result = metrics["modes"][mode]
        assert len(result["cases"]) == 36
        assert set(result["by_category"]) == {
            "paraphrase",
            "qualification",
            "conflicting_findings",
            "ambiguity",
            "unsupported",
        }
        assert 0 <= result["recall_at_5"] <= 1
        assert 0 <= result["unsupported_answer_rate"] <= 1
        assert result["citation_correctness"] in (None, 1.0)
        assert result["quote_faithfulness"] in (None, 1.0)
    stored = request("GET", "/api/v1/evaluations", params={"library_id": seed["library_id"]})
    assert next(r for r in stored if r["id"] == report["id"])["metrics"] == metrics
