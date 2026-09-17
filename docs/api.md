[← Overview](../README.md) · [Demo](demo.md) · [Setup](setup.md) · [API](api.md) · [Architecture](architecture.md) · [Evaluation](evaluation.md)

# API reference

The local base URL is `http://localhost:8000`.
All `/api/v1` routes require a bearer token.
Examples use the local development token; replace `LIBRARY_ID` and `JOB_ID` with returned identifiers.

**Explore:** [Interactive API](http://localhost:8000/docs) · [OpenAPI JSON](openapi.json) · [Authentication](setup.md#authentication-and-libraries)

## API examples

### Create a library

```bash
curl -sS http://localhost:8000/api/v1/libraries \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"name":"My research"}'
```

### Upload and track a PDF

```bash
curl -sS http://localhost:8000/api/v1/documents/upload \
  -H 'Authorization: Bearer local-dev-token' \
  -F library_id=LIBRARY_ID -F file=@paper.pdf
curl -sS http://localhost:8000/api/v1/jobs/JOB_ID \
  -H 'Authorization: Bearer local-dev-token'
```

### Import from arXiv

```bash
curl -sS http://localhost:8000/api/v1/documents/arxiv \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"library_id":"LIBRARY_ID","query":"1706.03762v7","max_results":1}'
```

The sync job reports matched document IDs in `result.document_ids`; PDF ingestion runs in child jobs.
Document details include `latest_job_id` for inspecting those jobs.
Uploads and arXiv ingestion return 202 after committing the durable job, before processing completes.

### Ask a question

```bash
curl -sS http://localhost:8000/api/v1/answers \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"library_id":"LIBRARY_ID","query":"What does the Transformer rely on?"}'
```

## Endpoint directory

| Route | Purpose |
| --- | --- |
| `POST /api/v1/libraries` | Create an owned library |
| `POST /api/v1/documents/upload` | Queue a PDF, deduplicated by bytes within its library |
| `POST /api/v1/documents/arxiv` | Queue bounded arXiv search and ingestion |
| `GET /api/v1/documents?library_id=...` | List documents with bounded limit/offset pagination |
| `GET /api/v1/documents/{id}` | Document status, chunk count, and latest job |
| `POST /api/v1/search` | Keyword, vector, or hybrid retrieval with reranking |
| `POST /api/v1/answers` | Cited quotations or configured experimental synthesis, or refusal |
| `GET /api/v1/jobs/{id}` | Attempts, status, safe error, duration, and result |
| `POST /api/v1/jobs/{id}/retry` | Create a new attempt series for a failed job, preserving history |
| `GET /api/v1/evaluations?library_id=...` | Read owned evaluation reports |
| `POST /api/v1/evaluations` | Run `synthetic` (default) or `real-papers` on its separately seeded library |
| `GET /health` | Process liveness |
| `GET /ready` | Database, migration, vector dimensions, Redis, and recent worker heartbeat |

The checked-in [OpenAPI contract](openapi.json) supports future client generation.

Set `dataset` to `real-papers` in an evaluation request to use the [real-paper benchmark](real-paper-evaluation.md).
The selection is recorded in each stored report's `metrics.dataset`.
A Next.js application can call these routes through its server layer without changing ingestion, retrieval, or generation code.
