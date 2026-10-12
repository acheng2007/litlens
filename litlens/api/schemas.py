from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LibraryCreate(Input):
    name: str = Field(min_length=1, max_length=200)


class LibraryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    owner_id: str
    embedding_space: str


class SearchRequest(Input):
    library_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=50)
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"


class AnswerRequest(SearchRequest):
    workflow: Literal["direct", "research"] = "direct"
    limit: int = Field(default=5, ge=1, le=10)


class ArxivRequest(Input):
    library_id: str
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=20)


class VerifierEvaluationRequest(Input):
    library_id: str = Field(min_length=1, max_length=36)


class EvaluationRequest(Input):
    workflow: Literal["direct", "research"] = "direct"
    library_id: str
    dataset: Literal["synthetic", "real-papers"] = "synthetic"


class Enqueued(BaseModel):
    job_id: str
    document_id: str | None = None
    status: str


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_id: str
    title: str
    source: str
    status: str
    arxiv_id: str | None


class DocumentDetail(DocumentView):
    chunks: int
    latest_job_id: str | None


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_id: str
    document_id: str | None
    kind: str
    status: str
    error: str | None
    attempts: int
    result: dict
    duration_ms: float | None
    created_at: datetime
    completed_at: datetime | None


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    text: str
    page: int
    section: str | None
    score: float
    retrieval_score: float
    citation: str


class SearchResponse(BaseModel):
    results: list[SearchHit]


class Citation(BaseModel):
    marker: str
    source: str
    title: str
    document_id: str
    chunk_id: str
    page: int
    section: str | None
    quote: str


class Usage(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    available: bool | None = None


class AnswerResponse(BaseModel):
    workflow: Literal["direct", "research"] = "direct"
    workflow_version: str | None = None
    research_plan: dict | None = None
    evidence_gaps: list[str] = []
    claim_verification: list[dict] = []
    verification_history: list[dict] = []
    request_id: str | None = None
    stage_timings_ms: dict[str, float] = {}
    estimated_cost_usd: float | None = None
    cost_complete: bool = False
    retry_count: int = 0
    logical_model_calls: int = 0
    provider_attempts: list[dict] = []
    first_round_evidence: list[str] = []
    final_evidence: list[str] = []
    evidence_rounds: int = 0
    answer: str
    supported: bool
    citations: list[Citation]
    usage: Usage
    reason: str
    prompt_version: str


class EvaluationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_id: str
    created_at: datetime
    metrics: dict


class Health(BaseModel):
    status: str


class RequestDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    library_id: str
    created_at: datetime
    summary: dict
