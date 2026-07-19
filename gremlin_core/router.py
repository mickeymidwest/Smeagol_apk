from __future__ import annotations
import asyncio
import json
import re
from typing import Optional

from .registry import ModelRegistry
from .backends.base import GenerationResult


PLAN_SYSTEM_PROMPT = (
    "You are one contributor among several AI models collaborating on a task. "
    "Given the task, respond with a short numbered plan (3-7 steps) of how you "
    "would approach it. Be concrete. Do not solve the task yet, just plan."
)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are merging plans proposed by several different AI models into one "
    "unified plan. Read all proposals, keep what's good from each, drop "
    "redundancy and disagreement, and output ONLY valid JSON in this exact "
    "shape, nothing else:\n"
    '{"steps": [{"id": 1, "task": "...", "assigned_to": null}, ...]}\n'
    "Do not assign steps yet -- leave assigned_to as null."
)


class Router:
    """
    Three ways to use your models:
      1. route()      - one prompt, one model
      2. broadcast()  - one prompt, many models in parallel, all responses back
      3. plan_and_build() - many models propose plans -> merged into one plan
                            -> steps assigned back out -> executed in parallel
    """

    def __init__(self, registry: ModelRegistry):
        self.registry = registry

    async def route(
        self, model_name: str, prompt: str, system: Optional[str] = None, **kw
    ) -> GenerationResult:
        backend = self.registry.get(model_name)
        return await backend.generate(prompt, system=system, **kw)

    async def broadcast(
        self, model_names: list[str], prompt: str, system: Optional[str] = None, **kw
    ) -> dict[str, GenerationResult]:
        """API backends (claude, gemini, ...) don't compete for local
        VRAM, so they still run together for speed. Local GGUF backends
        DO share the same limited GPU memory -- nothing here shares VRAM
        between concurrently-loading models, so running two at once on a
        constrained card (e.g. 8GB) risks an out-of-memory error that has
        nothing to do with which quant was picked. Those run one at a
        time instead: slower, but the thing that's actually supposed to
        work reliably. See gremlin_core.eviction for the other half of
        this -- unloading a local model once it's been idle a while, so
        VRAM doesn't just accumulate across many separate consults."""
        named = [(name, self.registry.get(name)) for name in model_names]
        local = [(n, b) for n, b in named if b.info.kind == "local_gguf"]
        remote = [(n, b) for n, b in named if b.info.kind != "local_gguf"]

        results: dict[str, GenerationResult] = {}

        if remote:
            remote_results = await asyncio.gather(
                *(b.generate(prompt, system=system, **kw) for _, b in remote)
            )
            for (name, _), r in zip(remote, remote_results):
                results[name] = r

        for name, b in local:
            results[name] = await b.generate(prompt, system=system, **kw)

        return results

    async def plan_and_build(
        self,
        model_names: list[str],
        task: str,
        synthesizer: Optional[str] = None,
        execute: bool = True,
    ) -> dict:
        """
        1. Every model in model_names proposes a plan for `task`.
        2. `synthesizer` (defaults to model_names[0]) merges all proposals
           into one JSON plan with discrete steps.
        3. Steps get round-robin assigned back across model_names.
        4. If execute=True, each model runs its assigned step(s) in parallel.
        Returns a dict with the raw proposals, the merged plan, assignments,
        and (if executed) each model's output for its step.
        """
        synthesizer = synthesizer or model_names[0]

        # Step 1: parallel plan proposals
        proposals = await self.broadcast(model_names, task, system=PLAN_SYSTEM_PROMPT)

        proposals_text = "\n\n".join(
            f"--- Proposal from {name} ---\n{res.text if res.ok else f'[failed: {res.error}]'}"
            for name, res in proposals.items()
        )

        # Step 2: synthesis into one structured plan
        synth_prompt = f"Task: {task}\n\n{proposals_text}\n\nMerge these into one plan."
        synth_result = await self.route(synthesizer, synth_prompt, system=SYNTHESIS_SYSTEM_PROMPT)

        plan = self._parse_plan_json(synth_result.text)
        if plan is None:
            # fall back: treat synthesizer's raw numbered list as the plan
            plan = {"steps": self._fallback_parse_numbered_list(synth_result.text)}

        # Step 3: round-robin assignment across all participating models
        steps = plan.get("steps", [])
        for i, step in enumerate(steps):
            step["assigned_to"] = model_names[i % len(model_names)]

        output = {
            "task": task,
            "proposals": {name: r.text for name, r in proposals.items()},
            "plan": steps,
        }

        if not execute:
            return output

        # Step 4: parallel execution of each assigned step
        async def run_step(step):
            model = step["assigned_to"]
            prompt = (
                f"Overall task: {task}\n\n"
                f"Your assigned step ({step.get('id')}): {step.get('task')}\n\n"
                "Complete this step. Be concrete and give a usable result, "
                "not just a description of what you'd do."
            )
            result = await self.route(model, prompt)
            return step.get("id"), model, result

        step_results = await asyncio.gather(*(run_step(s) for s in steps))
        output["results"] = [
            {"step_id": sid, "model": model, "output": res.text if res.ok else f"[failed: {res.error}]"}
            for sid, model, res in step_results
        ]
        return output

    @staticmethod
    def _parse_plan_json(text: str) -> Optional[dict]:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _fallback_parse_numbered_list(text: str) -> list[dict]:
        steps = []
        for i, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            m = re.match(r"^\d+[\.\)]\s*(.+)", line)
            if m:
                steps.append({"id": len(steps) + 1, "task": m.group(1), "assigned_to": None})
        return steps
