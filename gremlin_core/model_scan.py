"""
`python main.py models [directory]` -- scans a folder (defaults to
~/Downloads) for .gguf files and lets you pick which ones to register
as local models, straight into config/models.yaml.

Inserting is done as targeted text surgery, not a full YAML
read-modify-write-back: the file has hand-written comments that matter
(chat_format notes, safety notes on the persona section, etc.), and a
round-trip through yaml.safe_dump would silently discard all of them.
Instead this only ever inserts new, self-contained blocks right before
the `persona:` section, leaving every existing line untouched.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import yaml


def find_gguf_files(directory: str) -> list[Path]:
    root = Path(directory).expanduser()
    if not root.exists():
        return []
    return sorted(root.rglob("*.gguf"))


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def slugify(filename: str) -> str:
    stem = Path(filename).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()
    return slug or "local-model"


def already_registered_paths(config_text: str) -> set[str]:
    return set(re.findall(r'model_path:\s*"([^"]+)"', config_text))


def existing_model_names(config_text: str) -> set[str]:
    return set(re.findall(r"^\s*-\s*name:\s*(\S+)", config_text, re.MULTILINE))


def unique_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def build_entry_block(name: str, path: str, display_name: str) -> str:
    return (
        f"  - name: {name}\n"
        f"    type: local_gguf\n"
        f'    display_name: "{display_name}"\n'
        f'    model_path: "{path}"\n'
        f"    n_ctx: 4096\n"
        f"    n_gpu_layers: -1\n"
        f"    chat_format: chatml   # check this matches the model's actual prompt template\n"
        f"\n"
    )


def list_all_entries(config_text: str) -> list[dict]:
    """Parsed view of every registered model, in file order, for display
    purposes only -- removal itself uses the raw text, not this."""
    cfg = yaml.safe_load(config_text) or {}
    return cfg.get("models", [])


def persona_references(config_text: str, name: str) -> list[str]:
    """Which persona fields (if any) reference this model name -- so
    removal can warn before breaking gremlin's config."""
    cfg = yaml.safe_load(config_text) or {}
    persona = cfg.get("persona") or {}
    hits = []
    if persona.get("primary_model") == name:
        hits.append("primary_model")
    if persona.get("last_resort_model") == name:
        hits.append("last_resort_model")
    if name in (persona.get("fallback_models") or []):
        hits.append("fallback_models")
    if name in (persona.get("consult_models") or []):
        hits.append("consult_models")
    return hits


