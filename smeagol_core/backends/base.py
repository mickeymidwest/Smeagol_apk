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
