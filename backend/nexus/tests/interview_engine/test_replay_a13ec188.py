"""Replay assertion: session a13ec188 load-bearing turns must route
to the expected clarify_kind under the v2 intent-layer prompts.

Gated under `pytest -m prompt_quality` (LLM-in-the-loop, costs OpenAI
credits, nondeterministic). Run by hand after major Judge prompt
changes.

Fixture: backend/nexus/engine-events/a13ec188-ebf4-4b5e-95fa-912555a556a7.json
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.modules.interview_engine.models.judge import ClarifyKind, NextAction


SESSION_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "engine-events"
    / "a13ec188-ebf4-4b5e-95fa-912555a556a7.json"
)


def _load_judge_inputs_by_turn_index() -> dict[int, dict]:
    """Index judge.call input_summary payloads by their turn_index."""
    with SESSION_PATH.open() as f:
        data = json.load(f)
    events = data["events"]

    # Build turn_index -> turn_id map from turn.started.
    turn_index_by_id: dict[str, int] = {}
    for e in events:
        if e.get("kind") == "turn.started":
            p = e["payload"]
            turn_index_by_id[p["turn_id"]] = p["turn_index"]

    # Pull judge.call input_summary per turn_id.
    inputs_by_index: dict[int, dict] = {}
    for e in events:
        if e.get("kind") != "judge.call":
            continue
        p = e["payload"]
        idx = turn_index_by_id.get(p["turn_id"])
        if idx is not None:
            inputs_by_index[idx] = p.get("input_summary", {})
    return inputs_by_index


@pytest.mark.prompt_quality
@pytest.mark.parametrize(
    "turn_index, candidate_utterance, expected_kind",
    [
        (
            1,
            "Okay I couldn't understand the question. Can you?",
            ClarifyKind.broad_rephrase,
        ),
        (
            4,
            "What is the use case that Salesforce is being used in the "
            "Organization That helps me to set up the trigger, exact "
            "trigger, which is why I'm asking the question.",
            ClarifyKind.use_case_anchor,
        ),
        (
            7,
            "Why would there be duplicates for the same order ID? I mean, "
            "every order created in the Salesforce has their own unique "
            "order ID. Why would I mean, help me understand why would the "
            "same order ID be created twice?",
            ClarifyKind.concept_explanation,
        ),
        (
            11,
            "That's what I need to understand, how is it that ad "
            "impotency is a concern here because we are taking unique "
            "order IDs as a trigger for the recipes. So Uh, it's very "
            "hard for me to understand how a unique order ID being used "
            "as a trigger would lead to a situation where idempotency "
            "has to be enforced.",
            ClarifyKind.concept_explanation,
        ),
    ],
)
def test_a13ec188_routes_clarify_kind_correctly(
    turn_index: int,
    candidate_utterance: str,
    expected_kind: ClarifyKind,
) -> None:
    """Each load-bearing turn from session a13ec188 must route to the
    expected clarify_kind under the new Judge prompt.

    Implementation note: this test calls JudgeService directly with the
    same input_summary the original session captured, then asserts the
    Judge picks clarify with the expected clarify_kind. Other actions
    (push_back, advance) are FAIL — those are the misclassifications
    the rewrite is designed to fix.
    """
    inputs_by_index = _load_judge_inputs_by_turn_index()
    judge_input_summary = inputs_by_index.get(turn_index)
    assert judge_input_summary, (
        f"No judge.call event for turn_index={turn_index} in fixture"
    )

    # Reconstruct a JudgeInputPayload-like dict the JudgeService can use.
    # The fixture's input_summary captures the exact fields the Judge
    # saw. Override only the candidate_utterance (it was already in
    # the audit but explicit here for clarity).
    judge_input_summary = {**judge_input_summary,
                           "candidate_utterance": candidate_utterance}

    # NOTE: this test depends on a JudgeService dispatcher that can be
    # called with a raw input_summary dict. If the existing harness
    # uses a Pydantic-typed JudgeInputPayload, adapt the dict to the
    # model first. The exact call is:
    #
    #   from app.modules.interview_engine.judge.service import JudgeService
    #   service = JudgeService(...)
    #   out = asyncio.run(service.call(judge_input_payload))
    #
    # Inspect `app/modules/interview_engine/judge/service.py` to confirm
    # the exact entrypoint signature before implementing this assertion.
    pytest.skip(
        "Wire JudgeService harness using patterns from "
        "tests/interview_engine/judge/test_service.py — this is the only "
        "LLM-in-the-loop test in this plan."
    )

    # Once wired:
    # assert out.next_action == NextAction.clarify
    # assert isinstance(out.next_action_payload, ClarifyPayload)
    # assert out.next_action_payload.clarify_kind == expected_kind
