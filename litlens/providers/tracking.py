"""Request-local budgets and accounting. Never capture prompts or document text."""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from litlens.config import settings

active_request = ContextVar("provider_request", default=None)


class DeadlineExceeded(TimeoutError):
    pass


@dataclass
class Accounting:
    deadline: float | None
    calls: list[dict] = field(default_factory=list)
    logical_calls: int = 0
    role: str = "synthesis"
    prompt_version: str = ""

    def remaining(self):
        if self.deadline is None:
            return 120
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded("Request deadline exhausted")
        return remaining

    def record(self, model, provider, input_tokens, output_tokens, attempt, outcome):
        input_tokens = input_tokens if type(input_tokens) is int and input_tokens >= 0 else None
        output_tokens = output_tokens if type(output_tokens) is int and output_tokens >= 0 else None
        self.calls.append(
            {
                "role": self.role,
                "prompt_version": self.prompt_version,
                "model": model,
                "provider": provider,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "attempt": attempt,
                "outcome": outcome,
            }
        )

    def summary(self):
        usage = {
            key: sum(c[key] for c in self.calls)
            if all(c[key] is not None for c in self.calls)
            else None
            for key in ("input_tokens", "output_tokens")
        }
        costs = []
        for call in self.calls:
            price = settings.model_prices.get(call["model"])
            if price is None or call["input_tokens"] is None or call["output_tokens"] is None:
                costs.append(None)
            else:
                costs.append(
                    (call["input_tokens"] * price.input + call["output_tokens"] * price.output)
                    / 1_000_000
                )
        complete = bool(costs) and all(c is not None for c in costs)
        return {
            "usage": {**usage, "available": all(v is not None for v in usage.values())},
            "estimated_cost_usd": sum(costs) if complete else None,
            "cost_complete": complete,
            "retry_count": sum(c["attempt"] > 1 for c in self.calls),
            "logical_model_calls": self.logical_calls,
            "provider_attempts": self.calls,
        }
