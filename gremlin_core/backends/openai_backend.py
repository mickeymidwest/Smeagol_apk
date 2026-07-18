from __future__ import annotations
import os
from typing import Optional

from .base import ModelBackend, ModelInfo, GenerationResult

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


class OpenAIBackend(ModelBackend):
    """
    Works for real OpenAI models, and for anything that speaks the
    OpenAI-compatible API -- which includes Ollama (base_url
    http://localhost:11434/v1) and many local inference servers.
    This is the easy path if you'd rather manage local models through
    Ollama instead of raw llama-cpp-python.
    """

    def __init__(
        self,
        info: ModelInfo,
        model_id: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: Optional[str] = None,
    ):
        super().__init__(info)
        self.model_id = model_id
        self.api_key_env = api_key_env
        self.base_url = base_url
        self._client = None

    async def warmup(self) -> None:
        if self._client is not None:
            return
        if AsyncOpenAI is None:
            raise RuntimeError("Run: pip install openai")
        api_key = os.environ.get(self.api_key_env, "not-needed-for-local")
        self._client = AsyncOpenAI(api_key=api_key, base_url=self.base_url)

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        try:
            await self.warmup()
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            resp = await self._client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = resp.choices[0].message.content
            return GenerationResult(model=self.info.name, text=text)
        except Exception as e:
            return GenerationResult(model=self.info.name, text="", error=str(e))
