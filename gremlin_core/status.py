from __future__ import annotations
from pathlib import Path

import yaml

from . import model_scan


def get_status_data(config_path: Path) -> dict:
    """Reads config/models.yaml the same way `gremlin list` does."""
    text = config_path.read_text()
    entries = model_scan.list_all_entries(text)
    cfg = yaml.safe_load(text) or {}
    persona = cfg.get("persona", {})
    return {
        "models": [{"name": e["name"], "type": e["type"]} for e in entries],
        "primary_model": persona.get("primary_model"),
        "fallback_models": persona.get("fallback_models", []),
        "consult_models": persona.get("consult_models", []),
        "last_resort_model": persona.get("last_resort_model"),
    }
