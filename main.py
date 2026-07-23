"""
Gremlin -- core orchestrator CLI.

Usage (after `chmod +x gremlin` and putting it on your PATH):
  gremlin list
  gremlin models [directory]     (default: ~/Downloads)
  gremlin models --hf "<search terms>"   -- search & download from Hugging Face
  gremlin remove
  gremlin model-edit <name> --field=<field> --value=<value>
    Non-interactive on purpose -- edits one field on one existing model entry
    in place (fields: display_name, chat_format, n_gpu_layers, n_ctx). This is
    what the hologram's head-slots trigger remotely via /admin/execute, so it
    never prompts. Swapping the actual model file stays a `models --hf` job.
  gremlin chat <model_name>
  gremlin broadcast <model1,model2,...> "<prompt>"
  gremlin plan <model1,model2,...> "<task>"
  gremlin improve <model1,model2,...> "<goal>" [--apply] [--test] [--reviewer-a=NAME] [--reviewer-b=NAME] [--allow-consult-override] [--teach-on-failure] [--teacher=NAME]
    --allow-consult-override: if reviewer-a/reviewer-b (default claude/gemini) don't both
    approve, fall back to checking whether all 4 local consult models (config/models.yaml
    persona.consult_models) unanimously approve instead. Off by default -- must be requested
    explicitly per run.
    --teach-on-failure: if the applied patch fails to compile or fails a test (a real,
    concrete failure -- not just "didn't apply cleanly"), --teacher (default claude) explains
    the mistake and the correction is logged to data/learning_log.jsonl as future fine-tuning
    material. Never auto-applies the correction. Off by default.
  gremlin auto-fix
  gremlin edit <path> ["<problem description>"]
  gremlin serve [port]           (default: 8765) -- lets the phone app connect
  gremlin admin-token             -- reveal the separate admin token (system commands, reboot)
  gremlin set-sudo-password       -- cache a sudo password locally so root commands can run
    remotely (phone or desktop chat) without a monitor. Verified against real sudo before
    being cached; never sent over the network. (also: gremlin clear-sudo-password)
  gremlin list-snapshots          -- list BTRFS snapshots (via snapper)
  gremlin rollback-to <number>    -- roll back to a snapshot and reboot (requires sudo
    password cached via set-sudo-password)
  gremlin build-training-set      -- turn data/learning_log.jsonl (every time a consult
    was needed) into data/training_set.jsonl + data/eval_set.jsonl, for fine-tuning
    Gremlin's own primary model on what the consult group has contributed over time.
  gremlin finetune [--promote]    -- runs build-training-set, then a QLoRA fine-tune of
    the primary model's base repo on the result, merges + converts back to GGUF. Without
    --promote the new .gguf is left on disk untouched; with it, persona.primary_model in
    config/models.yaml is switched to the new version (the old model entry/file are left
    alone either way, so reverting is a one-line config edit).

Or directly: python main.py <command> ...
"""
import asyncio
import sys
from pathlib import Path
from typing import Optional

from gremlin_core.registry import ModelRegistry
from gremlin_core.router import Router
from gremlin_core import self_improve
from gremlin_core import consult
from gremlin_core import model_scan
from gremlin_core import script_edit
from gremlin_core import server
from gremlin_core import hf_hub
from gremlin_core import root_exec
from gremlin_core import snapshots as snapshots_mod
from gremlin_core import finetune
from gremlin_core.process_lock import git_mutation_lock, AlreadyRunning

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads a .env file in the current directory if one exists
except ImportError:
    pass  # optional -- falls back to whatever's already in the shell environment

CONFIG_PATH = "config/models.yaml"
PROJECT_ROOT = "."
DEFAULT_SCAN_DIR = "~/Downloads"


