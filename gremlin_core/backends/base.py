"""
Base interface every model backend (local GGUF, Anthropic, OpenAI, etc.)
must implement. The router only ever talks to this interface, so it
never needs to know or care whether a given model is running on your
GPU or living behind an API key.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelInfo:
    name: str                 # unique id used in config/routing, e.g. "dolphin-3b"
    kind: str                 # "local" | "api"
    display_name: str = ""
    notes: str = ""


@dataclass
class GenerationResult:
    model: str
    text: str
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


class ModelBackend(ABC):
    """Every backend wraps exactly one model instance."""

    def __init__(self, info: ModelInfo):
        self.info = info

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> GenerationResult:
        """Run one generation. Must never raise -- catch errors internally
        and return a GenerationResult with .error set, so one bad/offline
        model doesn't crash a group call involving several others."""
        raise NotImplementedError

    async def warmup(self) -> None:
        """Optional: load weights / open connection ahead of first use."""
        return None

    async def close(self) -> None:
        """Optional: release resources (unload model, close client)."""
        return None

    async def unload(self) -> None:
        """Optional: drop loaded weights to free VRAM/RAM while keeping
        the backend itself usable -- unlike close(), which ends the
        backend's life for good, this is meant to be followed by
        another generate() call later, which just re-loads via
        warmup(). No-op by default (API backends hold no local
        resources); see LlamaCppBackend for the real implementation."""
        return None

    def idle_seconds(self) -> float:
        """Optional: how long since this backend last actually ran a
        generation, for idle-eviction purposes (see gremlin_core.eviction).
        0 by default -- meaning "never idle, don't evict" -- API backends
        and anything else with nothing worth freeing should leave this
        as-is rather than implementing it."""
        return 0.0