def remove_entry(config_path: str, name: str) -> bool:
    """Removes exactly one model's block from the models: list, using
    the same targeted-text-surgery approach as insert_entries -- every
    other line in the file, including comments, is left untouched.
    Returns False if no entry with that name was found."""
    path = Path(config_path)
    text = path.read_text()

    pattern = re.compile(
        rf"^  - name: {re.escape(name)}\b.*?(?=^  - name: |^persona:|^\S|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    new_text, count = pattern.subn("", text)
    if count == 0:
        return False

    path.write_text(new_text)
    return True


def _strip_from_flow_list(text: str, field: str, name: str) -> str:
    """Removes `name` from a persona field written as an inline YAML
    list, e.g. `consult_models: [gemini, dolphin-3b, local-3]`. Leaves
    the line alone if the field isn't present or doesn't contain name."""
    pattern = re.compile(rf"^(  {field}:\s*\[)([^\]]*)(\])", re.MULTILINE)

    def _sub(m):
        items = [x.strip() for x in m.group(2).split(",") if x.strip()]
        items = [x for x in items if x != name]
        return f"{m.group(1)}{', '.join(items)}{m.group(3)}"

    return pattern.sub(_sub, text)


def add_to_flow_list(config_path: str, field: str, name: str) -> bool:
    """Appends `name` to a persona field written as an inline YAML list
    (e.g. consult_models: [...]), if it isn't already there. Returns
    False if the field doesn't exist in the file at all (e.g. no
    persona section), True otherwise -- including when name was already
    present, which isn't an error."""
    path = Path(config_path)
    text = path.read_text()
    pattern = re.compile(rf"^(  {field}:\s*\[)([^\]]*)(\])", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return False

    items = [x.strip() for x in match.group(2).split(",") if x.strip()]
    if name in items:
        return True
    items.append(name)
    new_text = text[:match.start()] + f"{match.group(1)}{', '.join(items)}{match.group(3)}" + text[match.end():]
    path.write_text(new_text)
    return True


def remove_model_and_clean_persona(config_path: str, name: str) -> tuple[bool, Optional[str]]:
    """The real removal entry point: removes the model's block AND scrubs
    it from persona.fallback_models / persona.consult_models so nothing
    dangles. Refuses outright to remove persona.primary_model -- there's
    no safe automatic substitute for "the model gremlin answers with by
    default", so that has to be a deliberate config edit, not an
    automatic one. Validates the end result by actually building the
    registry, restoring the original file if anything's still broken."""
    from .registry import ModelRegistry

    path = Path(config_path)
    backup_text = path.read_text()

    refs = persona_references(backup_text, name)
    if "primary_model" in refs:
        return False, (
            f"'{name}' is gremlin's primary_model -- refusing to remove it automatically. "
            f"Change primary_model to something else in config/models.yaml first."
        )
    if "last_resort_model" in refs:
        return False, (
            f"'{name}' is gremlin's last_resort_model -- refusing to remove it automatically. "
            f"Change last_resort_model to something else in config/models.yaml first, "
            f"or remove that line if you don't want a last-resort check."
        )

    removed = remove_entry(config_path, name)
    if not removed:
        return False, f"no model named '{name}' found"

    text = path.read_text()
    text = _strip_from_flow_list(text, "fallback_models", name)
    text = _strip_from_flow_list(text, "consult_models", name)
    path.write_text(text)

    try:
        ModelRegistry.from_yaml(config_path)
    except Exception as e:
        path.write_text(backup_text)
        return False, f"removal would have broken the config, restored original: {e}"

    return True, None


def insert_entries(config_path: str, entries: list[str]) -> None:
    """Inserts new model blocks right before the `persona:` top-level key,
    or at the end of the file if there's no persona section. Never
    touches any existing line."""
    path = Path(config_path)
    text = path.read_text()
    combined = "".join(entries)

    match = re.search(r"^persona:", text, re.MULTILINE)
    if match:
        insert_at = match.start()
        new_text = text[:insert_at] + combined + text[insert_at:]
    else:
        new_text = text.rstrip("\n") + "\n\n" + combined

    path.write_text(new_text)


# Only these are safe to edit in place from a hologram head-slot or a
# remote `model-edit` call -- everything else (name, type, model_path)
# either identifies the entry or points at an actual file on disk, and
# a bad edit there should go through the guided `models`/`models --hf`
# flow instead, not a raw text swap.
EDITABLE_FIELDS = {"display_name", "chat_format", "n_gpu_layers", "n_ctx"}
_INT_FIELDS = {"n_gpu_layers", "n_ctx"}


def _find_block_span(text: str, name: str) -> Optional[tuple[int, int]]:
    """Same block boundary used by remove_entry -- one model's `- name:`
    line up to (not including) the next entry, `persona:`, or EOF."""
    pattern = re.compile(
        rf"^  - name: {re.escape(name)}\b.*?(?=^  - name: |^persona:|^\S|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None
    return match.start(), match.end()


def update_entry_field(config_path: str, name: str, field: str, value: str) -> tuple[bool, Optional[str]]:
    """Edits exactly one field on exactly one model's block, in place --
    same targeted-text-surgery approach as remove_entry/add_to_flow_list,
    so every other line (including comments) is left untouched. Rejects
    anything outside EDITABLE_FIELDS outright. Validates the result by
    rebuilding the registry afterward, restoring the original file if
    that fails (same rollback pattern as remove_model_and_clean_persona).
    Returns (True, None) on success, (False, reason) otherwise.
    """
    from .registry import ModelRegistry

    if field not in EDITABLE_FIELDS:
        return False, f"'{field}' isn't editable here -- only {sorted(EDITABLE_FIELDS)} are"

    path = Path(config_path)
    backup_text = path.read_text()

    span = _find_block_span(backup_text, name)
    if span is None:
        return False, f"no model named '{name}' found"
    start, end = span
    block = backup_text[start:end]

    line_pattern = re.compile(rf"^(    {re.escape(field)}:\s*)(\S.*?)(\s*#.*)?$", re.MULTILINE)
    line_match = line_pattern.search(block)
    if line_match is None:
        return False, f"'{name}' has no existing '{field}' field to edit"

    if field in _INT_FIELDS:
        try:
            int(value)
        except ValueError:
            return False, f"'{field}' must be an integer, got {value!r}"
        new_value_text = value
    else:
        # Quote strings the same way the rest of this file already does
        # (build_entry_block quotes display_name unconditionally) --
        # escape any embedded double quotes rather than rejecting them.
        escaped = value.replace('"', '\\"')
        new_value_text = f'"{escaped}"'

    prefix, _old_value, comment = line_match.group(1), line_match.group(2), line_match.group(3) or ""
    new_line = f"{prefix}{new_value_text}{comment}"
    new_block = block[:line_match.start()] + new_line + block[line_match.end():]
    new_text = backup_text[:start] + new_block + backup_text[end:]

    path.write_text(new_text)
    try:
        ModelRegistry.from_yaml(config_path)
    except Exception as e:
        path.write_text(backup_text)
        return False, f"edit would have broken the config, restored original: {e}"

    return True, None
