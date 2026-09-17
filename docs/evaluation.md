[← Overview](../README.md) · [Demo](demo.md) · [Setup](setup.md) · [API](api.md) · [Architecture](architecture.md) · [Evaluation](evaluation.md)

# Evaluation and limitations

Compare retrieval modes against a reproducible fixture and understand what the measurements establish.

Two datasets are available: the synthetic fixture below and the separate [real-paper evaluation](real-paper-evaluation.md).
Use `--dataset real-papers` to select the latter; the default remains `synthetic`.

## Recorded baseline

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
See the [recorded JSON report](evaluation-results.json) for timing, ingestion measurements, configuration versions, and failure cases.
Reported evaluation latency includes two retrieval passes and generation per question; it is not a load-test result or isolated API latency.
Exact-quote faithfulness does not measure semantic entailment, whether a source is correct, or whether it fully answers a nuanced question.
Hosted and Ollama adapters have mocked contract tests; real model quality has not been benchmarked without configured model credentials and runtimes.

## Failure cases

- Keyword-only retrieval refuses “How is ingestion throughput calculated?” because full-text matching requires query lexemes missing from the source sentence.
- Hash embeddings and lexical reranking can miss synonyms or misjudge questions with negation or subtle qualifications.
- A scanned PDF without selectable text fails with an OCR explanation; OCR is outside this release.
- A provider that invents a quotation, cites an unknown source, or returns malformed JSON produces an unsupported answer with no citations.
- Multi-column reading order, equations, tables, and unusual headings remain PDF extraction limitations; citations identify extracted evidence rather than guaranteeing a perfect reconstruction of the page.
- Answers intentionally return source sentences; cross-paper synthesis and generated paraphrases are not enabled by the strict grounding policy.
- arXiv outages and rate limits may leave retrying or failed jobs; inspect status and use `litlens retry` after the cause is resolved.


## Reproduce the benchmark

From the project root:

```bash
docker compose up --build -d --wait
docker compose exec api litlens demo
```

Replace `LIBRARY_ID` with the printed ID:

```bash
docker compose exec api litlens evaluate --library-id LIBRARY_ID
```

Keep the seeded library separate from uploaded papers so the corpus stays comparable.
Read stored reports with `GET /api/v1/evaluations?library_id=LIBRARY_ID` using your bearer token.
See the [API reference](api.md) and [metric definitions](architecture.md#evaluation-and-observability).
