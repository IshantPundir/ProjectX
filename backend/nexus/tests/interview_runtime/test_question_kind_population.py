"""Verifies build_session_config populates question_kind on QuestionConfig.

Companion to ``test_schemas.py``: that file exercises the broader Literal
acceptance + rejection grid. This one is a narrower, spec-anchored regression
gate for the post-phase-transition feature (spec
``docs/superpowers/specs/2026-05-19-behavioral-layer-and-intro-design.md``
§4 "Schema additions") — it pins down the three properties the orchestrator
actually depends on:

1. The default value is ``"technical_depth"`` so legacy banks (whose DB rows
   pre-date migration 0026 and would otherwise be NULL) round-trip cleanly.
2. ``"behavioral_star"`` is accepted on construction (the kind the
   orchestrator's transition detector looks for).
3. An unknown kind is rejected — defense against a future generator emitting
   a string not in the engine-side Literal.
"""
from __future__ import annotations

import pytest

from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    """Build a minimal-but-valid QuestionConfig kwargs dict.

    Matches every required field on the current schema (id, position, text,
    signal_values, estimated_minutes, is_mandatory, follow_ups,
    positive_evidence, red_flags, rubric, evaluation_hint). Tests override
    only the field they care about.
    """
    base: dict[str, object] = dict(
        id="q-1",
        position=0,
        text="What is your approach to handling network failures in distributed systems?",
        signal_values=["sig"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["evidence one", "evidence two", "evidence three"],
        red_flags=["red flag one", "red flag two"],
        rubric=QuestionRubric(
            excellent="detailed answer with concrete examples",
            meets_bar="adequate answer covering the basics",
            below_bar="vague or off-topic response",
        ),
        evaluation_hint="hint at least ten chars long",
    )
    base.update(overrides)
    return base


def test_question_config_defaults_to_technical_depth() -> None:
    """Legacy banks (no kind set on row) default to technical_depth."""
    config = QuestionConfig(**_valid_kwargs())
    assert config.question_kind == "technical_depth"


def test_question_config_accepts_behavioral_star() -> None:
    config = QuestionConfig(
        **_valid_kwargs(
            text="Walk me through your background and recent integration work experience.",
            question_kind="behavioral_star",
        ),
    )
    assert config.question_kind == "behavioral_star"


def test_question_config_rejects_unknown_kind() -> None:
    with pytest.raises(Exception):
        QuestionConfig(
            **_valid_kwargs(
                text="Walk me through your background and recent integration work experience.",
                question_kind="not_a_real_kind",
            ),
        )
