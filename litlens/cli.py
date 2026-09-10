import json
import os
import time
from pathlib import Path

import httpx
import typer

app = typer.Typer(help="LitLens research assistant. All commands use the authenticated HTTP API.")
ingest = typer.Typer()
app.add_typer(ingest, name="ingest")


def client():
    return httpx.Client(
        base_url=os.getenv("LITLENS_API_URL", "http://localhost:8000"),
        headers={"Authorization": "Bearer " + os.getenv("LITLENS_API_TOKEN", "local-dev-token")},
        timeout=600,
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
def ask(question: str, library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID")):
    emit(request("POST", "/api/v1/answers", json={"library_id": library_id, "query": question}))


@app.command()
def evaluate(library_id: str = typer.Option(..., envvar="LITLENS_LIBRARY_ID")):
    """Benchmark the bundled 60-question corpus; run demo first."""
    emit(request("POST", "/api/v1/evaluations", json={"library_id": library_id}))


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
    emit(wait_job(job["job_id"]) if wait else job)


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


if __name__ == "__main__":
    app()
