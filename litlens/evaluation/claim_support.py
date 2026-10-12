"""Independent fixture labels for evaluating the fallible semantic verifier."""

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from litlens.config import settings
from litlens.db.models import Evaluation, Library, RequestSummary
from litlens.generation.answer import Synthesis, normalize
from litlens.generation.research import PROMPTS, VERIFIER_SYSTEM, Verdict, validate_verdict
from litlens.providers.remote import RemoteProvider
from litlens.providers.tracking import Accounting, DeadlineExceeded, active_request


def load_challenge():
    data = json.loads(Path(__file__).with_name("claim_support.json").read_text())
    ids = set()
    for case in data["cases"]:
        if case["id"] in ids or case["label"] not in {"supported", "unsupported", "contradicted"}:
            raise ValueError("Invalid challenge label or ID")
        ids.add(case["id"])
        if normalize(case["quote"]) not in normalize(case["passage"]):
            raise ValueError("Challenge quotation lacks provenance")
        Synthesis.model_validate(
            {
                "claims": [
                    {"text": case["claim"], "evidence": [{"source": 1, "quote": case["quote"]}]}
                ]
            }
        )
    return data


def evaluate_verifier(session, library_id, owner_id):
    if not session.scalar(
        select(Library).where(Library.id == library_id, Library.owner_id == owner_id)
    ):
        raise PermissionError("Library not found")
    if settings.llm_provider == "extractive":
        raise ValueError("Verifier evaluation requires LLM_PROVIDER=ollama or openai")
    challenge = load_challenge()
    cases = []
    for case in challenge["cases"]:
        started = time.monotonic()
        accounting = Accounting(
            started + settings.research_deadline_seconds,
            role="verifier",
            prompt_version=PROMPTS["verifier"],
        )
        accounting.logical_calls = 1
        token = active_request.set(accounting)
        prediction, reason = None, "invalid_provider_output"
        payload = {
            "claims": [
                {
                    "claim_id": "c1",
                    "text": case["claim"],
                    "evidence": [{"source": 1, "quote": case["quote"], "passage": case["passage"]}],
                }
            ]
        }
        try:
            output = RemoteProvider().generate(
                json.dumps(payload), system=VERIFIER_SYSTEM, model=settings.verifier_model
            )
            verdict = Verdict.model_validate_json(output.text)
            draft = Synthesis.model_validate(
                {
                    "claims": [
                        {"text": case["claim"], "evidence": [{"source": 1, "quote": case["quote"]}]}
                    ]
                }
            )
            validate_verdict(verdict, draft)
            accounting.remaining()
            prediction, reason = verdict.results[0].status, "classified"
        except DeadlineExceeded:
            reason = "deadline_exhausted"
        except httpx.HTTPError:
            reason = "upstream_error"
        except (ValueError, KeyError):
            pass
        finally:
            active_request.reset(token)
        request_id = str(uuid4())
        summary = {
            **accounting.summary(),
            "reason": reason,
            "workflow": "verifier-challenge",
            "workflow_version": challenge["version"],
            "stage_timings_ms": {"verify": (time.monotonic() - started) * 1000},
            "prompt_versions": {"verifier": PROMPTS["verifier"]},
        }
        session.add(
            RequestSummary(id=request_id, library_id=library_id, owner_id=owner_id, summary=summary)
        )
        cases.append(
            {
                "id": case["id"],
                "category": case["category"],
                "label": case["label"],
                "prediction": prediction,
                "correct": prediction == case["label"],
                "request_id": request_id,
                **summary,
            }
        )
    report = {
        "dataset": "claim-support",
        "dataset_version": challenge["version"],
        "annotation": challenge["annotation"],
        "dataset_sha256": hashlib.sha256(
            json.dumps(challenge, sort_keys=True).encode()
        ).hexdigest(),
        "provider": settings.llm_provider,
        "model": settings.verifier_model or settings.model_name,
        "prompt_version": PROMPTS["verifier"],
        "questions": len(cases),
        "accuracy": sum(c["correct"] for c in cases) / len(cases),
        "invalid_or_failed_rate": sum(c["prediction"] is None for c in cases) / len(cases),
        "confusion_matrix": {
            label: {
                prediction: sum(
                    c["label"] == label and c["prediction"] == prediction for c in cases
                )
                for prediction in ["supported", "unsupported", "contradicted", None]
            }
            for label in ["supported", "unsupported", "contradicted"]
        },
        "pricing": {k: v.model_dump() for k, v in settings.model_prices.items()},
        "bounds": {
            "deadline_seconds": settings.research_deadline_seconds,
            "logical_model_calls": 1,
        },
        "cases": cases,
    }
    evaluation = Evaluation(library_id=library_id, metrics=report)
    session.add(evaluation)
    session.commit()
    return evaluation
