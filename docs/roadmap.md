[← Overview](../README.md) · [Architecture](architecture.md) · [Evaluation](evaluation.md)

# Improvement roadmap

Captured September 11, 2026, from the project improvement discussion.
These are proposed priorities saved at the user's request, not a committed delivery schedule or authorization to implement them all.
Reassess the order against the current code and evidence when work resumes.

## Current baseline

LitLens supports real uploaded PDFs and arXiv imports today.
The bundled demo and recorded benchmark use ten original synthetic research-method notes, with 50 answerable questions and 10 unsupported questions.
A real-paper ingestion and cited-answer flow was previously verified with *Attention Is All You Need*; see the [demo record](demo.md).
That verification does not establish quality across a diverse real-paper corpus.

The default configuration uses deterministic hash embeddings, lexical reranking, and extractive answers made from verified source quotations.
Hosted and Ollama adapters exist, but their live model quality is not measured by the recorded default-provider benchmark.
Exact quotation matching establishes textual provenance, not whether the passage adequately answers the question.

## Proposed next milestone

Build a useful single-paper research workflow: ingest a real paper, ask paraphrased questions, retrieve relevant evidence, and inspect citations in the PDF.
Use this milestone to validate retrieval and the reading experience before expanding into cross-paper synthesis.

Suggested completion evidence:

- A versioned real-paper question set with manually checked passages and unanswerable cases.
- A reproducible comparison of the current baseline with learned embeddings and reranking.
- A working UI flow from upload through ingestion status to an answer and its source page.
- Citation navigation and passage highlighting validated against stored page coordinates.
- Recorded failures and support-decision metrics, including false refusals and unsupported answers.

## Prioritized improvements

Priority 1 is complete; priority 5 has an experimental implementation, and priorities 2-4 and 6 remain proposed.
The larger single-paper interface milestone above is not yet complete.

| Priority | Improvement | Intended outcome and verification |
| --- | --- | --- |
| 1 | Evaluate on real papers - complete | Six revision- and checksum-pinned PDFs, 36 source-checked questions, 26 evidence labels, CLI/API selection, and a separate [recorded evaluation](real-paper-evaluation.md). |
| 2 | Improve retrieval quality | Compare learned embeddings and a cross-encoder reranker with hash embeddings and lexical reranking; measure recall, ranking quality, latency, and resource use before changing defaults. |
| 3 | Assess evidence sufficiency | Test whether evidence answers the question with its qualifications intact; measure relevance, contradictions, false refusals, and unsupported answers in addition to exact-quote validity. |
| 4 | Build a research interface | Add the planned Next.js client with library management, ingestion progress, search, and a PDF viewer; clicking a citation should open its page and highlight its passage. |
| 5 | Introduce cited synthesis - experimental | Add a separately evaluated mode for explanations and comparisons across papers; retain quotation mode and check claim-level support and citation provenance. |
| 6 | Improve document understanding | Evaluate OCR, multi-column reading order, tables, and equations; preserve passage coordinates and test extraction on representative difficult pages. |

Passage coordinates are a dependency of reliable citation highlighting, so that part of document understanding may need to precede the interface work.
Keep ingestion, retrieval, and generation independent of the future frontend.

### Priority 1 completion evidence

- [Versioned dataset](../litlens/evaluation/real_papers.json): 18 paraphrases, six qualification questions, three comparisons of differing findings, three ambiguous questions, and six unsupported questions across six published ML papers.
- Gold evidence was checked against extracted text and 16 rendered PDF pages; source revisions, byte hashes, page counts, anchors, and reference summaries are recorded.
- Labels were reviewed by the implementing agent, not an independent human annotator; the corpus is diverse within ML, not across all scientific disciplines.
- `litlens seed-papers` validates and ingests the original PDFs, and `litlens evaluate --dataset real-papers --library-id LIBRARY_ID` compares vector, keyword, and hybrid retrieval.
- [The complete report](evaluation-real-papers.json) is separate from the unchanged synthetic fixture and historical synthetic report; all 534 stored chunks, gold text hashes, and case chunk IDs were audited against PostgreSQL.
- Hybrid reranked recall is 57.4%, false refusals are 51.9%, and support on the nine refusal cases is 44.4%; these measured failures motivate priorities 2 and 3.
- Real PDFs exposed ingestion failures from punctuation-only fragments, compatibility glyphs, and NUL characters; extraction now handles these cases, with a recorded extraction version and regression coverage.
- Completion logs now follow the database commit, so failed persistence cannot produce a misleading success event.
- Final verification: 44 tests passed against PostgreSQL, pgvector, Redis, and live workers, including actual-paper ingestion and evaluation; lint and documentation link checks passed.

Reproduce the full verification with:

```bash
docker compose exec -T -e LITLENS_INTEGRATION=1 -e LITLENS_LIVE=1 \
  -e LITLENS_REAL_PAPERS=1 api pytest -q
```

See [the evaluation guide](real-paper-evaluation.md) for cache behavior, corpus limits, failure analysis, and report reproduction.

### RAG direction update - September 17, 2026

The user confirmed that generated, evidence-grounded answers are the central product goal.
Work on cited generation has started ahead of the earlier ordering.
An opt-in `ANSWER_MODE=synthesis` now sends retrieved evidence to the configured Ollama or hosted model and renders generated claims with citations to validated source quotations.
Quotation mode remains the default and preserves the existing benchmark behavior.
Synthesis skips the lexical evidence cutoff, but learned retrieval and semantic support verification remain outstanding.
A valid quotation establishes provenance, not entailment of the generated claim.

Verification: 37 tests passed, 14 service-dependent tests skipped, and Ruff passed.
Provider tests exercised both transports with simulated model responses, including paraphrased output and citation validation failures.
No local Ollama service was available, so this is not a live-model quality result.
The next milestone is to run synthesis on the pinned real-paper questions with an available model, inspect support and refusals, and improve semantic retrieval against that measured baseline.
Existing real-paper labels were agent-reviewed and have not received independent human review.

## Architecture follow-ups

| Improvement | Intended outcome and verification |
| --- | --- |
| Versioned re-indexing | Rebuild chunks and embeddings in the background, validate the replacement index, and switch over with rollback; verify that queries never mix incompatible embedding spaces. |
| Background evaluations | Run evaluations as durable jobs with progress and cancellation; reuse job recovery infrastructure and test retries, interrupted runs, and user/library isolation. |

## Documentation follow-ups

The README and supporting guides already received a layout and navigation refresh.
Further ideas from the same discussion:

- Record a short terminal demo showing ingestion, a question, and a citation.
- Expand the existing real-paper walkthrough with the actual response and corresponding source passage.
- Add troubleshooting for ingestion failures, scanned PDFs, unavailable models, port conflicts, and insufficient evidence.
- Add a compact CLI command reference.
- Display false-refusal rates alongside citation correctness; the recorded keyword baseline has 100% citation correctness but refuses 16% of answerable questions.

## Resuming work

Choose the next scoped item with the user and inspect the implementation before making changes.
Retain the synthetic fixture for deterministic regression coverage while adding separate real-paper evaluations.
Record the configuration, dataset version, limitations, and verification evidence for completed work here or in a linked report.
Do not describe proposed capabilities as implemented or synthetic benchmark scores as real-paper quality results.
