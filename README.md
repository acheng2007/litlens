# LitLens

LitLens is a local-first research RAG assistant for arXiv papers and uploaded PDFs.
It exposes a versioned FastAPI contract and a Typer CLI, with provider-agnostic generation and citation-first answers.

## Quick start

```bash
cd /Users/adam/Project/LitLens
python -m venv .venv && source .venv/bin/activate
pip install -e '.[pdf,dev]'
cp .env.example .env
uvicorn litlens.main:app --reload
```

The offline path uses SQLite, deterministic local embeddings, and an explicit insufficient-evidence response.
For the full stack, run `docker compose up --build` after configuring PostgreSQL and Redis in `.env`.

## Architecture

Upload ingestion extracts page text with PyMuPDF, creates section-aware paragraph chunks, stores embeddings, and marks a job complete or failed.
Retrieval combines keyword overlap and cosine similarity behind a stable search service.
Generation receives retrieved context and returns source metadata containing title, page, section, and stored chunk ID.
The provider boundary supports local and OpenAI-compatible generation, with Ollama-ready configuration.

## API

Create a library with `POST /api/v1/libraries`, upload PDFs with `POST /api/v1/documents/upload`, search with `POST /api/v1/search`, and answer with `POST /api/v1/answers`.
OpenAPI is available at `/openapi.json`, with interactive docs at `/docs`.

```bash
curl -X POST localhost:8000/api/v1/libraries -H 'content-type: application/json' -d '{"name":"My papers"}'
curl -F library_id=LIBRARY_ID -F file=@paper.pdf localhost:8000/api/v1/documents/upload
curl -X POST localhost:8000/api/v1/answers -H 'content-type: application/json' -d '{"library_id":"LIBRARY_ID","query":"What is the main result?"}'
```

## Quality and evaluation

The initial tests cover chunk metadata and deterministic embeddings.
The evaluation endpoint is reserved for benchmark output; add JSONL questions with expected citation chunks to extend it with retrieval recall, citation correctness, faithfulness, unsupported-answer rate, latency, token usage, throughput, and retries.
Failure handling is explicit: non-PDF uploads are rejected, failed ingestion jobs report an error, and answers with insufficient evidence refuse instead of inventing a result.

