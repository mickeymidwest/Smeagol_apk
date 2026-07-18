"""
Local backend for any .gguf model (e.g. Dolphin3.0-Llama3.2-3B-q6_k_m.gguf)
via llama-cpp-python. Runs fully offline, no network calls.
"""

from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .base import ModelBackend, ModelInfo, GenerationResult

try:
    from llama_cpp import Llama
except ImportError:  # library not installed yet
    Llama = None


class LlamaCppBackend(ModelBackend):
    def __init__(
        self,
        info: ModelInfo,
        model_path: str,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,   # -1 = offload as much as possible to GPU
        n_threads: Optional[int] = None,
        chat_format: Optional[str] = "chatml",  # dolphin models use chatml
    ):
        super().__init__(info)
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.chat_format = chat_format
        self._llm: Optional["Llama"] = None
        self._lock = asyncio.Lock()  # llama.cpp isn't safely reentrant per-instance
        # Dedicated single-thread pool so this model never waits behind
        # unrelated work competing for the event loop's shared default
        # executor -- each local model gets its own lane.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"llamacpp-{info.name}")

    async def warmup(self) -> None:
        if self._llm is not None:
            return
        if Llama is None:
            raise RuntimeError(
                "llama-cpp-python is not installed. Run: "
                "pip install llama-cpp-python (or the CUDA/Metal build for GPU accel)"
            )
        loop = asyncio.get_event_loop()

        def _load():
            return Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                n_threads=self.n_threads,
                chat_format=self.chat_format,
                verbose=False,
            )

        self._llm = await loop.run_in_executor(self._executor, _load)

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

            loop = asyncio.get_event_loop()

            async with self._lock:
                def _run():
                    return self._llm.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                result = await loop.run_in_executor(self._executor, _run)

            text = result["choices"][0]["message"]["content"]
            return GenerationResult(model=self.info.name, text=text)
        except Exception as e:
            return GenerationResult(model=self.info.name, text="", error=str(e))

    async def close(self) -> None:
        self._llm = None
        self._executor.shutdown(wait=False)
