import json
import shutil
import uuid
from pathlib import Path
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from litlens.api.schemas import AnswerRequest, LibraryCreate, SearchRequest
from litlens.db.models import Chunk, Document, Job, Library, SessionLocal, init_db
from litlens.generation.answer import answer
from litlens.ingestion.pdf import extract_pdf
from litlens.retrieval.embeddings import embed
from litlens.retrieval.search import search

app = FastAPI(title="LitLens API", version="1.0.0")

@app.on_event("startup")
def startup(): init_db()

def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()

def library_or_404(session, library_id):
    library = session.get(Library, library_id)
    if not library: raise HTTPException(404, "Library not found")
    return library

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/ready")
def ready(session=Depends(db)): return {"status": "ready", "database": "ok"}

@app.post("/api/v1/libraries")
def create_library(payload: LibraryCreate, session=Depends(db)):
    library = Library(name=payload.name, owner_id=payload.owner_id)
    session.add(library); session.commit()
    return {"id": library.id, "name": library.name, "owner_id": library.owner_id}

@app.post("/api/v1/documents/upload")
def upload_document(library_id: str = Form(...), file: UploadFile = File(...), session=Depends(db)):
    library_or_404(session, library_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"): raise HTTPException(400, "Only PDF uploads are supported")
    target_dir = Path("data/uploads"); target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4()}-{Path(file.filename).name}"
    with target.open("wb") as output: shutil.copyfileobj(file.file, output)
    document = Document(library_id=library_id, title=Path(file.filename).stem, file_path=str(target), status="processing")
    session.add(document); session.flush()
    job = Job(document_id=document.id, kind="pdf_ingestion", status="processing", attempts=1); session.add(job)
    try:
        for i, item in enumerate(extract_pdf(target)):
            session.add(Chunk(document_id=document.id, chunk_index=i, text=item.text, page=item.page, section=item.section, embedding=json.dumps(embed(item.text))))
        document.status, job.status = "ready", "completed"
    except Exception as exc:
        document.status, job.status, job.error = "failed", "failed", str(exc)
    session.commit()
    return {"document_id": document.id, "job_id": job.id, "status": document.status}

@app.post("/api/v1/documents/arxiv")
def arxiv_document(library_id: str, arxiv_id: str, session=Depends(db)):
    library_or_404(session, library_id)
    document = Document(library_id=library_id, title=arxiv_id, source="arxiv", arxiv_id=arxiv_id, status="queued")
    job = Job(kind="arxiv_sync", status="queued"); session.add(document); session.flush(); job.document_id = document.id; session.add(job); session.commit()
    return {"document_id": document.id, "job_id": job.id, "status": job.status}

@app.get("/api/v1/documents")
def list_documents(library_id: str, session=Depends(db)):
    library_or_404(session, library_id)
    return [{"id": d.id, "title": d.title, "status": d.status, "source": d.source} for d in session.scalars(select(Document).where(Document.library_id == library_id))]

@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: str, session=Depends(db)):
    d = session.get(Document, document_id)
    if not d: raise HTTPException(404, "Document not found")
    return {"id": d.id, "library_id": d.library_id, "title": d.title, "status": d.status, "chunks": len(d.chunks)}

@app.post("/api/v1/search")
def do_search(payload: SearchRequest, session=Depends(db)):
    library_or_404(session, payload.library_id)
    return {"results": [{"chunk_id": h.chunk.id, "document_id": h.document.id, "text": h.chunk.text, "page": h.chunk.page, "section": h.chunk.section, "score": h.score, "citation": h.citation} for h in search(session, payload.library_id, payload.query, payload.limit)]}

@app.post("/api/v1/answers")
def do_answer(payload: AnswerRequest, session=Depends(db)):
    library_or_404(session, payload.library_id)
    return answer(payload.query, search(session, payload.library_id, payload.query, payload.limit))

@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, session=Depends(db)):
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    return {"id": job.id, "status": job.status, "attempts": job.attempts, "error": job.error}

@app.get("/api/v1/evaluations")
def evaluations(): return {"results": [], "message": "Run `litlens evaluate` after adding an evaluation dataset."}

