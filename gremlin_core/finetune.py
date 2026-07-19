"""
Turns learning_log.jsonl consult history into an SFT-ready dataset for
fine-tuning Gremlin's own primary model -- the "neural links... slowly
become part of Gremlin" mechanism: every time a consult round-trip was
needed, the resulting (prompt, final_answer) pair is exactly the
example that teaches Gremlin to answer directly next time, without a
consult, on a similarly-phrased question.

Deliberately trains only on final_answer -- Gremlin's own synthesized
voice -- never on a consult model's raw text directly (see
consult.py's append_learning_log for where consulted_texts is kept,
which exists for inspection, not as training material itself). That
keeps a fine-tune from picking up another model's phrasing/voice
instead of Gremlin's own.

Only dataset-building lives here so far -- no heavy dependencies
needed. The training/GGUF-conversion/promotion pieces (which do need
torch/transformers/peft/bitsandbytes, unavailable in this sandbox) are
a deliberate follow-up once this half is confirmed useful on real
learning-log data.
"""
from __future__ import annotations
import json
import os
from pathlib import Path


def _log_path(root: str) -> str:
    return os.path.join(root, "data", "learning_log.jsonl")


def _read_log_entries(root: str) -> list[dict]:
    path = _log_path(root)
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def build_training_dataset(root: str, eval_fraction: float = 0.15) -> tuple[list[dict], list[dict]]:
    """
    Returns (train_examples, eval_examples) -- both lists of chat-format
    SFT examples: {"messages": [{"role": "user", "content": prompt},
    {"role": "assistant", "content": final_answer}]}.

    Every entry in learning_log.jsonl already represents a real
    "Gremlin didn't know this on its own" moment -- load_learned_answer
    short-circuits an exact-repeat question before a consult ever
    happens, so nothing here is an already-known answer. Split by time
    (oldest first), not a random shuffle, so the eval set is genuinely
    held out from whatever a fine-tune would have trained on, not just
    a random sample of the same distribution.
    """
    entries = _read_log_entries(root)
    entries.sort(key=lambda e: e.get("timestamp", 0))

    examples = []
    for e in entries:
        prompt = e.get("prompt")
        answer = e.get("final_answer")
        if not prompt or not answer:
            continue
        examples.append({
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ],
        })

    if not examples:
        return [], []
    if len(examples) == 1:
        return examples, []

    split_at = max(1, int(len(examples) * (1 - eval_fraction)))
    split_at = min(split_at, len(examples) - 1)  # always leave at least one eval example
    return examples[:split_at], examples[split_at:]


def write_training_set(root: str, eval_fraction: float = 0.15) -> dict:
    """Writes data/training_set.jsonl (train split) and
    data/eval_set.jsonl (held-out split, used later by
    checkpoint_eval.py) -- returns counts for the CLI to report."""
    train, eval_examples = build_training_dataset(root, eval_fraction=eval_fraction)

    data_dir = Path(root) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    train_path = data_dir / "training_set.jsonl"
    eval_path = data_dir / "eval_set.jsonl"

    with open(train_path, "w") as f:
        for ex in train:
            f.write(json.dumps(ex) + "\n")
    with open(eval_path, "w") as f:
        for ex in eval_examples:
            f.write(json.dumps(ex) + "\n")

    return {
        "train_count": len(train),
        "eval_count": len(eval_examples),
        "train_path": str(train_path),
        "eval_path": str(eval_path),
    }
