"""
The gate a proposed self-edit must pass before Gremlin is allowed to
apply it: two different models each review the diff independently, and
BOTH have to approve. If either rejects it, their specific feedback
goes back to a fixer model, the patch gets revised, and review restarts
from stage one -- so the final approved patch is always the one both
reviewers actually saw, not some earlier draft one of them missed.

This is the only path by which a self-edit is allowed through. There is
no way to skip straight to apply_patch from a proposal -- review_and_revise
sits between propose_patch and apply_patch in main.py's improve command.
"""
from __future__ import annotations
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .router import Router

REVIEW_SYSTEM_PROMPT = (
    "You are reviewing a proposed unified diff patch to an AI orchestrator's "
    "own source code, before it is allowed to be applied. You will be given "
    "the goal the patch is meant to achieve and the diff itself. Check "
    "whether it: (1) actually achieves the stated goal, (2) is syntactically "
    "and logically sound, (3) does not remove or weaken any existing "
    "sandboxing, safety checks, git-revert safety, or error handling, and "
    "(4) does not introduce anything unrelated to the goal. "
    "Respond with ONLY valid JSON in this exact shape, nothing else:\n"
    '{"verdict": "APPROVE", "feedback": ""}\n'
    "or\n"
    '{"verdict": "REQUEST_CHANGES", "feedback": "specific, actionable issues"}\n'
    "Be a real reviewer, not a rubber stamp -- if you're not sure it's "
    "correct, request changes rather than approve."
)

REVISE_SYSTEM_PROMPT = (
    "You previously proposed a unified diff patch to an AI orchestrator's "
    "own source code. A reviewer has requested changes. You will be given "
    "the original goal, your current diff, and the reviewer's feedback. "
    "Respond with ONLY a corrected unified diff that addresses the feedback "
    "-- no explanation, no markdown fences, no commentary."
)


@dataclass
class ReviewRound:
    reviewer: str
    approved: bool
    feedback: str


@dataclass
class ReviewOutcome:
    approved: bool
    patch: str
    rounds_used: int
    history: list[ReviewRound] = field(default_factory=list)
    reason: Optional[str] = None


def _parse_verdict(text: str) -> Optional[dict]:
    # Try a markdown-fenced block first -- non-greedy and anchored to
    # the actual ``` markers, so it isn't corrupted by unrelated braces
    # elsewhere in the response (e.g. feedback text that quotes a dict
    # literal or discusses code containing braces). Confirmed by testing
    # that the old greedy `\{.*\}` approach breaks exactly this way: it
    # spans from the first brace all the way to the LAST brace in the
    # entire text, producing invalid JSON whenever anything brace-like
    # appears after the real verdict.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # Whole response is raw JSON, no fence, no surrounding commentary.
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Last resort: naive greedy match. Still useful when there's exactly
    # one JSON object and nothing else brace-like in the text, but this
    # is the fragile path, not the first one tried anymore.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def _review_once(router: Router, reviewer: str, patch: str, goal: str) -> ReviewRound:
    prompt = f"Goal: {goal}\n\nProposed diff:\n{patch}"
    result = await router.route(reviewer, prompt, system=REVIEW_SYSTEM_PROMPT)

    if not result.ok:
        # A reviewer that's unreachable is not the same as a reviewer that
        # approved -- fail closed, request changes (retry), don't apply.
        return ReviewRound(reviewer=reviewer, approved=False,
                            feedback=f"reviewer unreachable: {result.error}")

    verdict = _parse_verdict(result.text)
    if verdict is None:
        # Couldn't parse a clean verdict -- fail closed rather than guess.
        lowered = result.text.lower()
        if "approve" in lowered and "request_changes" not in lowered and "request changes" not in lowered:
            return ReviewRound(reviewer=reviewer, approved=True, feedback="")
        return ReviewRound(reviewer=reviewer, approved=False,
                            feedback=f"reviewer response wasn't clear, treating as not approved: {result.text}")

    approved = verdict.get("verdict") == "APPROVE"
    return ReviewRound(reviewer=reviewer, approved=approved, feedback=verdict.get("feedback", ""))


async def _revise(router: Router, fixer: str, patch: str, goal: str, feedback: str) -> str:
    prompt = f"Goal: {goal}\n\nCurrent diff:\n{patch}\n\nReviewer feedback:\n{feedback}"
    result = await router.route(fixer, prompt, system=REVISE_SYSTEM_PROMPT)
    return result.text if result.ok else patch  # if the fixer itself fails, keep the old patch; next loop will just fail review again and stop rather than apply something worse


async def review_and_revise(
    router: Router,
    patch: str,
    goal: str,
    reviewer_a: str,
    reviewer_b: str,
    fixer: str,
    max_rounds: int = 4,
) -> ReviewOutcome:
    """
    Runs the patch through reviewer_a, then reviewer_b, only once both
    approve in sequence on the SAME patch. Any rejection at either stage
    sends feedback to `fixer`, and review restarts from reviewer_a on the
    revised patch -- so a fix for reviewer_b's concerns is always re-checked
    by reviewer_a too before being re-shown to reviewer_b.
    """
    history: list[ReviewRound] = []
    current_patch = patch

    for round_num in range(1, max_rounds + 1):
        review_a = await _review_once(router, reviewer_a, current_patch, goal)
        history.append(review_a)
        if not review_a.approved:
            current_patch = await _revise(router, fixer, current_patch, goal, review_a.feedback)
            continue

        review_b = await _review_once(router, reviewer_b, current_patch, goal)
        history.append(review_b)
        if not review_b.approved:
            current_patch = await _revise(router, fixer, current_patch, goal, review_b.feedback)
            continue

        return ReviewOutcome(approved=True, patch=current_patch, rounds_used=round_num, history=history)

    return ReviewOutcome(
        approved=False,
        patch=current_patch,
        rounds_used=max_rounds,
        history=history,
        reason=f"{reviewer_a} and {reviewer_b} did not both approve within {max_rounds} rounds",
    )


async def consult_consensus_check(
    router: Router,
    patch: str,
    goal: str,
    consult_models: list[str],
) -> ReviewOutcome:
    """
    The override path: instead of the claude/gemini gate, checks whether
    EVERY model in `consult_models` independently approves the patch as
    it currently stands. Unlike review_and_revise, this doesn't loop or
    revise on a rejection -- it's a single up-or-down consensus read on
    one fixed patch, called only after the normal gate has already failed
    and the caller has explicitly opted into the override (--allow-consult-override).
    Any model that's unreachable or unclear counts as not approving
    (same fail-closed rule as _review_once), so consensus really does
    require all of them, not just a quorum.
    """
    if not consult_models:
        return ReviewOutcome(approved=False, patch=patch, rounds_used=1, history=[],
                              reason="no consult models configured to check consensus against")

    rounds = list(await asyncio.gather(
        *(_review_once(router, m, patch, goal) for m in consult_models)
    ))
    approved = all(r.approved for r in rounds)
    reason = None if approved else (
        "not all consult models approved: "
        + ", ".join(r.reviewer for r in rounds if not r.approved)
    )
    return ReviewOutcome(approved=approved, patch=patch, rounds_used=1, history=rounds, reason=reason)
