from __future__ import annotations
import os
from typing import Optional

from .base import ModelBackend, ModelInfo, GenerationResult

try:
    import anthropic
except ImportError:
    anthropic = None


class AnthropicBackend(ModelBackend):
    def __init__(self, info: ModelInfo, model_id: str, api_key_env: str = "ANTHROPIC_API_KEY"):
        super().__init__(info)
        self.model_id = model_id
        self.api_key_env = api_key_env
        self._client = None

    async def warmup(self) -> None:
        if self._client is not None:
            return
        if anthropic is None:
            raise RuntimeError("Run: pip install anthropic")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Set {self.api_key_env} in your environment")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        try:
            await self.warmup()
            resp = await self._client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in resp.content if block.type == "text")
            return GenerationResult(model=self.info.name, text=text)
        except Exception as e:
            return GenerationResult(model=self.info.name, text="", error=str(e))
