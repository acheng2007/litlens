import hashlib
import json
import platform
import statistics
import time

import pymupdf
from sqlalchemy import select

from litlens.config import settings
from litlens.db.models import Chunk, Document, Evaluation, Job, Library
from litlens.generation.answer import PROMPT_VERSION, answer, normalize
from litlens.ingestion.chunking import CHUNKING_VERSION
from litlens.ingestion.pdf import EXTRACTION_VERSION
from litlens.retrieval.search import search

from .corpus import load_corpus
from .datasets import evidence_text, group_recall, load_real_corpus, resolve_real_questions


def percentile(values, fraction):
    return sorted(values)[min(int((len(values) - 1) * fraction), len(values) - 1)] if values else 0


def evaluate(session, library_id, owner_id, dataset="synthetic"):
    library = session.get(Library, library_id)
    if not library or library.owner_id != owner_id:
        raise ValueError("Library not available")
    if dataset not in {"synthetic", "real-papers"}:
        raise ValueError("Unknown evaluation dataset")
    corpus = load_corpus()
    rows = session.execute(
        select(Chunk, Document)
        .join(Document)
        .where(Document.library_id == library_id, Document.status == "ready")
    ).all()
    questions = []
    for document in corpus["documents"] if dataset == "synthetic" else []:
        for page, fact in enumerate(document["facts"], 1):
            gold = {
                c.id
                for c, d in rows
                if d.title == document["title"]
                and c.page == page
                and normalize(fact["text"]) in normalize(c.text)
            }
            if not gold:
                raise ValueError("Seed the complete evaluation corpus with litlens demo first")
            questions.append(
                {
                    "query": fact["question"],
                    "gold": gold,
                    "text": fact["text"],
                    "gold_groups": [gold],
                    "category": "synthetic",
                }
            )
    if dataset == "synthetic":
        questions.extend(
            {"query": q, "gold": set(), "gold_groups": [], "category": "unsupported"}
            for q in corpus["unsupported_questions"]
        )
    else:
        real = load_real_corpus()
        documents = session.scalars(select(Document).where(Document.library_id == library_id)).all()
        questions = resolve_real_questions(real, rows, documents)
        corpus = real.model_dump()
    answerable_count = sum(bool(q["gold"]) for q in questions)
    refusal_count = len(questions) - answerable_count
    metrics = {
        "dataset": dataset,
        "dataset_version": corpus["version"],
        "questions": len(questions),
        "answerable_questions": answerable_count,
        "refusal_questions": refusal_count,
        "corpus_sha256": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(),
        "embedding_space": settings.embedding_space,
        "provider": settings.llm_provider,
        "model": settings.model_name if settings.llm_provider != "extractive" else None,
        "prompt_version": PROMPT_VERSION,
        "chunking_version": CHUNKING_VERSION,
        "extraction_version": EXTRACTION_VERSION,
        "python": platform.python_version(),
        "pymupdf": pymupdf.VersionBind,
        "evidence_threshold": settings.evidence_threshold,
        "retrieval_limit": 5,
        "modes": {},
    }
    if dataset == "real-papers":
        metrics["annotation"] = corpus["annotation"]
        metrics["sources"] = corpus["documents"]
        metrics["gold_resolution"] = {
            label: [
                {
                    "chunk_id": chunk.id,
                    "document_id": document.id,
                    "page": chunk.page,
                    "stored_section": chunk.section,
                    "text_sha256": hashlib.sha256(chunk.text.encode()).hexdigest(),
                }
                for chunk, document in rows
                if chunk.id in group
            ]
            for question in questions
            for label, group in zip(question["evidence"], question["gold_groups"])
        }
    for mode in ["vector", "keyword", "hybrid"]:
        recalls, reranked_recalls, reciprocal_ranks, latencies = [], [], [], []
        citation_valid, citation_count, faithful, claims_count = 0, 0, 0, 0
        unsupported_answers, false_refusals, input_tokens, output_tokens = 0, 0, 0, 0
        cases = []
        for question in questions:
            started = time.perf_counter()
            raw = search(session, library_id, question["query"], 5, mode, owner_id, rerank=False)
            hits = search(session, library_id, question["query"], 5, mode, owner_id, rerank=True)
            response = answer(question["query"], hits)
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            if question["gold"]:
                recalls.append(group_recall(question["gold_groups"], [h.chunk.id for h in raw]))
                reranked_recalls.append(
                    group_recall(question["gold_groups"], [h.chunk.id for h in hits])
                )
                reciprocal_ranks.append(
                    next((1 / i for i, h in enumerate(raw, 1) if h.chunk.id in question["gold"]), 0)
                )
                false_refusals += not response["supported"]
            else:
                unsupported_answers += response["supported"]
            for citation in response["citations"]:
                citation_count += 1
                chunk = session.get(Chunk, citation["chunk_id"])
                doc = session.get(Document, chunk.document_id) if chunk else None
                valid = bool(
                    chunk
                    and doc
                    and doc.library_id == library_id
                    and doc.id == citation["document_id"]
                    and doc.title == citation["title"]
                    and chunk.page == citation["page"]
                    and chunk.section == citation["section"]
                )
                citation_valid += valid
                claims_count += 1
                faithful += bool(valid and normalize(citation["quote"]) in normalize(chunk.text))
            input_tokens += response["usage"]["input_tokens"]
            output_tokens += response["usage"]["output_tokens"]
            cases.append(
                {
                    "id": question.get("id"),
                    "category": question["category"],
                    "query": question["query"],
                    "expected_supported": bool(question["gold"]),
                    "supported": response["supported"],
                    "reason": response["reason"],
                    "gold_chunk_ids": sorted(question["gold"]),
                    "retrieved_chunk_ids": [h.chunk.id for h in raw],
                    "reranked_chunk_ids": [h.chunk.id for h in hits],
                    "citation_chunk_ids": [c["chunk_id"] for c in response["citations"]],
                    "gold_evidence_ids": question.get("evidence", []),
                    "reference_answer": question.get("reference_answer"),
                    "evidence_recall_at_5": group_recall(
                        question["gold_groups"], [h.chunk.id for h in raw]
                    ),
                    "reranked_evidence_recall_at_5": group_recall(
                        question["gold_groups"], [h.chunk.id for h in hits]
                    ),
                    "citation_evidence_recall": group_recall(
                        question["gold_groups"], [c["chunk_id"] for c in response["citations"]]
                    ),
                    # Conservative proxy, not an entailment or semantic correctness score.
                    "quoted_anchor_coverage": (
                        sum(
                            any(
                                evidence_text(a) in evidence_text(c["quote"])
                                for c in response["citations"]
                            )
                            for a in question["anchors"]
                        )
                        / len(question["anchors"])
                        if question.get("anchors")
                        else None
                    ),
                    "latency_ms": round(elapsed, 2),
                }
            )
        metrics["modes"][mode] = {
            "recall_at_5": statistics.mean(recalls),
            "reranked_recall_at_5": statistics.mean(reranked_recalls),
            "mrr_at_5": statistics.mean(reciprocal_ranks),
            "citation_correctness": citation_valid / citation_count if citation_count else None,
            "quote_faithfulness": faithful / claims_count if claims_count else None,
            "citation_count": citation_count,
            "unsupported_answer_rate": unsupported_answers / refusal_count,
            "false_refusal_rate": false_refusals / answerable_count,
            "latency_p50_ms": percentile(latencies, 0.5),
            "latency_p95_ms": percentile(latencies, 0.95),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cases": cases,
            "by_category": {
                category: {
                    "questions": len(selected := [c for c in cases if c["category"] == category]),
                    "support_decision_accuracy": statistics.mean(
                        c["supported"] == c["expected_supported"] for c in selected
                    ),
                    "recall_at_5": statistics.mean(values)
                    if (
                        values := [
                            c["evidence_recall_at_5"] for c in selected if c["expected_supported"]
                        ]
                    )
                    else None,
                }
                for category in sorted({q["category"] for q in questions})
            },
        }
    jobs = session.scalars(select(Job).where(Job.library_id == library_id)).all()
    completed = [j for j in jobs if j.status == "completed" and j.kind == "pdf_ingestion"]
    seconds = sum(j.duration_ms or 0 for j in completed) / 1000
    metrics["ingestion"] = {
        "completed_documents": len(completed),
        "chunks_per_processing_second": sum(j.result.get("chunks", 0) for j in completed) / seconds
        if seconds
        else None,
        "retry_rate": sum(j.attempts > 1 for j in jobs) / len(jobs) if jobs else None,
        "retry_attempts": sum(max(j.attempts - 1, 0) for j in jobs),
        "failed_jobs": sum(j.status == "failed" for j in jobs),
    }
    evaluation = Evaluation(library_id=library_id, metrics=metrics)
    session.add(evaluation)
    session.commit()
    return evaluation
