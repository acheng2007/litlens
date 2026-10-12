"""Authenticated workflow execution and body-free durable request summaries."""

import time
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from litlens.config import settings
from litlens.db.models import Library, RequestSummary
from litlens.generation.answer import REFUSAL, answer
from litlens.generation.research import PROMPTS, WORKFLOW_VERSION, Research
from litlens.observability import event
from litlens.providers.tracking import Accounting, DeadlineExceeded, active_request
from litlens.retrieval.search import search


def execute(
    session,
    library_id,
    owner_id,
    question,
    workflow="direct",
    mode="hybrid",
    limit=5,
    request_id=None,
):
    library = session.scalar(
        select(Library).where(Library.id == library_id, Library.owner_id == owner_id)
    )
    if not library:
        raise PermissionError("Library not found")
    if library.embedding_space != settings.embedding_space:
        raise ValueError(
            "Embedding configuration changed; use the original settings or a new library"
        )
    if workflow not in {"direct", "research"}:
        raise ValueError("Unknown workflow")
    if workflow == "research" and settings.llm_provider == "extractive":
        raise ValueError("Research requires LLM_PROVIDER=ollama or openai")
    started = time.monotonic()
    accounting = Accounting(
        started + settings.research_deadline_seconds if workflow == "research" else None
    )
    token = active_request.set(accounting)
    request_id = request_id or str(uuid4())
    research = Research(session, library_id, owner_id, mode, accounting)
    result = {
        "answer": REFUSAL,
        "supported": False,
        "citations": [],
        "reason": "insufficient_evidence",
        "prompt_version": PROMPTS["synthesis"]
        if workflow == "research"
        else (
            "evidence-synthesis-v1" if settings.answer_mode == "synthesis" else "evidence-quotes-v1"
        ),
    }
    timings = {}
    try:
        if workflow == "research":
            state = research.graph().invoke(
                {"question": question, "round": 0, "gaps": []}, {"recursion_limit": 24}
            )
            result = research.result(state)
        else:
            remaining = accounting.remaining()
            session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": str(int(remaining * 1000))},
            )
            try:
                hits = search(session, library_id, question, limit, mode, owner_id)
            finally:
                timings["retrieve"] = (time.monotonic() - started) * 1000
            accounting.remaining()
            accounting.role = "synthesis" if settings.answer_mode == "synthesis" else "quotation"
            accounting.prompt_version = (
                "evidence-synthesis-v1"
                if settings.answer_mode == "synthesis"
                else "evidence-quotes-v1"
            )
            generation_started = time.monotonic()
            try:
                result = answer(question, hits)
                accounting.remaining()
            finally:
                timings["generation"] = (time.monotonic() - generation_started) * 1000
            result.update(
                final_evidence=[h.chunk.id for h in hits[:5]],
                first_round_evidence=[h.chunk.id for h in hits[:5]],
            )
    except (
        DeadlineExceeded,
        httpx.HTTPError,
        ValidationError,
        ValueError,
        KeyError,
        DBAPIError,
    ) as exc:
        if workflow == "research":
            result = research.result(research.last_state)
        result.update(answer=REFUSAL, citations=[], supported=False)
        if isinstance(exc, DeadlineExceeded):
            reason = "deadline_exhausted"
        elif isinstance(exc, (httpx.HTTPError, DBAPIError)):
            reason = "upstream_error"
            if isinstance(exc, DBAPIError):
                reason = (
                    "deadline_exhausted"
                    if getattr(exc.orig, "sqlstate", None) == "57014"
                    else reason
                )
                session.rollback()
        else:
            reason = "invalid_provider_output"
        result.update(
            reason=reason,
            evidence_gaps=[
                {
                    "deadline_exhausted": "The request exhausted its total deadline.",
                    "upstream_error": "A required upstream service failed.",
                    "invalid_provider_output": "A model returned invalid structured output.",
                }[reason]
            ],
        )
        event("workflow.failure", workflow=workflow, reason=reason, error_type=type(exc).__name__)
    finally:
        active_request.reset(token)
    timings = result.get("stage_timings_ms", timings)
    timings["total"] = (time.monotonic() - started) * 1000
    result.update(
        accounting.summary(),
        request_id=request_id,
        workflow=workflow,
        workflow_version=WORKFLOW_VERSION if workflow == "research" else "direct-v1",
        stage_timings_ms=timings,
    )
    # No question, answer, quotes, document bodies, or prompts in routine persistence.
    summary = {
        key: result[key]
        for key in (
            "workflow",
            "workflow_version",
            "reason",
            "supported",
            "stage_timings_ms",
            "usage",
            "estimated_cost_usd",
            "cost_complete",
            "retry_count",
            "logical_model_calls",
            "provider_attempts",
        )
    }
    summary["evidence_rounds"] = result.get("evidence_rounds", 1)
    summary.update(
        models={
            "provider": settings.llm_provider,
            "synthesis": settings.model_name,
            "planner": settings.planner_model or settings.model_name,
            "verifier": settings.verifier_model or settings.model_name,
        },
        prompt_versions=PROMPTS if workflow == "research" else {"answer": result["prompt_version"]},
        bounds={
            "evidence_rounds": 2 if workflow == "research" else 1,
            "logical_model_calls": 6 if workflow == "research" else 1,
            "deadline_seconds": settings.research_deadline_seconds
            if workflow == "research"
            else None,
        },
        pricing={k: v.model_dump() for k, v in settings.model_prices.items()},
    )
    session.execute(text("SELECT set_config('statement_timeout', '0', true)"))
    session.add(
        RequestSummary(id=request_id, library_id=library_id, owner_id=owner_id, summary=summary)
    )
    session.commit()
    event("workflow.complete", request_id=request_id, **summary)
    return result
