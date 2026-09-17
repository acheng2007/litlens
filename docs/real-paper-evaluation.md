[← Overview](../README.md) · [Evaluation](evaluation.md) · [API](api.md) · [Roadmap](roadmap.md)

# Evaluation on real papers

The `real-papers` dataset evaluates the actual PDF ingestion and retrieval pipeline on six published research papers.
It is separate from the synthetic `litlens demo` fixture and does not replace that regression suite.

## Corpus and labels

Version `1.0.0` contains 36 questions: 18 paraphrases, six qualification questions, three comparisons of differing findings, three ambiguous questions, and six unsupported questions.
There are 27 answerable cases and nine cases expected to return insufficient evidence.
Comparisons require evidence for each side; differences across architectures or experimental conditions are not labeled as logical contradictions.

| Paper | Pinned arXiv revision | Focus |
| --- | --- | --- |
| Attention Is All You Need | [1706.03762v7](https://arxiv.org/abs/1706.03762v7) | Translation and attention |
| BERT | [1810.04805v2](https://arxiv.org/abs/1810.04805v2) | Language representation and pretraining |
| Retrieval-Augmented Generation | [2005.11401v4](https://arxiv.org/abs/2005.11401v4) | External knowledge and generation |
| Lost in the Middle | [2307.03172v3](https://arxiv.org/abs/2307.03172v3) | Context position and retrieval failures |
| Mamba | [2312.00752v2](https://arxiv.org/abs/2312.00752v2) | Selective state spaces and long sequences |
| Deep Residual Learning for Image Recognition | [1512.03385v1](https://arxiv.org/abs/1512.03385v1) | Computer vision and optimization |

The [dataset manifest](../litlens/evaluation/real_papers.json) pins PDF SHA-256 hashes, page counts, 26 evidence labels, question categories, and reference-answer summaries.
Evidence labels use one-based PDF pages and short text anchors instead of regenerated chunk IDs.
The section label describes the source PDF; it is not inferred from the parser's potentially imperfect section metadata.
Multiple overlapping chunks containing the same anchor are alternatives for one evidence label, not additional facts that must all be retrieved.

Questions and reference summaries were authored and source-checked by the implementing agent, including visual inspection of the 16 cited PDF pages.
They were prepared before running the benchmark, not selected based on retrieval scores.
They have **not** received independent human annotation review.
The corpus spans several machine-learning tasks and PDF layouts, but remains a small, curated ML corpus rather than a cross-discipline scientific benchmark.

## Run it

Start the stack from the project root:

```bash
docker compose up --build -d --wait
docker compose exec api litlens seed-papers
```

The seed command downloads six pinned PDFs, verifies their bytes and page counts, creates a dedicated library, uploads the originals through the API, and waits for the background jobs.
Its final JSON includes the new `library_id`.
Replace `LIBRARY_ID` below with that value:

```bash
docker compose exec api litlens evaluate --dataset real-papers --library-id LIBRARY_ID
```

To save a complete report from the container:

```bash
docker compose exec api litlens evaluate --dataset real-papers \
  --library-id LIBRARY_ID --output /app/data/real-paper-report.json
docker compose cp api:/app/data/real-paper-report.json ./real-paper-report.json
```

Choose a new output filename on subsequent runs; the CLI refuses to overwrite an existing report.
For a locally installed CLI, the same `seed-papers` and `evaluate` commands work without the Compose prefix.
Use `--cache-dir PATH` to select a PDF cache; the default is `data/evaluation-papers`.
Inside Compose that cache is on the persistent shared data volume.
Network access is needed only for uncached PDFs; downloads are spaced at least three seconds apart.
Failed downloads leave no valid cache entry; a checksum mismatch stops the seed rather than silently accepting a revised file.
Original papers are downloaded locally and are not checked into the repository.

The API equivalent is:

```json
{
  "library_id": "LIBRARY_ID",
  "dataset": "real-papers"
}
```

Send this body to `POST /api/v1/evaluations` with your bearer token.
Omitting `dataset` retains the existing synthetic evaluation behavior.
The real-paper evaluator requires exactly the six checksum-pinned uploads, all ready, and rejects missing or extra documents with a 409 response.
Normal arXiv imports remain supported by LitLens, but this benchmark deliberately uses verified PDF uploads to pin document bytes rather than trusting metadata alone.

## What the metrics mean

| Measurement | Interpretation |
| --- | --- |
| Recall@5 | Average fraction of required evidence labels found in the first five results, before reranking |
| Reranked recall@5 | The same measurement after reranking |
| MRR@5 | Rank of the first matching gold chunk; this does not require all sides of a comparison |
| Citation correctness | Citation fields agree with the stored document and chunk, not a claim of semantic correctness |
| Quote faithfulness | The emitted quotation is present in its cited stored chunk |
| False-refusal rate | Fraction of the 27 answerable questions returned as unsupported |
| Unsupported-answer rate | Fraction of the nine ambiguous or unsupported questions returned as supported |
| Citation evidence recall | Per-case fraction of required gold evidence groups represented by citations |
| Quoted-anchor coverage | Conservative per-case overlap with the short gold anchors; not semantic entailment |
| Category breakdown | Support-decision accuracy and retrieval recall for each question type |

Reference summaries guide review but are not scored by a model or an exact-string answer grader.
A supported response is not automatically counted as semantically correct.
Latency includes two retrieval passes and generation, so it is neither isolated endpoint latency nor a capacity test.
Token counts and ingestion metrics use the existing provider and job instrumentation.

The report records dataset and PDF hashes, provider configuration, extraction/chunking/prompt versions, Python and PyMuPDF versions, and the gold labels resolved to stored chunk IDs and text hashes.
Per-question results are retained even when the system fails.
The report avoids copying full paper passages; use its page references and the source PDFs for qualitative review.

## Recorded results

The [complete report](evaluation-real-papers.json) records a local Docker run with hash embeddings, lexical reranking, and extractive generation.
It processed all six PDFs into 534 chunks, with no failed jobs or retries in the successful run.

| Mode | Raw recall@5 | Reranked recall@5 | False refusals | Answers on refusal cases |
| --- | ---: | ---: | ---: | ---: |
| Vector | 33.3% | 57.4% | 51.9% | 44.4% |
| Keyword | 0.0% | 0.0% | 100.0% | 22.2% |
| Hybrid | 25.9% | 57.4% | 51.9% | 44.4% |

Citation correctness and exact-quote faithfulness are 100% for emitted citations in all three modes.
This does **not** mean that the answers are correct: vector and hybrid modes answer all three ambiguous questions and one of the six unsupported questions.
The final column uses all nine refusal cases as its denominator; the category breakdown in the JSON keeps ambiguity separate from unsupported questions.
No LLM tokens were consumed by the default provider.
These measurements do not establish quality for hosted or Ollama models.

Representative failures:

- `attention-2`: reranking retrieves the labeled explanation of multiple attention heads, but generation still refuses the question.
- `ambiguous-model`: the system returns a source quotation even though the question does not specify which model it means.
- `unsupported-middle`: a question asking for a universal claim about future models gets a supported response, despite the paper only providing empirical findings for tested models.
- Keyword retrieval finds no labeled evidence for the answerable questions; its conjunctive full-text query is too restrictive for these paraphrases and paper-specific wording.
- The cross-paper comparisons remain difficult; retrieving a passage from one side does not establish evidence for the whole comparison.

The first live attempt also exposed ingestion failures on BERT, Mamba, and RAG.
PDF extraction now normalizes compatibility glyphs, removes NUL characters rejected by PostgreSQL, and omits punctuation-only chunks that yield zero hash vectors.
This preprocessing is recorded as `pymupdf-nfkc-v1`; the hash embedding algorithm itself remains `sha256-v1`.
Worker completion logs now emit only after the database transaction commits, avoiding a misleading success event when persistence fails.
The failed development library is retained for inspection, while the published report uses a newly ingested library after the fix.
PDF reading order and section detection remain imperfect, particularly in two-column layouts; source section labels and stored section metadata are kept distinct in the evaluation artifacts.

## Verification and maintenance

Fast tests validate label references, alternative-chunk scoring, checksum enforcement, incomplete libraries, and CLI report handling.
The normal integration suite also checks real-dataset authorization and API errors against PostgreSQL and Redis.
An opt-in test downloads or reuses real PDFs and exercises seed, upload, worker processing, all three retrieval modes, report export, and report persistence:

```bash
docker compose exec -e LITLENS_REAL_PAPERS=1 api pytest -q tests/test_real_papers_live.py
```

Use `LITLENS_PAPER_CACHE` to override the cache path in that test.
Use `LITLENS_REAL_REPORT` to save its report to a new path rather than a temporary test directory.
Real-paper downloads are opt-in so ordinary CI does not depend on arXiv availability.

When changing labels, update the dataset version, review the source pages, and regenerate a separate report.
When changing extraction, re-ingest into a fresh library before comparing results.
Keep the original synthetic benchmark report as a separate historical baseline.
