from .base import Generation

class LocalProvider:
    def generate(self, prompt: str) -> Generation:
        return Generation("I do not have enough evidence in the selected corpus to answer that question.")

