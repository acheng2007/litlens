from dataclasses import dataclass
from typing import Protocol


@dataclass
class Generation:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    def generate(
        self, prompt: str, *, system: str | None = None, model: str | None = None
    ) -> Generation: ...
