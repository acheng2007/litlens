from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://litlens:litlens@localhost:55432/litlens"
    redis_url: str = "redis://localhost:56379/0"
    api_tokens: dict[str, str] = {"local-dev-token": "local"}
    data_dir: Path = Path("data")
    llm_provider: Literal["extractive", "openai", "ollama"] = "extractive"
    answer_mode: Literal["quotes", "synthesis"] = "quotes"
    planner_model: str | None = None
    verifier_model: str | None = None
    research_deadline_seconds: float = Field(180, gt=0, le=600)
    model_prices: dict[str, ModelPrice] = {}
    embedding_provider: Literal["hash", "openai", "ollama"] = "hash"
    model_base_url: str = "http://localhost:11434"
    model_api_key: str = ""
    model_name: str = "llama3.2"
    completion_token_parameter: Literal["max_completion_tokens", "max_tokens"] = (
        "max_completion_tokens"
    )
    model_temperature: float | None = None
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = Field(384, ge=1, le=2000)
    max_upload_bytes: int = 30 * 1024 * 1024
    max_pdf_pages: int = 500
    max_pdf_chars: int = 2_000_000
    job_max_attempts: int = 3
    job_lease_seconds: int = Field(900, ge=60)
    retry_seconds: int = 5
    evidence_threshold: float = 0.45

    @property
    def embedding_space(self) -> str:
        model = "sha256-v1" if self.embedding_provider == "hash" else self.embedding_model
        return f"{self.embedding_provider}:{model}:{self.embedding_dimensions}"


settings = Settings()
