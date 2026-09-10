import httpx
from .base import Generation

class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str): self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
    def generate(self, prompt: str) -> Generation:
        response = httpx.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json={"model": self.model, "messages": [{"role": "user", "content": prompt}]}, timeout=120)
        response.raise_for_status(); data = response.json(); usage = data.get("usage", {})
        return Generation(data["choices"][0]["message"]["content"], usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

