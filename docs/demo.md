# Demo walkthrough

Start the stack and run `docker compose exec api litlens demo`.
Use the returned library ID with the commands below.

```bash
docker compose exec api litlens ask "What candidates does hybrid retrieval combine?" --library-id LIBRARY_ID
```

The demo returns:

```json
{
  "answer": "Hybrid retrieval combines keyword candidates with vector candidates before selecting evidence. [1]",
  "supported": true,
  "citations": [{
    "marker": "[1]",
    "title": "Fusion Experiment",
    "page": 1,
    "section": "1 Hybrid Retrieval",
    "chunk_id": "a-real-stored-UUID-is-returned-here"
  }]
}
```

This shortened example omits other response fields; the API returns document ID, source description, quotation, usage, policy version, and reason too.

```bash
docker compose exec api litlens ask "What is the mass of Jupiter?" --library-id LIBRARY_ID
```

Expected behavior is the explicit insufficient-evidence response with `supported: false` and `citations: []`.

For a real paper, create a separate library so the evaluation corpus stays unchanged:

```bash
docker compose exec api litlens library "arXiv demo"
docker compose exec api litlens ingest arxiv 1706.03762v7 --library-id ARXIV_LIBRARY_ID --max-results 1 --wait
docker compose exec api litlens ask "What does the Transformer rely on?" --library-id ARXIV_LIBRARY_ID
```

The live verification on September 10, 2026 retrieved arXiv metadata, downloaded the PDF, processed its text, and returned evidence from page 2 of [Attention Is All You Need](https://arxiv.org/abs/1706.03762).
It also exposed split-line numbered headings, which are covered by a regression test in `tests/test_ingestion.py`.
Re-ingest into a new library after chunking changes so stored metadata reflects the new parser.

Provider adapters are additionally checked with controlled HTTP responses for hosted chat, hosted embeddings, Ollama chat, Ollama embeddings, usage accounting, invalid dimensions, authentication failures, and transient retries.
Live hosted or Ollama model generation is not included in the recorded default-provider benchmark.
