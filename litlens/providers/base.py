from dataclasses import dataclass
from typing import Protocol

@dataclass
class Generation:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0

class LLMProvider(Protocol):
    def generate(self, prompt: str) -> Generation: ...

