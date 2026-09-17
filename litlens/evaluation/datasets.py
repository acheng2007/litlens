"""Versioned real-paper labels, with immutable PDF identities and evidence groups."""

import hashlib
import json
import re
import unicodedata
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DatasetName = Literal["synthetic", "real-papers"]


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Paper(Label):
    id: str
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}v\d+$")
    title: str
    pdf_url: str
    source_url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pages: int = Field(gt=0)

    @model_validator(mode="after")
    def pinned_urls(self):
        if self.pdf_url != f"https://arxiv.org/pdf/{self.arxiv_id}":
            raise ValueError("PDF URL must match the pinned arXiv revision")
        if self.source_url != f"https://arxiv.org/abs/{self.arxiv_id}":
            raise ValueError("Source URL must match the pinned arXiv revision")
        return self


class Evidence(Label):
    id: str
    document: str
    page: int = Field(gt=0)
    anchor: str = Field(min_length=3)
    section: str


class Question(Label):
    id: str
    category: Literal[
        "paraphrase", "qualification", "conflicting_findings", "ambiguity", "unsupported"
    ]
    query: str = Field(min_length=1)
    expected_supported: bool
    evidence: list[str]
    reference_answer: str = Field(min_length=1)


class RealCorpus(Label):
    id: Literal["real-papers"]
    version: str
    annotation: dict
    documents: list[Paper]
    evidence: list[Evidence]
    questions: list[Question]

    @model_validator(mode="after")
    def valid_references(self):
        for collection in (self.documents, self.evidence, self.questions):
            if len({x.id for x in collection}) != len(collection):
                raise ValueError("Duplicate dataset IDs")
        papers = {d.id: d for d in self.documents}
        labels = {e.id for e in self.evidence}
        for evidence in self.evidence:
            if evidence.document not in papers or evidence.page > papers[evidence.document].pages:
                raise ValueError("Evidence must reference a valid paper page")
        for question in self.questions:
            if not set(question.evidence) <= labels:
                raise ValueError("Unknown evidence label")
            if question.expected_supported != bool(question.evidence):
                raise ValueError(
                    "Supported questions require evidence; refusals must not have gold"
                )
        if not any(q.expected_supported for q in self.questions) or all(
            q.expected_supported for q in self.questions
        ):
            raise ValueError("Dataset must contain both answerable and refusal cases")
        return self


def load_real_corpus():
    return RealCorpus.model_validate_json(
        files("litlens.evaluation").joinpath("real_papers.json").read_text()
    )


def fingerprint(corpus):
    return hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()


def evidence_text(text):
    # Normalize typography for gold lookup only; generation retains its strict quote policy.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def resolve_real_questions(corpus, rows, documents):
    expected = {d.sha256 for d in corpus.documents}
    if {d.source_key for d in documents} != expected or len(documents) != len(expected):
        raise ValueError("Use a separate library containing exactly the pinned real-paper PDFs")
    if any(d.status != "ready" for d in documents):
        raise ValueError("Wait for every real-paper ingestion job to complete")
    papers = {d.id: d for d in corpus.documents}
    gold = {}
    for evidence in corpus.evidence:
        paper = papers[evidence.document]
        gold[evidence.id] = {
            chunk.id
            for chunk, document in rows
            if document.source_key == paper.sha256
            and chunk.page == evidence.page
            and evidence_text(evidence.anchor) in evidence_text(chunk.text)
        }
        if not gold[evidence.id]:
            raise ValueError(
                f"Missing gold evidence {evidence.id}; check ingestion and PDF revision"
            )
    return [
        {
            **q.model_dump(),
            "gold": set().union(*(gold[e] for e in q.evidence)),
            "gold_groups": [gold[e] for e in q.evidence],
            "anchors": [
                next(e.anchor for e in corpus.evidence if e.id == key) for key in q.evidence
            ],
        }
        for q in corpus.questions
    ]


def group_recall(groups, ids):
    """One hit per evidence label suffices, including overlapping alternative chunks."""
    return sum(bool(group & set(ids)) for group in groups) / len(groups) if groups else None
