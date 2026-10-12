import json
import os
import time
from pathlib import Path

import httpx
import typer

from litlens.evaluation.datasets import DatasetName

app = typer.Typer(help="LitLens research assistant. All commands use the authenticated HTTP API.")
ingest = typer.Typer()
app.add_typer(ingest, name="ingest")


def client():
    return httpx.Client(
        base_url=os.getenv("LITLENS_API_URL", "http://localhost:8000"),
        headers={"Authorization": "Bearer " + os.getenv("LITLENS_API_TOKEN", "local-dev-token")},
        timeout=float(os.getenv("LITLENS_API_TIMEOUT_SECONDS", "600")),
    )


def request(method, path, **kwargs):
    try:
        with client() as http:
            response = http.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        typer.echo(f"API error {exc.response.status_code}: {exc.response.text}", err=True)
        raise typer.Exit(1) from None
    except httpx.HTTPError:
        typer.echo("Cannot reach LitLens API; check LITLENS_API_URL and service status.", err=True)
        raise typer.Exit(1) from None


def emit(data):
    typer.echo(json.dumps(data, indent=2))


def wait_job(job_id, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request("GET", f"/api/v1/jobs/{job_id}")
        if job["status"] == "completed":
            return job
        if job["status"] == "failed":
            typer.echo(f"Job failed after {job['attempts']} attempts: {job['error']}", err=True)
            raise typer.Exit(1)
        time.sleep(1)
    typer.echo(f"Timed out; inspect job {job_id} with litlens status --job-id.", err=True)
    raise typer.Exit(1)


@app.command()
def library(name: str):
    """Create a library belonging to the API token's user."""
    emit(request("POST", "/api/v1/libraries", json={"name": name}))


@app.command()
def status(job_id: str | None = None):
    emit(request("GET", f"/api/v1/jobs/{job_id}" if job_id else "/ready"))


@app.command("search")
def search_cmd(
    question: str,
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"),
    mode: str = "hybrid",
):
    emit(
        request(
            "POST",
            "/api/v1/search",
            json={"library_id": library_id, "query": question, "mode": mode},
        )
    )


@app.command()
def retry(job_id: str, wait: bool = False):
    """Retry a failed job after correcting its cause, preserving its failure history."""
    job = request("POST", f"/api/v1/jobs/{job_id}/retry")
    emit(wait_job(job["job_id"]) if wait else job)


@app.command()
def ask(
    question: str,
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"),
    workflow: str = "direct",
):
    if workflow not in {"direct", "research"}:
        raise typer.BadParameter("Choose direct or research", param_hint="--workflow")
    emit(
        request(
            "POST",
            "/api/v1/answers",
            json={"library_id": library_id, "query": question, "workflow": workflow},
        )
    )


@app.command()
def evaluate(
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"),
    workflow: str = "direct",
    dataset: str = typer.Option("synthetic", help="synthetic or real-papers"),
    output: Path | None = typer.Option(None, help="Save the complete JSON report to a new file."),
):
    """Benchmark a seeded library, keeping real-paper and synthetic reports separate."""
    from typing import get_args

    if dataset not in get_args(DatasetName):
        raise typer.BadParameter("Choose synthetic or real-papers", param_hint="--dataset")
    if workflow not in {"direct", "research"}:
        raise typer.BadParameter("Choose direct or research", param_hint="--workflow")
    if output and output.exists():
        raise typer.BadParameter("Output already exists; choose a new path", param_hint="--output")
    report = request(
        "POST",
        "/api/v1/evaluations",
        json={"library_id": library_id, "dataset": dataset, "workflow": workflow},
    )
    if output:
        with output.open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    emit(report)


@app.command("seed-papers")
def seed_papers(cache_dir: Path = typer.Option(Path("data/evaluation-papers"))):
    """Download pinned real papers, verify checksums, and ingest a separate library."""
    from litlens.evaluation.seed import seed_real_papers

    try:
        emit(seed_real_papers(cache_dir, request, wait_job))
    except (ValueError, httpx.HTTPError, OSError) as exc:
        typer.echo(f"Cannot seed real papers: {exc}", err=True)
        raise typer.Exit(1) from None


@ingest.command("pdf")
def ingest_pdf(
    path: Path = typer.Argument(..., exists=True, dir_okay=False),
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"),
    wait: bool = False,
):
    with path.open("rb") as file:
        job = request(
            "POST",
            "/api/v1/documents/upload",
            data={"library_id": library_id},
            files={"file": (path.name, file, "application/pdf")},
        )
    emit(wait_job(job["job_id"]) if wait else job)


@ingest.command("arxiv")
def ingest_arxiv(
    query: str,
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"),
    max_results: int = typer.Option(5, min=1, max=20),
    wait: bool = False,
):
    job = request(
        "POST",
        "/api/v1/documents/arxiv",
        json={"library_id": library_id, "query": query, "max_results": max_results},
    )
    if wait:
        job = wait_job(job["job_id"])
        for document_id in job["result"]["document_ids"]:
            document = request("GET", f"/api/v1/documents/{document_id}")
            wait_job(document["latest_job_id"])
    emit(job)


@app.command()
def demo():
    """Seed ten original research-method notes as PDFs and print the library ID."""
    from litlens.evaluation.corpus import load_corpus, pdf_bytes

    library = request("POST", "/api/v1/libraries", json={"name": "LitLens evaluation corpus v1"})
    jobs = []
    for document in load_corpus()["documents"]:
        jobs.append(
            request(
                "POST",
                "/api/v1/documents/upload",
                data={"library_id": library["id"]},
                files={
                    "file": (document["title"] + ".pdf", pdf_bytes(document), "application/pdf")
                },
            )
        )
    for job in jobs:
        wait_job(job["job_id"])
    emit(library)


@app.command("evaluate-verifier")
def evaluate_verifier_cmd(
    library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID"), output: Path | None = None
):
    """Measure the semantic verifier against the versioned claim-support challenge."""
    if output and output.exists():
        raise typer.BadParameter("Output already exists; choose a new path", param_hint="--output")
    report = request("POST", "/api/v1/evaluations/claim-support", json={"library_id": library_id})
    if output:
        with output.open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    emit(report)


if __name__ == "__main__":
    app()
