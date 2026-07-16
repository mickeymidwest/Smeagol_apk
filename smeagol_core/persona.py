"""
Gives Smeagol a persistent identity that's decoupled from whichever
backend model happens to be answering underneath it.

Talking to "smeagol" always gets the same name, personality, and system
prompt back -- regardless of whether the actual generation came from
your local Dolphin model, Claude, or Gemini. Swap `primary_model` in
config/models.yaml and every interaction with "smeagol" is instantly
backed by a different engine, with zero change to how you talk to it.

It also fails over: if the primary model errors out (API down, local
model crashed, etc.), it automatically tries each fallback in order
before giving up, so Smeagol staying "up" doesn't depend on any single
backend staying up.
"""
from __future__ import annotations
from typing import Optional

from .backends.base import ModelBackend, ModelInfo, GenerationResult


class PersonaBackend(ModelBackend):
    def __init__(
        self,
        info: ModelInfo,
        primary: ModelBackend,
        fallbacks: Optional[list[ModelBackend]] = None,
        system_prompt: str = "",
        consult_model_names: Optional[list[str]] = None,
        last_resort_model_name: Optional[str] = None,
    ):
        super().__init__(info)
        self.primary = primary
        self.fallbacks = fallbacks or []
        self.system_prompt = system_prompt
        # Names (not instances) of models to consult only when this
        # persona's own answer looks uncertain -- see consult.py.
        self.consult_model_names = consult_model_names or []
        # Only ever tried if NOTHING in consult_model_names came back
        # with a confident answer -- a dedicated final check, not just
        # another name in the same list.
        self.last_resort_model_name = last_resort_model_name

    def _combined_system(self, extra: Optional[str]) -> str:
        if self.system_prompt and extra:
            return f"{self.system_prompt}\n\n{extra}"
        return self.system_prompt or extra or ""

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        combined_system = self._combined_system(system)
        candidates = [self.primary] + self.fallbacks
        errors = []

        for backend in candidates:
            result = await backend.generate(
                prompt, system=combined_system, max_tokens=max_tokens, temperature=temperature
            )
            if result.ok:
                # Always answer AS Smeagol -- the caller shouldn't need to
                # know or care which underlying model actually ran.
                return GenerationResult(
                    model=self.info.name,
                    text=result.text,
                    meta={"backed_by": backend.info.name, "failover": backend is not self.primary},
                )
            errors.append(f"{backend.info.name}: {result.error}")

        return GenerationResult(
            model=self.info.name,
            text="",
            error=f"all backends failed -- {'; '.join(errors)}",
        )

    async def warmup(self) -> None:
        # Only warm the primary eagerly; fallbacks load lazily on first
        # actual use so a healthy primary doesn't pay the cost of loading
        # models it may never need.
        await self.primary.warmup()

    async def close(self) -> None:
        # No-op: the registry owns and closes the underlying backends
        # directly, since they're also registered under their own names.
        return None
