# LitLens

LitLens is a local research assistant for uploaded PDFs and arXiv papers.
It provides a versioned FastAPI backend and Typer CLI, with PostgreSQL hybrid retrieval, background ingestion, and answers made from verified source quotations.
The default configuration runs without model credentials using deterministic hash embeddings and an extractive answer provider.
Ollama and hosted OpenAI-compatible providers can supply learned embeddings and select evidence through the same interfaces.

## Start the local stack

Prerequisite: Docker with Compose, with ports 8000, 55432, and 56379 available.
From this directory, run:

```bash
docker compose up --build -d --wait
```

This starts PostgreSQL with pgvector, Redis, a migration service, the API, and a worker.
The API is at [localhost:8000](http://localhost:8000/docs), the interactive contract is at `/docs`, and OpenAPI JSON is at `/openapi.json`.
Use `docker compose down` to stop the services while retaining their named data volumes.
Python dependencies are pinned in `requirements.lock`; `pyproject.toml` declares the supported dependency ranges.

## Five-minute demo

```bash
docker compose exec api litlens demo
```

The command creates a library, uploads ten original research-method notes as PDFs, and waits for ingestion.
Copy the printed library ID into the following commands:

```bash
docker compose exec api litlens search "What candidates does hybrid retrieval combine?" --library-id LIBRARY_ID
docker compose exec api litlens ask "What candidates does hybrid retrieval combine?" --library-id LIBRARY_ID
docker compose exec api litlens ask "What is the mass of Jupiter?" --library-id LIBRARY_ID
docker compose exec api litlens evaluate --library-id LIBRARY_ID
```

The first answer quotes the relevant sentence and includes its title, page, section, document ID, and stored chunk ID.
The second answer returns `supported: false` and an empty citation list.
See [the demo walkthrough](docs/demo.md) for a real arXiv example and [the recorded evaluation](docs/evaluation-results.json) for measurements.

## Authentication and libraries

All `/api/v1` routes require `Authorization: Bearer TOKEN`.
The local default is `local-dev-token`, mapped to the user `local`.
For separate local users, copy `.env.example` to `.env` and set `API_TOKENS` to a JSON object mapping distinct tokens to user identities, then recreate the API and worker.
For example, `API_TOKENS={"alice-token":"alice","bob-token":"bob"}` creates two isolated identities.
These example tokens are public development values; choose private tokens for your own data.
An API caller cannot choose an owner ID, and unauthorized library, document, job, and evaluation lookups return 404.
The CLI uses the same authenticated HTTP API.
The stack binds published ports to loopback; PostgreSQL and Redis credentials are intended for this local development stack.

## API examples

Create a library:

```bash
curl -sS http://localhost:8000/api/v1/libraries \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"name":"My research"}'
```

Upload a PDF and poll the returned job ID:

```bash
curl -sS http://localhost:8000/api/v1/documents/upload \
  -H 'Authorization: Bearer local-dev-token' \
  -F library_id=LIBRARY_ID -F file=@paper.pdf
curl -sS http://localhost:8000/api/v1/jobs/JOB_ID \
  -H 'Authorization: Bearer local-dev-token'
```

Search arXiv by identifier or its query syntax:

```bash
curl -sS http://localhost:8000/api/v1/documents/arxiv \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"library_id":"LIBRARY_ID","query":"1706.03762v7","max_results":1}'
```

The sync job reports matched document IDs in `result.document_ids`; PDF ingestion runs in child jobs.
Document details include `latest_job_id` for inspecting those jobs.
Uploads and arXiv ingestion return 202 after committing the durable job, before processing completes.

```bash
curl -sS http://localhost:8000/api/v1/answers \
  -H 'Authorization: Bearer local-dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"library_id":"LIBRARY_ID","query":"What does the Transformer rely on?"}'
```

| Route | Purpose |
| --- | --- |
| `POST /api/v1/libraries` | Create an owned library |
| `POST /api/v1/documents/upload` | Queue a PDF, deduplicated by bytes within its library |
| `POST /api/v1/documents/arxiv` | Queue bounded arXiv search and ingestion |
| `GET /api/v1/documents?library_id=...` | List documents with bounded limit/offset pagination |
| `GET /api/v1/documents/{id}` | Document status, chunk count, and latest job |
| `POST /api/v1/search` | Keyword, vector, or hybrid retrieval with reranking |
| `POST /api/v1/answers` | Verified evidence quotations and citations, or refusal |
| `GET /api/v1/jobs/{id}` | Attempts, status, safe error, duration, and result |
| `POST /api/v1/jobs/{id}/retry` | Create a new attempt series for a failed job, preserving history |
| `GET /api/v1/evaluations?library_id=...` | Read owned evaluation reports |
| `POST /api/v1/evaluations` | Run the bundled benchmark on a seeded library |
| `GET /health` | Process liveness |
| `GET /ready` | Database, migration, vector dimensions, Redis, and recent worker heartbeat |

The checked-in [OpenAPI contract](docs/openapi.json) supports future client generation.
A Next.js application can call these routes through its server layer without changing ingestion, retrieval, or generation code.

## CLI and local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]' -c requirements.lock
litlens library "My research"
litlens ingest pdf paper.pdf --library-id LIBRARY_ID --wait
litlens ingest arxiv 'all:retrieval AND all:generation' --library-id LIBRARY_ID --max-results 3 --wait
litlens status
litlens status --job-id JOB_ID
litlens retry JOB_ID --wait
```

`LITLENS_API_URL`, `LITLENS_API_TOKEN`, and `LITLENS_LIBRARY_ID` configure CLI defaults.
The arXiv command's `--wait` waits for both discovery and the resulting PDF jobs.
Local Python processes use the PostgreSQL and Redis ports in `.env.example`.
To run the API and worker outside Docker, start only `postgres redis`, run `alembic upgrade head`, then run `uvicorn litlens.main:app` and `python -m litlens.worker` in separate terminals.
Use the same absolute `DATA_DIR` for both processes.

## Providers

Generation and embeddings can be selected independently.
Set the following environment variables in `.env` and run `docker compose up --build -d --wait` after changes.
Generation changes do not require re-ingestion.

| Setting | Default | Meaning |
| --- | --- | --- |
| `LLM_PROVIDER` | `extractive` | `extractive`, `openai`, or `ollama` |
| `EMBEDDING_PROVIDER` | `hash` | `hash`, `openai`, or `ollama` |
| `MODEL_BASE_URL` | Host Ollama URL in Compose | Hosted base URL includes `/v1`; Ollama base URL does not |
| `MODEL_API_KEY` | Empty | Hosted credential, supplied only through environment |
| `MODEL_NAME` | `llama3.2` | Model selected for evidence quotation |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Learned embedding model, unused by hash baseline |
| `EMBEDDING_DIMENSIONS` | `384` | Fixed vector dimensions established by the initial migration |
| `EMBEDDING_BASE_URL` | Empty | Optional separate embedding service; otherwise use the model base URL |
| `EMBEDDING_API_KEY` | Empty | Credential for a separate embedding service |
| `COMPLETION_TOKEN_PARAMETER` | `max_completion_tokens` | Use `max_tokens` for compatible servers requiring the legacy parameter |

For Ollama generation, install and start Ollama on the host, pull your chosen chat model, and set `LLM_PROVIDER=ollama`.
For learned Ollama embeddings, also pull an embedding model and match the database vector dimension to that model's output.
For a hosted service, set `LLM_PROVIDER=openai`, its `/v1` base URL, credential, and a chat model supporting the Chat Completions contract.
The adapter omits optional temperature by default and uses the current completion-token parameter documented by [OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Hosted embedding providers must support the configured [embedding dimensions](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
Ollama uses its native `/api/chat` and [`/api/embed`](https://docs.ollama.com/api/embed) endpoints.

Each library records its embedding provider, model, and dimensions.
Changing that embedding space requires a new library and re-ingestion; changing dimensions also requires a separately initialized database or an explicit migration.
Existing libraries fail with a configuration error rather than silently mixing incompatible vectors.
Do not delete existing data volumes to change models unless you intend to discard their contents.

## Architecture and reliability

See [architecture and operational notes](docs/architecture.md) for module boundaries, indexes, queue recovery, and scaling limits.
PostgreSQL is the source of truth for documents, native vectors, jobs, and evaluation results.
Redis delivers bounded worker wakeups and coordinates arXiv request throttling.
Workers also poll committed jobs, so a missed Redis notification cannot lose an ingestion request.
Workers claim jobs using row locks, renew leases, and fence stale attempts before committing chunks.
Transient failures use bounded exponential retries; invalid or textless PDFs fail explicitly.
Retrying a failed job creates a new job while keeping the old failure record.

JSON logs contain request trace IDs, stage span IDs, ingestion and retrieval latency, provider retries, generation token usage, and job throughput inputs.
`X-Request-ID` connects an API request to its background job logs.
Inspect them with `docker compose logs api worker`.
These are structured log traces; this release does not include an OpenTelemetry collector or dashboard.

## Evaluation results and limits

The bundled dataset contains 50 answerable questions and 10 unsupported questions over ten original synthetic research-method notes.
It is a deterministic engineering fixture, not a scientific benchmark on diverse arXiv papers.
The runner compares raw retrieval and reranked recall at five, reciprocal rank, citation validity, exact-quote faithfulness, false refusals, unsupported answers, latency, token usage, ingestion throughput, and retry rates.
Gold passages are resolved from expected title, page, and exact text, so evaluations survive regenerated chunk IDs.

The recorded local Docker run used hash embeddings and the extractive provider:

| Retrieval mode | Recall@5 | MRR@5 | Citation correctness | Unsupported-answer rate |
| --- | ---: | ---: | ---: | ---: |
| Vector | 100% | 98% | 100% | 0% |
| Keyword | 84% | 84% | 100% | 0% |
| Hybrid | 100% | 100% | 100% | 0% |

All emitted quotations matched their stored evidence.
The default provider consumes zero LLM tokens.
See the [recorded JSON report](docs/evaluation-results.json) for timing, ingestion measurements, configuration versions, and failure cases.
Reported evaluation latency includes two retrieval passes and generation per question; it is not a load-test result or isolated API latency.
Exact-quote faithfulness does not measure semantic entailment, whether a source is correct, or whether it fully answers a nuanced question.
Hosted and Ollama adapters have mocked contract tests; real model quality has not been benchmarked without configured model credentials and runtimes.

Representative failures and boundaries:

- Keyword-only retrieval refuses “How is ingestion throughput calculated?” because full-text matching requires query lexemes missing from the source sentence.
- Hash embeddings and lexical reranking can miss synonyms or misjudge questions with negation or subtle qualifications.
- A scanned PDF without selectable text fails with an OCR explanation; OCR is outside this release.
- A provider that invents a quotation, cites an unknown source, or returns malformed JSON produces an unsupported answer with no citations.
- Multi-column reading order, equations, tables, and unusual headings remain PDF extraction limitations; citations identify extracted evidence rather than guaranteeing a perfect reconstruction of the page.
- Answers intentionally return source sentences; cross-paper synthesis and generated paraphrases are not enabled by the strict grounding policy.
- arXiv outages and rate limits may leave retrying or failed jobs; inspect status and use `litlens retry` after the cause is resolved.

## Verification

Fast tests without services:

```bash
pytest -q
ruff check litlens tests migrations
```

Full tests, including a real HTTP upload-to-worker-to-answer flow:

```bash
docker compose exec -e LITLENS_INTEGRATION=1 -e LITLENS_LIVE=1 api pytest -q
```

Integration tests create and remove a uniquely named temporary PostgreSQL database and use real pgvector and Redis.
Live acceptance tests create a small library in the running API database.
Tests cover parsing, section metadata, chunk bounds, provider payloads and retries, stored citations, refusals, ownership, duplicate uploads, request limits, job recovery, arXiv orchestration, evaluation regressions, and OpenAPI drift.
The CI workflow runs the same Compose-based checks.
