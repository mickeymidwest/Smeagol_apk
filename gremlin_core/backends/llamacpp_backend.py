"""
Local backend for any .gguf model (e.g. Dolphin3.0-Llama3.2-3B-q6_k_m.gguf)
via llama-cpp-python. Runs fully offline, no network calls.
"""

from __future__ import annotations
import asyncio
import time
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
        self._last_used: float = 0.0
        self._lock = asyncio.Lock()  # llama.cpp isn't safely reentrant per-instance --
        # also now the one thing serializing load/generate/unload against
        # each other, so unload() can never race a generate() that's
        # mid-flight (see unload() below).
        # Dedicated single-thread pool so this model never waits behind
        # unrelated work competing for the event loop's shared default
        # executor -- each local model gets its own lane.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"llamacpp-{info.name}")

    async def _ensure_loaded(self) -> None:
        """Must be called while holding self._lock. Split out of
        warmup() so generate() can load-and-use atomically under the
        same lock unload() also uses, instead of the previous
        lock-free warmup() (which had a real, if narrow, race: nothing
        stopped unload() from clearing self._llm between warmup()
        returning and the actual inference call reading it)."""
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
        self._last_used = time.monotonic()

    async def warmup(self) -> None:
        async with self._lock:
            await self._ensure_loaded()

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            loop = asyncio.get_event_loop()

            # A blocking llama.cpp call already in flight (load or generate)
            # can't actually be cancelled once it's running in its executor
            # thread -- that's a hard limitation of the underlying C library,
            # not something fixable here. So a caller that gave up waiting
            # (e.g. an HTTP client timeout) leaves this lock held until that
            # call naturally finishes. What we CAN do is stop a *second*
            # request from silently hanging behind it for the same amount of
            # time again -- fail fast with a clear "busy" error instead of
            # queuing indefinitely.
            try:
                await asyncio.wait_for(self._lock.acquire(), timeout=5.0)
            except asyncio.TimeoutError:
                return GenerationResult(
                    model=self.info.name, text="",
                    error=f"{self.info.name} is still busy with a previous request -- try again shortly",
                )

            try:
                await self._ensure_loaded()

                def _run():
                    return self._llm.create_chat_completion(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                result = await loop.run_in_executor(self._executor, _run)
                self._last_used = time.monotonic()
            finally:
                self._lock.release()

            text = result["choices"][0]["message"]["content"]
            return GenerationResult(model=self.info.name, text=text)
        except Exception as e:
            return GenerationResult(model=self.info.name, text="", error=str(e))

    async def unload(self) -> None:
        """Frees this model's VRAM/RAM (drops the loaded llama.cpp
        instance) without shutting down the executor -- the backend
        stays fully usable, the next generate() call just reloads via
        _ensure_loaded(). Called by gremlin_core.eviction's idle sweep
        (see server.py's serve()), never automatically after a single
        use -- an idle timeout, not "unload immediately," so a model
        used twice in quick succession doesn't pay reload cost twice."""
        async with self._lock:
            self._llm = None

    def idle_seconds(self) -> float:
        """0.0 whenever nothing is actually loaded (nothing to evict,
        same as "don't evict" -- the eviction sweep never needs a
        separate is-loaded check), otherwise real elapsed time since
        the last generate()/load."""
        if self._llm is None:
            return 0.0
        return time.monotonic() - self._last_used

    async def close(self) -> None:
        self._llm = None
        self._executor.shutdown(wait=False)