def cmd_models(directory: str):
    directory = directory or DEFAULT_SCAN_DIR
    found = model_scan.find_gguf_files(directory)
    if not found:
        print(f"No .gguf files found in {directory}")
        return

    config_text = open(CONFIG_PATH).read()
    registered = model_scan.already_registered_paths(config_text)
    taken_names = model_scan.existing_model_names(config_text)

    print(f"Found {len(found)} .gguf file(s) in {directory}:\n")
    for i, f in enumerate(found, start=1):
        size = model_scan.human_size(f.stat().st_size)
        tag = "  [already added]" if str(f.resolve()) in registered else ""
        print(f"  {i}. {f.name}  ({size}){tag}")

    print()
    choice = input("Add which ones? (comma-separated numbers, 'all', or blank to cancel): ").strip()
    if not choice:
        print("Cancelled -- nothing added.")
        return

    if choice.lower() == "all":
        indices = list(range(1, len(found) + 1))
    else:
        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
        except ValueError:
            print("Couldn't parse that -- use numbers like '1,3' or 'all'.")
            return

    blocks = []
    added_names = []
    for i in indices:
        if i < 1 or i > len(found):
            print(f"Skipping {i} -- out of range.")
            continue
        f = found[i - 1]
        resolved = str(f.resolve())
        if resolved in registered:
            print(f"Skipping {f.name} -- already registered.")
            continue
        base_name = model_scan.slugify(f.name)
        name = model_scan.unique_name(base_name, taken_names)
        taken_names.add(name)
        blocks.append(model_scan.build_entry_block(name, resolved, f.stem))
        added_names.append(name)

    if not blocks:
        print("Nothing new to add.")
        return

    model_scan.insert_entries(CONFIG_PATH, blocks)
    for name in added_names:
        model_scan.add_to_flow_list(CONFIG_PATH, "consult_models", name)
    print(f"\nAdded to {CONFIG_PATH}: {', '.join(added_names)}")
    print("Also added to gremlin's consult_models, so he'll actually reach for these when uncertain.")
    print("Run `python main.py list` to confirm, and adjust chat_format per model if needed.")


def cmd_models_hf(query: str):
    print(f"Searching Hugging Face for: {query}\n")
    try:
        results = hf_hub.search_models(query, limit=8)
    except Exception as e:
        print(f"Search failed: {e}")
        return

    if not results:
        print("No GGUF repos found for that search.")
        return

    for i, r in enumerate(results, start=1):
        print(f"  {i}. {r['id']}  ({r['downloads']} downloads, {r['likes']} likes)")

    print()
    choice = input("Which repo? (number, or blank to cancel): ").strip()
    if not choice:
        print("Cancelled.")
        return
    try:
        repo = results[int(choice) - 1]["id"]
    except (ValueError, IndexError):
        print("Not a valid choice.")
        return

    print(f"\nFetching file list for {repo}...\n")
    try:
        files = hf_hub.list_gguf_files(repo)
    except Exception as e:
        print(f"Couldn't list files: {e}")
        return

    if not files:
        print("No .gguf files found in that repo.")
        return

    for i, f in enumerate(files, start=1):
        print(f"  {i}. {f['filename']}  ({model_scan.human_size(f['size'])})")

    print()
    file_choice = input("Which file (quantization)? (number, or blank to cancel): ").strip()
    if not file_choice:
        print("Cancelled.")
        return
    try:
        chosen = files[int(file_choice) - 1]
    except (ValueError, IndexError):
        print("Not a valid choice.")
        return

    dest_dir = Path(PROJECT_ROOT) / "models"
    dest_path = dest_dir / chosen["filename"]
    print(f"\nDownloading to {dest_path} ...")

    last_pct = [-1]
    def progress(downloaded, total):
        if total:
            pct = int(downloaded * 100 / total)
            if pct != last_pct[0] and pct % 10 == 0:
                print(f"  {pct}%")
                last_pct[0] = pct

    try:
        hf_hub.download_file(repo, chosen["filename"], str(dest_path), progress_callback=progress)
    except Exception as e:
        print(f"Download failed: {e}")
        return

    config_text = open(CONFIG_PATH).read()
    taken_names = model_scan.existing_model_names(config_text)
    base_name = model_scan.slugify(chosen["filename"])
    name = model_scan.unique_name(base_name, taken_names)
    block = model_scan.build_entry_block(name, str(dest_path.resolve()), chosen["filename"])
    model_scan.insert_entries(CONFIG_PATH, [block])
    model_scan.add_to_flow_list(CONFIG_PATH, "consult_models", name)

    print(f"\nDownloaded and added as '{name}'.")
    print("Also added to gremlin's consult_models, so he'll actually reach for this when uncertain.")
    print("Run `gremlin list` to confirm, and check chat_format matches this model's template.")


