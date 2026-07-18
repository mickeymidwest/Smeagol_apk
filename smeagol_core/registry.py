from __future__ import annotations
import yaml
from typing import Dict

from .backends.base import ModelBackend, ModelInfo
from .backends.llamacpp_backend import LlamaCppBackend
from .backends.anthropic_backend import AnthropicBackend
from .backends.openai_backend import OpenAIBackend
from .backends.gemini_backend import GeminiBackend
from .persona import PersonaBackend


class ModelRegistry:
    """
    Reads config/models.yaml and instantiates every backend once.
    Add a new model = add a block to the YAML. No code changes needed
    for another local GGUF file or another API model.
    """

    def __init__(self):
        self.backends: Dict[str, ModelBackend] = {}

    @classmethod
    def from_yaml(cls, path: str) -> "ModelRegistry":
        reg = cls()
        with open(path, "r") as f:
            cfg = yaml.safe_load(f) or {}

        for entry in cfg.get("models", []):
            name = entry["name"]
            info = ModelInfo(
                name=name,
                kind=entry["type"],
                display_name=entry.get("display_name", name),
                notes=entry.get("notes", ""),
            )

            if entry["type"] == "local_gguf":
                backend = LlamaCppBackend(
                    info,
                    model_path=entry["model_path"],
                    n_ctx=entry.get("n_ctx", 4096),
                    n_gpu_layers=entry.get("n_gpu_layers", -1),
                    chat_format=entry.get("chat_format", "chatml"),
                )
            elif entry["type"] == "anthropic":
                backend = AnthropicBackend(
                    info,
                    model_id=entry["model_id"],
                    api_key_env=entry.get("api_key_env", "ANTHROPIC_API_KEY"),
                )
            elif entry["type"] == "openai_compatible":
                backend = OpenAIBackend(
                    info,
                    model_id=entry["model_id"],
                    api_key_env=entry.get("api_key_env", "OPENAI_API_KEY"),
                    base_url=entry.get("base_url"),
                )
            elif entry["type"] == "gemini":
                backend = GeminiBackend(
                    info,
                    model_id=entry["model_id"],
                    api_key_env=entry.get("api_key_env", "GEMINI_API_KEY"),
                )
            else:
                raise ValueError(f"Unknown model type: {entry['type']}")

            reg.backends[name] = backend

        persona_cfg = cfg.get("persona")
        if persona_cfg:
            persona_name = persona_cfg.get("name", "smeagol").lower()
            primary_name = persona_cfg["primary_model"]
            fallback_names = persona_cfg.get("fallback_models", [])
            last_resort_name = persona_cfg.get("last_resort_model")

            primary = reg.get(primary_name)
            fallbacks = [reg.get(n) for n in fallback_names]
            if last_resort_name:
                reg.get(last_resort_name)  # validate it exists now, fail fast on a config typo

            persona_info = ModelInfo(
                name=persona_name,
                kind="persona",
                display_name=persona_cfg.get("display_name", persona_name.capitalize()),
                notes=f"backed by {primary_name}" + (f", falls back to {fallback_names}" if fallback_names else ""),
            )
            reg.backends[persona_name] = PersonaBackend(
                persona_info,
                primary=primary,
                fallbacks=fallbacks,
                system_prompt=persona_cfg.get("system_prompt", ""),
                consult_model_names=persona_cfg.get("consult_models", []),
                last_resort_model_name=last_resort_name,
            )

        return reg

    def get(self, name: str) -> ModelBackend:
        if name not in self.backends:
            raise KeyError(f"No model registered as '{name}'. Check config/models.yaml")
        return self.backends[name]

    def names(self) -> list[str]:
        return list(self.backends.keys())

    def consult_models(self) -> list[str]:
        """The persona's curated local consult models (config: persona.consult_models),
        regardless of what the persona itself is named."""
        for b in self.backends.values():
            if b.info.kind == "persona":
                return list(getattr(b, "consult_model_names", []))
        return []

    async def close_all(self):
        for b in self.backends.values():
            await b.close()
