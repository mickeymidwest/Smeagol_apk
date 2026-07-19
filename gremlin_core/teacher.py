"""
When Gremlin's own execution hits a real error -- a self-improve patch
that doesn't compile or fails its tests -- this is the "teacher loop":
an external judge (claude or gemini, kept in their existing fallback
role too, not instead of it -- see config/models.yaml's fallback_models)
explains what went wrong and gives a corrected version. The correction
is logged into the same learning_log.jsonl schema consult.py already
writes to, so it becomes real fine-tuning material through the
existing dataset builder (finetune.py) with no special-casing there --
Gremlin actually learning from a concrete mistake, not just a one-off
fix applied and forgotten.

Deliberately separate from review.py's reviewer gate: that reviews a
patch BEFORE it's applied (a design-time check, and if it fails, the
patch is never applied at all). This runs AFTER a real failure -- a
patch that passed review but still didn't compile or broke a test --
a different moment producing a different, more concrete kind of
signal, worth keeping as its own thing rather than overloading
review_and_revise with a second responsibility.

Purely a logging/learning signal -- this never re-applies the
correction automatically. If you want the correction actually applied
to the code, that goes through the normal propose/review/apply flow
again, same as any other change.
"""
from __future__ import annotations
from typing import Optional

from .router import Router
from .consult import append_learning_log

TEACHER_SYSTEM_PROMPT = (
    "You are an external logic and code correction teacher for a local AI "
    "model. It attempted a task and hit a real error. Explain concisely "
    "*why* it failed and give the corrected result. Respond with the "
    "corrected result first, then a short explanation of the mistake, "
    "so the correction alone is useful even without reading the explanation."
)


async def teach_from_error(
    router: Router,
    teacher_model: str,
    task: str,
    attempt: str,
    error: str,
    root: str,
) -> Optional[str]:
    """Asks `teacher_model` to explain and correct a real execution
    failure, then logs the correction into learning_log.jsonl in the
    same {prompt, final_answer} shape consult.py already uses. Returns
    the correction text, or None if the teacher itself was unreachable
    -- fails closed, no bad/empty data logged on a failed teacher call."""
    prompt = f"Task: {task}\n\nAttempt:\n{attempt}\n\nError encountered:\n{error}"
    result = await router.route(teacher_model, prompt, system=TEACHER_SYSTEM_PROMPT)
    if not result.ok:
        return None

    append_learning_log(root, {
        "prompt": task,
        "final_answer": result.text,
        "kind": "teacher_correction",
        "teacher_model": teacher_model,
        "failed_attempt": attempt,
        "error": error,
    })
    return result.text