def cmd_remove():
    config_text = open(CONFIG_PATH).read()
    entries = model_scan.list_all_entries(config_text)
    if not entries:
        print("No models registered.")
        return

    print("Registered models:\n")
    for i, e in enumerate(entries, start=1):
        refs = model_scan.persona_references(config_text, e["name"])
        tag = f"  [used by gremlin: {', '.join(refs)}]" if refs else ""
        print(f"  {i}. {e['name']} ({e['type']}){tag}")

    print()
    choice = input("Remove which one(s)? (comma-separated numbers, or blank to cancel): ").strip()
    if not choice:
        print("Cancelled -- nothing removed.")
        return

    try:
        indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
    except ValueError:
        print("Couldn't parse that -- use numbers like '1' or '1,3'.")
        return

    for i in indices:
        if i < 1 or i > len(entries):
            print(f"Skipping {i} -- out of range.")
            continue
        name = entries[i - 1]["name"]

        refs = model_scan.persona_references(config_text, name)
        if refs:
            confirm = input(
                f"'{name}' is used by gremlin's {', '.join(refs)} -- "
                f"removing it will also clean it out of those list(s). Remove anyway? (y/N): "
            ).strip().lower()
            if confirm != "y":
                print(f"Skipped {name}.")
                continue

        ok, err = model_scan.remove_model_and_clean_persona(CONFIG_PATH, name)
        if ok:
            print(f"Removed {name}.")
            config_text = open(CONFIG_PATH).read()  # refresh for subsequent iterations
        else:
            print(f"Did NOT remove {name}: {err}")


def cmd_model_edit(name: str, field: Optional[str], value: Optional[str]):
    """Deliberately non-interactive (no input() prompts) -- unlike the
    other model-management commands above, this one also needs to work
    as a single fire-and-forget shell command over the server's
    /admin/execute endpoint (e.g. triggered from the Android app's
    hologram head-slots), which has no stdin channel to prompt through."""
    if not field or not value:
        print('Usage: gremlin model-edit <name> --field=<field> --value=<value>')
        print(f"  fields: {sorted(model_scan.EDITABLE_FIELDS)}")
        return

    config_text = open(CONFIG_PATH).read()
    entries = {e["name"]: e for e in model_scan.list_all_entries(config_text)}
    old_value = entries.get(name, {}).get(field, "(unset)") if name in entries else None

    ok, err = model_scan.update_entry_field(CONFIG_PATH, name, field, value)
    if ok:
        print(f"'{name}'.{field}: {old_value!r} -> {value!r}")
    else:
        print(f"NOT edited: {err}")


async def cmd_set_sudo_password():
    import getpass
    password = getpass.getpass(
        "Desktop sudo password (verified against real sudo, cached locally only, "
        "never sent over the network): "
    )
    if not password:
        print("Cancelled -- nothing entered.")
        return
    ok, message = await root_exec.set_sudo_password(PROJECT_ROOT, password)
    print(message if ok else f"NOT saved: {message}")


def cmd_clear_sudo_password():
    root_exec.clear_sudo_password(PROJECT_ROOT)
    print("Cleared the cached sudo password.")


async def cmd_list_snapshots():
    ok, result = await snapshots_mod.list_snapshots(PROJECT_ROOT)
    if not ok:
        print(f"Couldn't list snapshots: {result}")
        return
    if not result:
        print("No snapshots found.")
        return
    for s in result:
        print(f"  {s['number']}  {s['date']}  {s['description']}")


