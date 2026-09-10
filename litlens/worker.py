"""PostgreSQL outbox with Redis wakeups, leases, fencing, and bounded retries."""

import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
from redis import Redis, RedisError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import OperationalError

from litlens.config import settings
from litlens.db.models import Chunk, Document, Job, Library, SessionLocal, now, uid
from litlens.ingestion.arxiv import download_pdf, search_arxiv
from litlens.ingestion.pdf import InvalidPDF, extract_pdf
from litlens.observability import event, span, trace_id
from litlens.retrieval.embeddings import embed_many


def redis_client():
    return Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=5)


def wake():
    try:
        client = redis_client()
        with client.pipeline() as pipe:
            pipe.lpush("litlens:wakeup", "1").ltrim("litlens:wakeup", 0, 999).execute()
    except RedisError:
        # The committed SQL job is the durable queue; workers also poll it.
        event("queue.wakeup_failed")


def claim_job():
    with SessionLocal.begin() as session:
        job = session.scalar(
            select(Job)
            .where(Job.status.in_(["queued", "retrying", "processing"]), Job.available_at <= now())
            .order_by(Job.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        if job.attempts >= settings.job_max_attempts:
            job.status, job.error, job.completed_at = (
                "failed",
                "Worker lease expired too many times",
                now(),
            )
            if job.document_id:
                session.get(Document, job.document_id).status = "failed"
            return None
        job.attempts += 1
        job.status, job.lease_token = "processing", uid()
        job.available_at = now() + timedelta(seconds=settings.job_lease_seconds)
        if job.document_id:
            session.get(Document, job.document_id).status = "processing"
        session.flush()
        return job


def renew(job):
    with SessionLocal.begin() as session:
        session.execute(
            update(Job)
            .where(Job.id == job.id, Job.lease_token == job.lease_token, Job.status == "processing")
            .values(available_at=now() + timedelta(seconds=settings.job_lease_seconds))
        )


def heartbeat(stop, worker_id, job=None):
    while not stop.is_set():
        try:
            if job:
                renew(job)
            redis_client().set(f"litlens:worker:{worker_id}", "alive", ex=60)
        except Exception as exc:
            event("worker.heartbeat_error", error_type=type(exc).__name__)
        stop.wait(15)


def process(job):
    started = time.perf_counter()
    token = trace_id.set(job.trace_id)
    stop = threading.Event()
    keeper = threading.Thread(target=heartbeat, args=(stop, job.id, job), daemon=True)
    keeper.start()
    try:
        with span("ingestion", job_id=job.id, attempt=job.attempts, kind=job.kind):
            if job.kind == "arxiv_sync":
                papers = search_arxiv(job.payload["query"], job.payload.get("max_results", 5))
                chunks, document = None, None
            else:
                with SessionLocal() as session:
                    document = session.get(Document, job.document_id)
                    library = session.get(Library, job.library_id)
                    if library.embedding_space != settings.embedding_space:
                        raise ValueError("Embedding configuration differs from library")
                target = settings.data_dir / "uploads" / f"{document.id}-{job.lease_token}.pdf"
                if document.source == "arxiv":
                    try:
                        download_pdf(document.arxiv_id, target)
                        chunks = extract_pdf(target)
                    finally:
                        target.unlink(missing_ok=True)
                else:
                    chunks = extract_pdf(Path(document.file_path))
                vectors = []
                for offset in range(0, len(chunks), 32):
                    vectors.extend(embed_many([c.text for c in chunks[offset : offset + 32]]))
            with SessionLocal.begin() as session:
                active = session.scalar(select(Job).where(Job.id == job.id).with_for_update())
                if active.lease_token != job.lease_token or active.status != "processing":
                    event("job.stale_lease", job_id=job.id)
                    return
                if job.kind == "arxiv_sync":
                    # Serialize per-library deduplication with uploads and concurrent syncs.
                    session.scalar(
                        select(Library).where(Library.id == job.library_id).with_for_update()
                    )
                    ids = []
                    for paper in papers:
                        existing = session.scalar(
                            select(Document).where(
                                Document.library_id == job.library_id,
                                Document.source_key == f"arxiv:{paper.arxiv_id}",
                            )
                        )
                        if existing:
                            ids.append(existing.id)
                            continue
                        doc = Document(
                            library_id=job.library_id,
                            title=paper.title,
                            source="arxiv",
                            source_key=f"arxiv:{paper.arxiv_id}",
                            arxiv_id=paper.arxiv_id,
                        )
                        session.add(doc)
                        session.flush()
                        session.add(
                            Job(
                                library_id=job.library_id,
                                document_id=doc.id,
                                kind="pdf_ingestion",
                                trace_id=job.trace_id,
                            )
                        )
                        ids.append(doc.id)
                    active.result = {"document_ids": ids, "matched": len(papers)}
                else:
                    session.execute(delete(Chunk).where(Chunk.document_id == document.id))
                    session.add_all(
                        [
                            Chunk(
                                document_id=document.id,
                                chunk_index=i,
                                text=c.text,
                                page=c.page,
                                section=c.section,
                                embedding=vectors[i],
                            )
                            for i, c in enumerate(chunks)
                        ]
                    )
                    session.get(Document, document.id).status = "ready"
                    active.result = {"chunks": len(chunks)}
                active.status, active.error, active.completed_at = "completed", None, now()
                active.duration_ms = (time.perf_counter() - started) * 1000
                event(
                    "job.completed",
                    job_id=job.id,
                    duration_ms=active.duration_ms,
                    attempts=active.attempts,
                    **active.result,
                )
            wake()
    except Exception as exc:
        retryable = isinstance(exc, (httpx.TransportError, RedisError, OSError, OperationalError))
        if isinstance(exc, httpx.HTTPStatusError):
            retryable = exc.response.status_code in {408, 429, 500, 502, 503, 504}
        with SessionLocal.begin() as session:
            active = session.scalar(select(Job).where(Job.id == job.id).with_for_update())
            if active.lease_token != job.lease_token:
                return
            retry = retryable and active.attempts < settings.job_max_attempts
            active.status = "retrying" if retry else "failed"
            # Exception strings may contain provider URLs or credentials. Persist only safe messages.
            active.error = str(exc)[:300] if isinstance(exc, InvalidPDF) else type(exc).__name__
            active.available_at = now() + timedelta(
                seconds=settings.retry_seconds * 2 ** (active.attempts - 1)
            )
            active.duration_ms = (time.perf_counter() - started) * 1000
            if not retry:
                active.completed_at = now()
            if active.document_id:
                session.get(Document, active.document_id).status = active.status
            event(
                "job." + active.status,
                job_id=job.id,
                attempts=active.attempts,
                error_type=type(exc).__name__,
            )
    finally:
        stop.set()
        keeper.join(timeout=5)
        trace_id.reset(token)


def run_once():
    job = claim_job()
    if job:
        process(job)
        return True
    return False


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    worker_id = uid()
    keeper = threading.Thread(target=heartbeat, args=(stop, worker_id), daemon=True)
    keeper.start()
    event("worker.started", worker_id=worker_id)
    while not stop.is_set():
        try:
            if not run_once():
                try:
                    redis_client().brpop("litlens:wakeup", timeout=2)
                except RedisError:
                    stop.wait(2)
        except Exception as exc:
            event("worker.error", error_type=type(exc).__name__)
            stop.wait(2)
    keeper.join(timeout=5)


if __name__ == "__main__":
    main()
