import hashlib
import time
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from litlens.api.dependencies import db, library_or_404, owner
from litlens.api.limits import BodyLimit
from litlens.api.schemas import (
    AnswerRequest,
    AnswerResponse,
    ArxivRequest,
    DocumentDetail,
    DocumentView,
    Enqueued,
    EvaluationRequest,
    EvaluationView,
    Health,
    JobView,
    LibraryCreate,
    LibraryView,
    SearchRequest,
    SearchResponse,
)
from litlens.config import settings
from litlens.db.models import Chunk, Document, Evaluation, Job, Library
from litlens.generation.answer import answer
from litlens.observability import event, trace_id
from litlens.retrieval.search import search
from litlens.worker import redis_client, wake

app = FastAPI(
    title="LitLens API",
    version="1.0.0",
    description="Library-isolated research retrieval and verified, cited source quotations.",
)
app.add_middleware(BodyLimit)


@app.middleware("http")
async def trace_request(request, call_next):
    token = trace_id.set(str(uuid4()))
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id.get()
        event(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return response
    finally:
        trace_id.reset(token)


@app.exception_handler(httpx.HTTPError)
async def provider_error(request, exc):
    event("provider.unavailable", error_type=type(exc).__name__)
    return JSONResponse(status_code=502, content={"detail": "Model provider unavailable"})


@app.exception_handler(ValueError)
async def configuration_error(request, exc):
    event("configuration.error", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=409,
        content={
            "detail": "Configuration or provider output is incompatible; check embedding settings and service logs"
        },
    )


@app.get("/health", response_model=Health)
def health():
    return {"status": "ok"}


@app.get("/ready", response_model=Health)
def ready(session=Depends(db)):
    try:
        if session.execute(text("SELECT version_num FROM alembic_version")).scalar() != "0001":
            raise RuntimeError("Migration required")
        dimensions = session.execute(
            text(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        ).scalar()
        if dimensions != settings.embedding_dimensions:
            raise RuntimeError("Embedding dimensions differ from schema")
        client = redis_client()
        client.ping()
        if not next(client.scan_iter("litlens:worker:*", count=100), None):
            raise RuntimeError("No worker heartbeat")
    except Exception:
        raise HTTPException(503, "Database, schema, Redis, or worker unavailable") from None
    return {"status": "ready"}


@app.post("/api/v1/libraries", response_model=LibraryView, status_code=201)
def create_library(payload: LibraryCreate, session=Depends(db), user=Depends(owner)):
    library = Library(name=payload.name, owner_id=user)
    session.add(library)
    session.commit()
    return library


@app.post("/api/v1/documents/upload", response_model=Enqueued, status_code=202)
def upload_document(
    library_id: str = Form(...),
    file: UploadFile = File(...),
    session=Depends(db),
    user=Depends(owner),
):
    library_or_404(session, library_id, user)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF uploads are supported")
    target_dir = settings.data_dir / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid4()}.pdf"
    digest, size = hashlib.sha256(), 0
    try:
        with target.open("wb") as output:
            while part := file.file.read(65536):
                if size == 0 and not part.startswith(b"%PDF-"):
                    raise HTTPException(400, "Invalid PDF signature")
                size += len(part)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "PDF exceeds upload size limit")
                digest.update(part)
                output.write(part)
        if size == 0:
            raise HTTPException(400, "Empty upload")
        library = library_or_404(session, library_id, user, lock=True)
        if library.embedding_space != settings.embedding_space:
            raise HTTPException(409, "Embedding configuration differs from library")
        existing = session.scalar(
            select(Document).where(
                Document.library_id == library_id, Document.source_key == digest.hexdigest()
            )
        )
        if existing:
            job = session.scalar(
                select(Job)
                .where(Job.document_id == existing.id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            target.unlink(missing_ok=True)
            return {"document_id": existing.id, "job_id": job.id, "status": job.status}
        document = Document(
            library_id=library_id,
            title=Path(file.filename).stem[:500],
            source_key=digest.hexdigest(),
            file_path=str(target.resolve()),
        )
        session.add(document)
        session.flush()
        job = Job(
            library_id=library_id,
            document_id=document.id,
            kind="pdf_ingestion",
            trace_id=trace_id.get(),
        )
        session.add(job)
        session.commit()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()
    wake()
    return {"document_id": document.id, "job_id": job.id, "status": job.status}


@app.post("/api/v1/documents/arxiv", response_model=Enqueued, status_code=202)
def arxiv_document(payload: ArxivRequest, session=Depends(db), user=Depends(owner)):
    library_or_404(session, payload.library_id, user)
    job = Job(
        library_id=payload.library_id,
        kind="arxiv_sync",
        payload=payload.model_dump(exclude={"library_id"}),
        trace_id=trace_id.get(),
    )
    session.add(job)
    session.commit()
    wake()
    return {"job_id": job.id, "status": job.status}


@app.get("/api/v1/documents", response_model=list[DocumentView])
def list_documents(
    library_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session=Depends(db),
    user=Depends(owner),
):
    library_or_404(session, library_id, user)
    return session.scalars(
        select(Document)
        .where(Document.library_id == library_id)
        .order_by(Document.created_at, Document.id)
        .limit(limit)
        .offset(offset)
    ).all()


@app.get("/api/v1/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, session=Depends(db), user=Depends(owner)):
    document = session.scalar(
        select(Document).join(Library).where(Document.id == document_id, Library.owner_id == user)
    )
    if not document:
        raise HTTPException(404, "Document not found")
    count = session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
    )
    latest_job = session.scalar(
        select(Job.id)
        .where(Job.document_id == document_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    return {
        **DocumentView.model_validate(document).model_dump(),
        "chunks": count,
        "latest_job_id": latest_job,
    }


def retrieve(payload, session, user):
    library_or_404(session, payload.library_id, user)
    return search(session, payload.library_id, payload.query, payload.limit, payload.mode, user)


@app.post("/api/v1/search", response_model=SearchResponse)
def do_search(payload: SearchRequest, session=Depends(db), user=Depends(owner)):
    return {
        "results": [
            {
                "chunk_id": h.chunk.id,
                "document_id": h.document.id,
                "title": h.document.title,
                "text": h.chunk.text,
                "page": h.chunk.page,
                "section": h.chunk.section,
                "score": h.score,
                "retrieval_score": h.retrieval_score,
                "citation": h.citation,
            }
            for h in retrieve(payload, session, user)
        ]
    }


@app.post("/api/v1/answers", response_model=AnswerResponse)
def do_answer(payload: AnswerRequest, session=Depends(db), user=Depends(owner)):
    return answer(payload.query, retrieve(payload, session, user))


@app.get("/api/v1/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, session=Depends(db), user=Depends(owner)):
    job = session.scalar(
        select(Job).join(Library).where(Job.id == job_id, Library.owner_id == user)
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/v1/evaluations", response_model=list[EvaluationView])
def evaluations(
    library_id: str, limit: int = Query(20, ge=1, le=100), session=Depends(db), user=Depends(owner)
):
    library_or_404(session, library_id, user)
    return session.scalars(
        select(Evaluation)
        .where(Evaluation.library_id == library_id)
        .order_by(Evaluation.created_at.desc())
        .limit(limit)
    ).all()


@app.post("/api/v1/jobs/{job_id}/retry", response_model=Enqueued, status_code=202)
def retry_job(job_id: str, session=Depends(db), user=Depends(owner)):
    failed = session.scalar(
        select(Job).join(Library).where(Job.id == job_id, Library.owner_id == user)
    )
    if not failed:
        raise HTTPException(404, "Job not found")
    library_or_404(session, failed.library_id, user, lock=True)
    if failed.status != "failed":
        raise HTTPException(409, "Only failed jobs can be retried")
    if failed.document_id:
        document = session.get(Document, failed.document_id)
        if document.status == "ready":
            raise HTTPException(409, "Document is already ready")
        active = session.scalar(
            select(Job)
            .where(
                Job.document_id == failed.document_id,
                Job.status.in_(["queued", "processing", "retrying"]),
            )
            .limit(1)
        )
        if active:
            return {"job_id": active.id, "document_id": active.document_id, "status": active.status}
        document.status = "queued"
    job = Job(
        library_id=failed.library_id,
        document_id=failed.document_id,
        kind=failed.kind,
        payload=failed.payload,
        trace_id=trace_id.get(),
    )
    session.add(job)
    session.commit()
    wake()
    return {"job_id": job.id, "document_id": job.document_id, "status": job.status}


@app.post("/api/v1/evaluations", response_model=EvaluationView, status_code=201)
def run_evaluation(payload: EvaluationRequest, session=Depends(db), user=Depends(owner)):
    from litlens.evaluation.runner import evaluate

    library_or_404(session, payload.library_id, user)
    return evaluate(session, payload.library_id, user)
