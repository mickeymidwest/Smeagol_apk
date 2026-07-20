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

Below the dataset-building half is the follow-up this docstring used
to describe as future work: a QLoRA fine-tune of the primary model's
own base repo (4-bit + LoRA adapter, so training itself never needs
the full model resident -- fits an 8GB card), a merge + GGUF
reconversion via a real llama.cpp checkout (tools/llama.cpp -- its
per-architecture tensor-mapping logic isn't worth reimplementing) and
quantization via llama-cpp-python's own bound llama_model_quantize
(same CUDA-enabled build already linked, no separate binary needed),
and a promotion step that registers the result as a new model entry
and switches persona.primary_model to it. The old primary entry and
its file are never touched, so reverting is a one-line config edit.
"""
from __future__ import annotations
import ctypes
import json
import os
import subprocess
import sys
from datetime import datetime
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


DEFAULT_BASE_REPO = "mlabonne/Meta-Llama-3.1-8B-Instruct-abliterated"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def train_lora(root: str, base_repo: str = DEFAULT_BASE_REPO, epochs: int = 3, lr: float = 2e-4) -> dict:
    """
    QLoRA fine-tune on data/training_set.jsonl (write_training_set() must
    have already been run -- this doesn't call it itself, since building
    the dataset and deciding to spend GPU time training on it are separate
    decisions). 4-bit base + a small LoRA adapter keeps peak VRAM well
    inside an 8GB card; the adapter is saved on its own here, merged into
    full precision only later in merge_and_export_gguf, right before
    conversion -- training never needs the merged model resident.

    Returns {"out_dir", "adapter_dir", "base_repo", "train_loss", "eval_loss"}.
    Raises RuntimeError if training_set.jsonl is empty or missing.
    """
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    data_dir = Path(root) / "data"
    train_rows = _load_jsonl(data_dir / "training_set.jsonl")
    if not train_rows:
        raise RuntimeError(
            "data/training_set.jsonl is empty -- run write_training_set() first "
            "(needs real entries in data/learning_log.jsonl, i.e. Gremlin actually "
            "consulting on something, not just downloading models)."
        )
    eval_rows = _load_jsonl(data_dir / "eval_set.jsonl")

    tokenizer = AutoTokenizer.from_pretrained(base_repo)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def _tokenize(example):
        text = tokenizer.apply_chat_template(example["messages"], tokenize=False, add_generation_prompt=False)
        return tokenizer(text, truncation=True, max_length=2048)

    train_ds = Dataset.from_list(train_rows).map(_tokenize, remove_columns=["messages"])
    eval_ds = Dataset.from_list(eval_rows).map(_tokenize, remove_columns=["messages"]) if eval_rows else None

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(base_repo, quantization_config=bnb_config, device_map="auto")
    model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora_config)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = data_dir / "finetunes" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    args = TrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=lr,
        bf16=True,
        logging_steps=5,
        save_strategy="no",
        eval_strategy="epoch" if eval_ds is not None else "no",
        report_to=[],
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )
    result = trainer.train()

    eval_loss = trainer.evaluate().get("eval_loss") if eval_ds is not None else None

    adapter_dir = out_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    return {
        "out_dir": str(out_dir),
        "adapter_dir": str(adapter_dir),
        "base_repo": base_repo,
        "train_loss": result.training_loss,
        "eval_loss": eval_loss,
    }


def merge_and_export_gguf(root: str, adapter_dir: str, base_repo: str, quant: str = "Q4_K_M") -> str:
    """
    Merges the LoRA adapter into a full-precision copy of the base model,
    converts that to GGUF via a real llama.cpp checkout's
    convert_hf_to_gguf.py (tools/llama.cpp -- per-architecture tensor
    mapping isn't worth reimplementing here), then quantizes with
    llama-cpp-python's own bound llama_model_quantize -- the same
    CUDA-enabled build fixed earlier in this session, so no separate
    llama-quantize binary needs compiling. Returns the final .gguf path.
    """
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter_dir = Path(adapter_dir)
    merged_dir = adapter_dir.parent / "merged"

    base = AutoModelForCausalLM.from_pretrained(base_repo, torch_dtype=torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, str(adapter_dir)).merge_and_unload()
    merged.save_pretrained(str(merged_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_repo).save_pretrained(str(merged_dir))
    del base, merged
    import gc
    gc.collect()

    llama_cpp_root = Path(root) / "tools" / "llama.cpp"
    convert_script = llama_cpp_root / "convert_hf_to_gguf.py"
    if not convert_script.exists():
        raise RuntimeError(f"{convert_script} not found -- clone llama.cpp into tools/llama.cpp first")

    f16_path = adapter_dir.parent / "merged-f16.gguf"
    subprocess.run(
        [sys.executable, str(convert_script), str(merged_dir), "--outfile", str(f16_path), "--outtype", "f16"],
        check=True,
    )

    final_path = adapter_dir.parent / f"merged-{quant}.gguf"
    _quantize_gguf(str(f16_path), str(final_path), quant)
    f16_path.unlink(missing_ok=True)  # intermediate only, the quantized file is what gets registered

    return str(final_path)


def _quantize_gguf(src_path: str, dst_path: str, quant: str) -> None:
    import llama_cpp.llama_cpp as lc

    ftype_name = f"LLAMA_FTYPE_MOSTLY_{quant.upper()}"
    ftype = getattr(lc, ftype_name, None)
    if ftype is None:
        raise ValueError(f"Unknown quant type '{quant}' (expected e.g. Q4_K_M, Q5_K_M, Q8_0)")

    params = lc.llama_model_quantize_default_params()
    params.ftype = ftype
    params.nthread = os.cpu_count() or 4

    rc = lc.llama_model_quantize(src_path.encode(), dst_path.encode(), ctypes.byref(params))
    if rc != 0:
        raise RuntimeError(f"llama_model_quantize failed with code {rc}")


def promote_finetuned_model(config_path: str, gguf_path: str, base_chat_format: str = "llama-3") -> str:
    """
    Registers the new GGUF as its own model entry (never overwrites the old
    primary's file or entry -- reverting is just editing primary_model back)
    and switches persona.primary_model to it. Returns the new entry's name.
    Raises RuntimeError if the resulting config doesn't validate (model_scan
    restores the original file in that case, same rollback pattern used
    everywhere else in this file).
    """
    from . import model_scan

    config_text = Path(config_path).read_text()
    taken = model_scan.existing_model_names(config_text)
    name = model_scan.unique_name("gremlin-primary-ft", taken)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = model_scan.build_entry_block(name, gguf_path, f"Gremlin primary (fine-tuned {stamp})")
    model_scan.insert_entries(config_path, [block])

    ok, err = model_scan.update_entry_field(config_path, name, "chat_format", base_chat_format)
    if not ok:
        raise RuntimeError(f"registered '{name}' but couldn't set chat_format: {err}")

    ok, err = model_scan.set_primary_model(config_path, name)
    if not ok:
        raise RuntimeError(f"registered '{name}' but couldn't promote it: {err}")

    return name


def run_pipeline(root: str, config_path: str, base_repo: str = DEFAULT_BASE_REPO, epochs: int = 3,
                  quant: str = "Q4_K_M", promote: bool = False) -> dict:
    """Full ladder: dataset -> LoRA training -> merge/convert/quantize ->
    (optionally) promote. Returns a dict the CLI prints as it goes; raises
    on any stage's failure rather than half-applying a broken result."""
    ds = write_training_set(root)
    if ds["train_count"] == 0:
        return {"stage": "dataset", **ds}

    train_result = train_lora(root, base_repo=base_repo, epochs=epochs)
    gguf_path = merge_and_export_gguf(root, train_result["adapter_dir"], base_repo, quant=quant)

    promoted_name = None
    if promote:
        promoted_name = promote_finetuned_model(config_path, gguf_path)

    return {
        "stage": "done",
        **ds,
        **train_result,
        "gguf_path": gguf_path,
        "promoted_name": promoted_name,
    }
