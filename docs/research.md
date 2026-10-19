[← Overview](../README.md) · [Roadmap](roadmap.md) · [Evaluation](evaluation.md)

# Bounded research workflow

Research is opt-in with `workflow: research` on answer and evaluation requests, or `--workflow research` in the CLI.
Direct quotation and synthesis remain available; `direct` and quotation mode remain the defaults.
Research requires `LLM_PROVIDER=ollama` or `openai`, using the existing provider URL and credentials.

```bash
litlens ask "How do these studies differ?" --library-id LIBRARY_ID --workflow research
litlens evaluate --library-id SYNTHETIC_LIBRARY --dataset synthetic --workflow direct --output direct-synthetic.json
litlens evaluate --library-id SYNTHETIC_LIBRARY --dataset synthetic --workflow research --output research-synthetic.json
litlens evaluate --library-id REAL_LIBRARY --dataset real-papers --workflow direct --output direct-real.json
litlens evaluate --library-id REAL_LIBRARY --dataset real-papers --workflow research --output research-real.json
litlens evaluate-verifier --library-id LIBRARY_ID --output claim-support.json
```

Use new report paths; the CLI refuses to overwrite existing reports.
Evaluation runs synchronously and can take substantially longer than a single answer request.
For a full remote-model evaluation, increase the CLI HTTP timeout with `LITLENS_API_TIMEOUT_SECONDS` if needed.

A typed LangGraph graph runs planning, retrieval, synthesis, quotation provenance validation, semantic claim verification, and finalization.
The planner supplies one to three focused queries, document needs, and answer requirements without exposing private reasoning.
It never discovers or imports papers.
Every search enforces authenticated ownership, the selected library, its embedding space, and ready-document status.
Retrieved rankings are combined with reciprocal rank fusion, deduplicated by chunk ID, and capped at ten passages.
The first five passages from the first and final rankings are retained separately for evaluation.

Generated claims stay structured until their citations are validated.
The verifier receives each complete claim, its cited quotations, and their full stored passage context so an accurate quotation can still fail semantic support.
Its validated output must cover every claim exactly once and use only evidence references cited by that claim.
Supported, unsupported, and contradicted are model judgments, not guarantees of factual correctness.
A failed semantic verdict permits one targeted retrieval and synthesis revision, followed by verification of every revised claim.
A successful answer requires all final claims to pass.
A final failure returns a refusal with evidence gaps; malformed output or invalid provenance fails closed.

The graph permits at most two evidence rounds and six logical generation calls.
Its current route uses three calls for first-pass success and five when revised.
Query embedding requests and all transport retries are accounted for separately.
There are at most three transport attempts per provider call.
`RESEARCH_DEADLINE_SECONDS` defaults to 180 seconds and applies across stages and retries.
Remaining time bounds the entire provider response and each retrieval SQL statement.
Durable summary persistence follows execution and adds database overhead beyond the reported workflow deadline.
A deadline failure returns HTTP 504; an upstream service failure returns HTTP 502, distinct from an evidence refusal.

`PLANNER_MODEL` and `VERIFIER_MODEL` optionally override `MODEL_NAME` on the same provider.
Planner, synthesis, and verifier prompts have separate `research-*-v1` versions.
Document text is explicitly treated as untrusted data in all role prompts.

```dotenv
LLM_PROVIDER=openai
MODEL_NAME=your-synthesis-model
PLANNER_MODEL=your-planner-model
VERIFIER_MODEL=your-verifier-model
RESEARCH_DEADLINE_SECONDS=180
MODEL_PRICES={"your-synthesis-model":{"input":1.0,"output":4.0},"your-planner-model":{"input":1.0,"output":4.0},"your-verifier-model":{"input":1.0,"output":4.0}}
```

The example prices are illustrative assumptions, not current provider prices.
Prices are USD per million input and output tokens, keyed by model name; include the embedding model when using hosted embeddings.
Missing usage stays `null`, with `usage.available: false`; known attempt counts remain visible even when aggregate tokens are unknown.
A failed attempt has unknown usage because the upstream service may have processed it.
Missing prices or usage make `estimated_cost_usd` unknown and `cost_complete: false`.
Local inference defaults to unknown monetary cost even when token counts are available; explicit per-model prices can supply a local cost assumption.

