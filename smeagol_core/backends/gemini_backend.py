from __future__ import annotations
import os
from typing import Optional

from .base import ModelBackend, ModelInfo, GenerationResult

try:
    from google import genai
except ImportError:
    genai = None


class GeminiBackend(ModelBackend):
    def __init__(self, info: ModelInfo, model_id: str, api_key_env: str = "GEMINI_API_KEY"):
        super().__init__(info)
        self.model_id = model_id
        self.api_key_env = api_key_env
        self._client = None

    async def warmup(self) -> None:
        if self._client is not None:
            return
        if genai is None:
            raise RuntimeError("Run: pip install google-genai")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Set {self.api_key_env} in your environment")
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        try:
            await self.warmup()
            config = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                config["system_instruction"] = system

            # google-genai's client is sync-only; run it off the event loop
            # thread so it doesn't block other models running in parallel.
            import asyncio
            loop = asyncio.get_event_loop()

            def _call():
                return self._client.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=config,
                )

            resp = await loop.run_in_executor(None, _call)
            return GenerationResult(model=self.info.name, text=resp.text)
        except Exception as e:
            return GenerationResult(model=self.info.name, text="", error=str(e))
