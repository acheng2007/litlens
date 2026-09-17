"""Fetch checksum-pinned papers and ingest them through the public upload API."""

import hashlib
import time
from pathlib import Path

import httpx
import pymupdf

from .datasets import load_real_corpus

MAX_PDF_BYTES = 30 * 1024 * 1024


def validate_pdf(path, paper):
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(f"Oversized PDF: {paper.id}")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != paper.sha256:
            raise ValueError(f"PDF checksum mismatch: {paper.id}; do not update the pin blindly")
    with pymupdf.open(path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or len(pdf) != paper.pages:
            raise ValueError(f"PDF page count or format mismatch: {paper.id}")


def fetch_papers(cache_dir: Path):
    corpus = load_real_corpus()
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with httpx.Client(
        timeout=90, headers={"User-Agent": "LitLens/0.1 research evaluation"}
    ) as http:
        for paper in corpus.documents:
            target = cache_dir / f"{paper.arxiv_id}.pdf"
            if not target.exists():
                # Keep the cache atomic; failed downloads never become valid cached papers.
                import tempfile

                with tempfile.NamedTemporaryFile(
                    dir=cache_dir, suffix=".part", delete=False
                ) as tmp:
                    temporary = Path(tmp.name)
                    try:
                        with http.stream("GET", paper.pdf_url) as response:
                            response.raise_for_status()
                            size = 0
                            for part in response.iter_bytes():
                                size += len(part)
                                if size > MAX_PDF_BYTES:
                                    raise ValueError("Evaluation PDF exceeds size limit")
                                tmp.write(part)
                        tmp.flush()
                        validate_pdf(temporary, paper)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                        time.sleep(3.1)
            validate_pdf(target, paper)
            paths.append((paper, target))
    return corpus, paths


def seed_real_papers(cache_dir, request, wait_job):
    corpus, papers = fetch_papers(cache_dir)
    library = request("POST", "/api/v1/libraries", json={"name": f"Real papers v{corpus.version}"})
    jobs = []
    for paper, path in papers:
        with path.open("rb") as stream:
            jobs.append(
                request(
                    "POST",
                    "/api/v1/documents/upload",
                    data={"library_id": library["id"]},
                    files={"file": (paper.title + ".pdf", stream, "application/pdf")},
                )
            )
    for job in jobs:
        wait_job(job["job_id"])
    return {
        "library_id": library["id"],
        "dataset": corpus.id,
        "version": corpus.version,
        "documents": len(papers),
        "jobs": jobs,
    }
