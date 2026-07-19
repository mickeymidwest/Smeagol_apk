"""
Periodically unloads local GGUF models that have sat idle for a while,
so VRAM doesn't just accumulate forever across a `gremlin serve`
process's lifetime. Without this, `LlamaCppBackend.warmup()` loads a
model once and it stays resident until the process exits -- on a
VRAM-constrained card, chatting with a few different consult models
over the course of a day would eventually load all of them
simultaneously with nothing ever giving memory back.

Deliberately an idle timeout, not "unload right after every single
use" -- a model used twice in a row (e.g. two questions back to back)
shouldn't pay reload cost twice. Never touches the persona's primary
model (see registry.primary_model_name()) -- that one's supposed to
stay resident so Gremlin's baseline chat is always instant; only
consult/fallback/last-resort local models are ever idle-evicted.

This does NOT make an individually-oversized model (e.g. one whose
smallest available quant is already bigger than your VRAM) fit --
that's a model_path/n_gpu_layers choice, not something a load/unload
policy can fix. See README's "Confirmed model sources" section.
"""
from __future__ import annotations
import asyncio

from .registry import ModelRegistry

DEFAULT_IDLE_SECONDS = 90.0
DEFAULT_SWEEP_INTERVAL = 30.0


async def evict_idle_models(
    registry: ModelRegistry,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    sweep_interval: float = DEFAULT_SWEEP_INTERVAL,
) -> None:
    """Runs forever (intended to be scheduled once on gremlin serve's
    background event loop, see server.py's serve()). Each sweep is
    best-effort -- an error unloading one backend is logged to stdout
    and never stops the sweep loop or affects any other backend."""
    primary_name = registry.primary_model_name()

    while True:
        await asyncio.sleep(sweep_interval)
        for name, backend in registry.backends.items():
            if name == primary_name:
                continue
            try:
                if backend.idle_seconds() > idle_seconds:
                    await backend.unload()
            except Exception as e:
                print(f"[eviction] couldn't unload '{name}': {e}")
