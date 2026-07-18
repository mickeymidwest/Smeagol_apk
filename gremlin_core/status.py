from __future__ import annotations
from pathlib import Path

import yaml

from . import model_scan


def _model_summary(e: dict) -> dict:
    """name/type plus whatever's actually editable via `gremlin model-edit`
    (model_scan.EDITABLE_FIELDS) -- lets a client (hologram head-slots,
    desktop/Android settings screens) show and edit a model's current
    values without needing its own copy of the config's field layout."""
    summary = {"name": e["name"], "type": e["type"], "display_name": e.get("display_name", e["name"])}
    if e["type"] == "local_gguf":
        for field in ("chat_format", "n_gpu_layers", "n_ctx"):
            if field in e:
                summary[field] = e[field]
    return summary


def get_status_data(config_path: Path) -> dict:
    """Reads config/models.yaml the same way `gremlin list` does."""
    text = config_path.read_text()
    entries = model_scan.list_all_entries(text)
    cfg = yaml.safe_load(text) or {}
    persona = cfg.get("persona", {})
    return {
        "models": [_model_summary(e) for e in entries],
        "primary_model": persona.get("primary_model"),
        "fallback_models": persona.get("fallback_models", []),
        "consult_models": persona.get("consult_models", []),
        "last_resort_model": persona.get("last_resort_model"),
    }