Answer responses retain the existing fields and add the public plan, evidence gaps, claim verdicts and history, request ID, stage timings, aggregate usage, cost status, and attempt metadata.
Run `alembic upgrade head` to install migration `0002` before serving requests.
Authenticated `GET /api/v1/requests/REQUEST_ID?library_id=LIBRARY_ID` returns the durable summary only when both library and owner match.
Summaries and structured JSON traces contain durations, role/model/prompt versions, usage, pricing assumptions, retries, and outcomes.
They do not store questions, model prompts, document bodies, generated answers, or quotation text.
Evaluation reports retain their existing public questions and benchmark annotations.
No dashboard, OpenTelemetry exporter, durable research jobs, external discovery, or new frontend is included.

## Evaluation and verification

Recall@5 and MRR@5 use the final ranked evidence, with first-round ranking metrics recorded separately.
Raw single-query retrieval remains separately reported for comparison.
Cases record workflow latency, stage timings, tokens, estimated cost, and request IDs.
Known refusal cases measure unsupported-answer rate; answerable cases measure false-refusal rate.
`model_assessed_unsupported_claim_rate` describes runtime verifier judgments only and must not be presented as an independently measured hallucination rate.

The separate [claim-support-v1 challenge](../litlens/evaluation/claim_support.json) contains 15 original synthetic cases with fixture labels for paraphrases, numerical errors, missing qualifications, contradictions, and misleading but valid quotations.
`evaluate-verifier` measures accuracy, failed-output rate, and a confusion matrix against those labels.
The labels were reviewed by an agent and have not received independent human review.
The existing real-paper dataset still consists of six pinned PDFs and 36 agent-reviewed questions; it also has no independent human review.
Historical synthetic and real-paper reports remain unchanged.
Fresh direct runs are recorded separately in the [synthetic report](evaluation-direct-synthetic-v1.json), [real-paper report](evaluation-direct-real-papers-v1.json), and [verification summary](research-verification.json).
With hash embeddings and quotation answers, the fresh hybrid synthetic run measured 98% Recall@5, 2% false refusals, and 0% unsupported answers on known refusal cases.
The fresh hybrid real-paper run measured 57.4% Recall@5, 51.9% false refusals, and 33.3% unsupported answers on known refusal cases.
Its median and p95 execution latency, including summary persistence, were 4.3 ms and 5.9 ms; token counts were zero for deterministic extraction, and monetary cost was unknown.
Approximate retrieval rankings can vary with shared HNSW index contents; these fresh direct runs do not establish quality improvement.

Implementation verification on October 12, 2026: 76 tests passed inside the Python 3.12 containers against PostgreSQL, pgvector, Redis, and a live HTTP API plus a controlled HTTP model transport; one pinned-paper acceptance test passed separately on the host against the same live API and workers.
The HTTP CLI acceptance covered a successful answer, a corrected draft, and a final refusal.
Controlled responses establish routing, transport, citation checking, and accounting behavior; they do not measure real-model semantic quality.
Tests also cover malformed outputs, invalid provenance and evidence references, revision bounds, wall-clock cancellation, embedding usage, unknown costs, persisted failures, and owner/library isolation.
One paced live pilot now uses Z.ai GLM-4.7-Flash on the first pinned real-paper question.
All four generated claims passed semantic verification after three logical model calls and three retries across six provider attempts.
The request took 110.8 seconds, including 45.4 seconds in planning, 45.5 seconds in synthesis, and 19.8 seconds in verification.
The provider returned token usage for successful attempts, but the request-wide total and estimated cost remain unknown because failed attempts did not report usage.
This single answer is a smoke test, not a quality benchmark; the full 36-question comparison and live verifier challenge remain outstanding.

```bash
DATABASE_URL=postgresql+psycopg://litlens:litlens@localhost:55433/litlens \
LITLENS_INTEGRATION=1 LITLENS_RESEARCH_HTTP=1 .venv/bin/pytest -q
```

The Docker verification command is:

```bash
docker compose exec -T -e LITLENS_INTEGRATION=1 -e LITLENS_LIVE=1 \
  -e LITLENS_RESEARCH_HTTP=1 api pytest -q
```

The separate host pinned-paper test uses `LITLENS_REAL_PAPERS=1` and the checked PDF cache.
The Docker build and `pip check` passed.
The build backend is now pinned, and dependency installation is cached separately from source changes.

Port 55433 was used during verification because an unrelated database occupied the default 55432 port.
Set the URL to the isolated LitLens database in your environment.