async def cmd_rollback_to(number: str, skip_confirm: bool = False):
    if not skip_confirm:
        confirm = input(f"Roll back to snapshot {number} and reboot NOW? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
    ok, message = await snapshots_mod.rollback_to(number, PROJECT_ROOT)
    print(message)


def cmd_build_training_set():
    result = finetune.write_training_set(PROJECT_ROOT)
    print(f"Wrote {result['train_count']} training example(s) to {result['train_path']}")
    print(f"Wrote {result['eval_count']} held-out eval example(s) to {result['eval_path']}")
    if result["train_count"] == 0:
        print(
            "Nothing here yet -- these come from data/learning_log.jsonl, which only "
            "gets an entry each time Gremlin's own answer was uncertain and a consult "
            "was needed. Chat with Gremlin a bit more first."
        )


def cmd_finetune(promote: bool):
    print("Building training set from data/learning_log.jsonl...")
    try:
        result = finetune.run_pipeline(PROJECT_ROOT, CONFIG_PATH, promote=promote)
    except Exception as e:
        print(f"Fine-tune failed: {e}")
        return

    if result["stage"] == "dataset":
        print(f"Wrote {result['train_count']} training example(s) -- nothing to train on yet.")
        print(
            "These come from data/learning_log.jsonl, which only gets an entry each time "
            "Gremlin's own answer was uncertain and a consult was needed. Chat with Gremlin "
            "a bit more first, then try again."
        )
        return

    print(f"Trained on {result['train_count']} example(s), held out {result['eval_count']} for eval.")
    loss_line = f"Train loss: {result['train_loss']:.4f}"
    if result["eval_loss"] is not None:
        loss_line += f", eval loss: {result['eval_loss']:.4f}"
    print(loss_line)
    print(f"Merged + quantized GGUF: {result['gguf_path']}")
    if result["promoted_name"]:
        print(f"Promoted as '{result['promoted_name']}' -- persona.primary_model now points to it.")
        print("Run `gremlin list` to confirm, or edit config/models.yaml's primary_model to revert.")
    else:
        print(
            "Not promoted -- the new GGUF is on disk but gremlin's primary_model is untouched. "
            "Re-run with --promote once you've sanity-checked it, or register/point to it manually."
        )


async def cmd_list(registry: ModelRegistry):
    print("Registered models:")
    for name in registry.names():
        b = registry.get(name)
        tag = " <- talk to this one" if b.info.kind == "persona" else ""
        print(f"  - {name} ({b.info.kind}) {b.info.notes}{tag}")


async def cmd_chat(registry: ModelRegistry, router: Router, model_name: str):
    backend = registry.get(model_name)
    is_persona = backend.info.kind == "persona"

    print(f"Chatting with {model_name}. Ctrl+C to quit.\n")
    while True:
        try:
            user_input = input("you> ")
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if is_persona:
            result = await consult.consult_and_learn(
                router, model_name, backend.consult_model_names, user_input, PROJECT_ROOT,
                last_resort_model=backend.last_resort_model_name,
                consult_sample_rate=backend.consult_sample_rate,
            )
            print(f"{model_name}> {result['answer']}")
            if result["from_memory"]:
                print("   (answered from something learned earlier -- no model call needed)")
            elif result["consulted"]:
                if result["contributors"]:
                    via = "last-resort check" if result.get("escalated") else "consulted"
                    print(f"   (wasn't sure on its own -- {via}: {', '.join(result['contributors'])})")
                else:
                    print(f"   ({result.get('note', 'consulted but nothing came back')})")
            print()
        else:
            result = await router.route(model_name, user_input)
            if result.ok:
                print(f"{model_name}> {result.text}\n")
            else:
                print(f"{model_name}> [error: {result.error}]\n")


async def cmd_broadcast(router: Router, model_names: list[str], prompt: str):
    results = await router.broadcast(model_names, prompt)
    for name, res in results.items():
        print(f"\n=== {name} ===")
        print(res.text if res.ok else f"[error: {res.error}]")


async def cmd_plan(router: Router, model_names: list[str], task: str):
    output = await router.plan_and_build(model_names, task)
    print("\n=== Merged Plan ===")
    for step in output["plan"]:
        print(f"  [{step.get('id')}] ({step.get('assigned_to')}) {step.get('task')}")
    print("\n=== Results ===")
    for r in output.get("results", []):
        print(f"\n--- Step {r['step_id']} ({r['model']}) ---")
        print(r["output"])


async def cmd_improve(
    router: Router,
    model_names: list[str],
    goal: str,
    do_apply: bool,
    run_tests: bool,
    reviewer_a: str,
    reviewer_b: str,
    allow_consult_override: bool = False,
    consult_models: Optional[list[str]] = None,
    teach_on_failure: bool = False,
    teacher_model: str = "gemini",
):
    print(f"Asking {', '.join(model_names)} to propose changes for: {goal}\n")
    patch = await self_improve.propose_patch(router, model_names, goal, PROJECT_ROOT)
    print("=== Proposed diff ===")
    print(patch)

    if not do_apply:
        print("\n(dry run -- rerun with --apply to actually apply this patch)")
        return

    print(f"\n=== Review gate: {reviewer_a} then {reviewer_b} must both approve ===")
    result = await self_improve.run_self_edit(
        router, PROJECT_ROOT, goal, model_names,
        reviewer_a=reviewer_a, reviewer_b=reviewer_b, run_tests=run_tests,
        allow_consult_override=allow_consult_override, consult_models=consult_models,
        teach_on_failure=teach_on_failure, teacher_model=teacher_model,
        patch=patch,
    )

    for r in result.get("review_history", []):
        verdict = "APPROVED" if r["approved"] else "REQUESTED CHANGES"
        print(f"  [{r['reviewer']}] {verdict}" + (f" -- {r['feedback']}" if r["feedback"] else ""))

    print("\n=== Result ===")
    if result["applied"] and result.get("committed"):
        print(f"Applied and committed: {result['commit_message']}")
        print(f"Files changed: {result['files_changed']}")
    elif result["applied"]:
        print(f"Applied but NOT committed -- {result.get('warning')}")
        print(f"Files changed: {result['files_changed']}")
    else:
        print(f"NOT applied: {result['reason']}")


async def cmd_auto_fix(registry: ModelRegistry, router: Router):
    goal = input("What should Gremlin add to its own code, or fix, or learn to do? ").strip()
    if not goal:
        print("Cancelled -- nothing to do.")
        return

    model_names = [n for n in registry.names() if registry.get(n).info.kind != "persona"]
    print(f"Using: {', '.join(model_names)}")
    run_tests_input = input("Also run pytest before committing? (y/N): ").strip().lower()
    override_input = input(
        "If gemini/deepseek-r1-distill-8b don't both approve, allow the 4 local consult models "
        "to approve it instead if they unanimously agree? (y/N): "
    ).strip().lower()
    teach_input = input(
        "If the patch fails to compile or fails a test, ask gemini to explain the "
        "mistake and log the correction for future fine-tuning? (y/N): "
    ).strip().lower()

    # Reuses cmd_improve entirely -- auto-fix is a friendlier front door,
    # not a different, lighter-weight path. The two-reviewer gate always
    # applies first; the consult-consensus override and the teacher loop
    # are both opt-in per run, never silent.
    await cmd_improve(
        router, model_names, goal, do_apply=True, run_tests=(run_tests_input == "y"),
        reviewer_a="gemini", reviewer_b="deepseek-r1-distill-8b",
        allow_consult_override=(override_input == "y"),
        teach_on_failure=(teach_input == "y"), teacher_model="gemini",
        consult_models=registry.consult_models(),
    )


async def cmd_edit(registry: ModelRegistry, router: Router, path: str, problem: Optional[str]):
    refusal = script_edit.check_path_safety(path)
    if refusal:
        print(f"Refused: {refusal}")
        return

    resolved = Path(path).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        print(f"No such file: {resolved}")
        return

    if not problem:
        problem = input(f"What's wrong with {resolved.name}? ").strip()
        if not problem:
            print("Cancelled -- no problem description given.")
            return

    try:
        with git_mutation_lock(PROJECT_ROOT):
            model_names = [n for n in registry.names() if registry.get(n).info.kind != "persona"]
            print(f"Asking {', '.join(model_names)} to propose a fix for {resolved.name}...\n")

            new_content = await script_edit.propose_fix(router, model_names, str(resolved), problem)
            old_content = resolved.read_text()
            diff = script_edit.diff_preview(old_content, new_content, resolved.name)

            if not diff.strip():
                print("No changes proposed -- nothing to do.")
                return

            print("=== Proposed changes ===")
            print(diff)
            confirm = input("\nApply this fix? (y/N): ").strip().lower()
            if confirm != "y":
                print("Cancelled -- nothing changed.")
                return

            verify_command = input(
                "Optional: command to verify the fix (e.g. `bash -n script.sh`), or blank to skip: "
            ).strip() or None

            result = await script_edit.apply_fix(
                str(resolved), new_content, verify_command=verify_command,
                project_root=PROJECT_ROOT, problem=problem,
            )
            if result["applied"]:
                print(f"\nApplied. Original backed up to: {result['backup_path']}")
            else:
                print(f"\nNOT applied: {result['reason']}")
    except AlreadyRunning as e:
        print(f"\nNot starting -- {e}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "models":
        if len(sys.argv) > 2 and sys.argv[2] == "--hf":
            query = sys.argv[3] if len(sys.argv) > 3 else ""
            if not query:
                print('Usage: gremlin models --hf "search terms"')
                return
            cmd_models_hf(query)
            return
        directory = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_models(directory)
        return

    if cmd == "remove":
        cmd_remove()
        return

    if cmd == "model-edit":
        if len(sys.argv) < 3:
            print('Usage: gremlin model-edit <name> --field=<field> --value=<value>')
            return
        model_name = sys.argv[2]
        field = value = None
        for arg in sys.argv[3:]:
            if arg.startswith("--field="):
                field = arg.split("=", 1)[1]
            elif arg.startswith("--value="):
                value = arg.split("=", 1)[1]
        cmd_model_edit(model_name, field, value)
        return

    if cmd == "set-sudo-password":
        await cmd_set_sudo_password()
        return

    if cmd == "clear-sudo-password":
        cmd_clear_sudo_password()
        return

    if cmd == "list-snapshots":
        await cmd_list_snapshots()
        return

    if cmd == "rollback-to":
        if len(sys.argv) < 3:
            print("Usage: gremlin rollback-to <number>")
            return
        await cmd_rollback_to(sys.argv[2])
        return

    if cmd == "build-training-set":
        cmd_build_training_set()
        return

    if cmd == "finetune":
        promote = "--promote" in sys.argv[2:]
        cmd_finetune(promote)
        return

    registry = ModelRegistry.from_yaml(CONFIG_PATH)
    router = Router(registry)

    try:
        if cmd == "list":
            await cmd_list(registry)
        elif cmd == "chat":
            await cmd_chat(registry, router, sys.argv[2])
        elif cmd == "broadcast":
            models = sys.argv[2].split(",")
            await cmd_broadcast(router, models, sys.argv[3])
        elif cmd == "plan":
            models = sys.argv[2].split(",")
            await cmd_plan(router, models, sys.argv[3])
        elif cmd == "improve":
            models = sys.argv[2].split(",")
            goal = sys.argv[3]
            extra_args = sys.argv[4:]
            do_apply = "--apply" in extra_args
            run_tests = "--test" in extra_args
            allow_consult_override = "--allow-consult-override" in extra_args
            teach_on_failure = "--teach-on-failure" in extra_args
            reviewer_a = "gemini"
            reviewer_b = "deepseek-r1-distill-8b"
            teacher_model = "gemini"
            for arg in extra_args:
                if arg.startswith("--reviewer-a="):
                    reviewer_a = arg.split("=", 1)[1]
                elif arg.startswith("--reviewer-b="):
                    reviewer_b = arg.split("=", 1)[1]
                elif arg.startswith("--teacher="):
                    teacher_model = arg.split("=", 1)[1]
            await cmd_improve(
                router, models, goal, do_apply, run_tests, reviewer_a, reviewer_b,
                allow_consult_override=allow_consult_override,
                consult_models=registry.consult_models(),
                teach_on_failure=teach_on_failure, teacher_model=teacher_model,
            )
        elif cmd == "auto-fix":
            await cmd_auto_fix(registry, router)
        elif cmd == "edit":
            path = sys.argv[2]
            problem = sys.argv[3] if len(sys.argv) > 3 else None
            await cmd_edit(registry, router, path, problem)
        elif cmd == "serve":
            port = int(sys.argv[2]) if len(sys.argv) > 2 else server.DEFAULT_PORT
            server.serve(registry, router, PROJECT_ROOT, port=port)
        elif cmd == "admin-token":
            data_dir = Path(PROJECT_ROOT) / "data"
            admin_token = server.get_or_create_admin_token(data_dir)
            print(f"Admin token: {admin_token}")
            print("Enter this manually in the Android app's Admin section --")
            print("it's never shown in the regular pairing QR/output, on purpose.")
        else:
            print(__doc__)
    finally:
        await registry.close_all()


if __name__ == "__main__":
    asyncio.run(main())
