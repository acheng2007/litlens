[← Overview](../README.md) · [Demo](demo.md) · [Setup](setup.md) · [API](api.md) · [Architecture](architecture.md) · [Evaluation](evaluation.md)

# Setup and development

Configure the stack, choose providers, and run the verification suite.

[Local stack](#start-the-local-stack) · [Authentication](#authentication-and-libraries) · [Development](#cli-and-local-development) · [Providers](#providers) · [Verification](#verification)

## Start the local stack

Prerequisite: Docker with Compose, with ports 8000, 55432, and 56379 available.
From the project root, run:

```bash
docker compose up --build -d --wait
```

This starts PostgreSQL with pgvector, Redis, a migration service, the API, and a worker.
The API is at [localhost:8000](http://localhost:8000/docs), the interactive contract is at `/docs`, and OpenAPI JSON is at `/openapi.json`.
Use `docker compose down` to stop the services while retaining their named data volumes.
Python dependencies are pinned in `requirements.lock`; `pyproject.toml` declares the supported dependency ranges.

## Authentication and libraries

All `/api/v1` routes require `Authorization: Bearer TOKEN`.
The local default is `local-dev-token`, mapped to the user `local`.
For separate local users, copy `.env.example` to `.env` and set `API_TOKENS` to a JSON object mapping distinct tokens to user identities, then recreate the API and worker.
For example, `API_TOKENS={"alice-token":"alice","bob-token":"bob"}` creates two isolated identities.
These example tokens are public development values; choose private tokens for your own data.
An API caller cannot choose an owner ID, and unauthorized library, document, job, and evaluation lookups return 404.
The CLI uses the same authenticated HTTP API.
The stack binds published ports to loopback; PostgreSQL and Redis credentials are intended for this local development stack.

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
| `ANSWER_MODE` | `quotes` | `quotes` or experimental `synthesis`; synthesis requires a model provider |
| `LLM_PROVIDER` | `extractive` | `extractive`, `openai`, or `ollama` |
| `EMBEDDING_PROVIDER` | `hash` | `hash`, `openai`, or `ollama` |
| `MODEL_BASE_URL` | Host Ollama URL in Compose | Provider-specific API base URL; hosted calls append `/chat/completions` |
| `MODEL_API_KEY` | Empty | Hosted credential, supplied only through environment |
| `MODEL_NAME` | `llama3.2` | Model selected for answering |
| `MODEL_MAX_TOKENS` | `1000` | Output budget per call, including reasoning tokens when counted by the provider |
| `MODEL_JSON_MODE` | `false` | Request `response_format: {"type":"json_object"}` from hosted providers that support JSON mode |
| `MODEL_MIN_REQUEST_INTERVAL_SECONDS` | `0` | Minimum time between outbound model requests in one API process |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Learned embedding model, unused by hash baseline |
| `EMBEDDING_DIMENSIONS` | `384` | Fixed vector dimensions established by the initial migration |
| `EMBEDDING_BASE_URL` | Empty | Optional separate embedding service; otherwise use the model base URL |
| `EMBEDDING_API_KEY` | Empty | Credential for a separate embedding service |
| `COMPLETION_TOKEN_PARAMETER` | `max_completion_tokens` | Use `max_tokens` for compatible servers requiring the legacy parameter |

For Ollama generation, install and start Ollama on the host, pull your chosen chat model, and set `LLM_PROVIDER=ollama`.
For learned Ollama embeddings, also pull an embedding model and match the database vector dimension to that model's output.
For a hosted service, set `LLM_PROVIDER=openai`, its provider-specific base URL, credential, and a chat model supporting the Chat Completions contract.
The adapter omits optional temperature by default and uses the current completion-token parameter documented by [OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Hosted embedding providers must support the configured [embedding dimensions](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
Ollama uses its native `/api/chat` and [`/api/embed`](https://docs.ollama.com/api/embed) endpoints.

For Z.ai's standard API, use `MODEL_BASE_URL=https://api.z.ai/api/paas/v4` and `COMPLETION_TOKEN_PARAMETER=max_tokens`.
Set `MODEL_JSON_MODE=true` to request JSON without Markdown fences, and consider `MODEL_MAX_TOKENS=4096` for models that spend output tokens on reasoning.
If a free tier is rate limited, set `MODEL_MIN_REQUEST_INTERVAL_SECONDS=15`; HTTP 429 retries wait at least 15 seconds and honor a longer `Retry-After` value.
The larger budget remains subject to the research workflow's total deadline.
Confirm model access and current pricing in your provider account before evaluation.

Each library records its embedding provider, model, and dimensions.
Changing that embedding space requires a new library and re-ingestion; changing dimensions also requires a separately initialized database or an explicit migration.
Existing libraries fail with a configuration error rather than silently mixing incompatible vectors.
Do not delete existing data volumes to change models unless you intend to discard their contents.

## Experimental RAG synthesis

Set `ANSWER_MODE=synthesis` together with `LLM_PROVIDER=ollama` or `LLM_PROVIDER=openai` and the model settings described above.
Recreate the services with `docker compose up --build -d --wait`, then use the existing `litlens ask` command or `/api/v1/answers` endpoint.
For example, with an available Ollama chat model:

```dotenv
ANSWER_MODE=synthesis
LLM_PROVIDER=ollama
MODEL_NAME=llama3.2
MODEL_BASE_URL=http://host.docker.internal:11434
```

The model receives retrieved passages and writes up to five claims in its own words, each accompanied by supporting quotations.
LitLens checks that each quotation occurs in its referenced passage and builds citation markers and page metadata from stored records.
Malformed output, missing evidence, and fabricated quotations produce a refusal.
Empty model claims also produce a refusal.
`ANSWER_MODE=quotes` restores the original quotation behavior without re-ingestion.

Synthesis responses use `prompt_version=evidence-synthesis-v1` and `reason=generated_answer_with_verified_quotes`.
Here, `supported=true` means the model supplied evidence with verified textual provenance; it does not mean an independent check established that the evidence entails the generated claim.
Synthesis does not apply the quotation mode's lexical overlap threshold, which can reject paraphrased questions, but retrieval still uses the existing lexical reranker.
Hash embeddings remain the default; this feature alone does not improve retrieval quality.
Live synthesis quality, semantic support, and cross-paper comparisons have not yet been benchmarked.
The recorded synthetic and real-paper results describe quotation mode, not synthesis.

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
