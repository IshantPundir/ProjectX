"""Verifies build_session_config populates question_kind on QuestionConfig.

Companion to ``test_schemas.py``: that file exercises the str-relaxation grid.
This one is a narrower regression gate for the QuestionConfig read projection
(engine-v2 M2, decision D1):

1. The default is ``"technical_scenario"`` (new-taxonomy default post M2).
2. Legacy kinds (``"behavioral_star"``, ``"technical_depth"``) are accepted
   unchanged — the relaxed ``str`` field keeps the v1 engine backstop green
   with zero edits to ``tests/interview_engine/``.
3. New-taxonomy kind ``"behavioral"`` is also accepted.
4. Any string is accepted at the read projection; enforcement lives at
   the DB CHECK constraint + the GeneratedQuestion generator model.
"""
from __future__ import annotations

import pytest

from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    """Build a minimal-but-valid QuestionConfig kwargs dict.

    Matches every required field on the current schema (id, position, text,
    signal_values, follow_ups, positive_evidence, red_flags, rubric,
    evaluation_hint). Tests override only the field they care about.
    """
    base: dict[str, object] = dict(
        id="q-1",
        position=0,
        text="What is your approach to handling network failures in distributed systems?",
        signal_values=["sig"],
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


def test_question_config_defaults_to_technical_scenario() -> None:
    """Default is technical_scenario post M2 taxonomy switch."""
    config = QuestionConfig(**_valid_kwargs())
    assert config.question_kind == "technical_scenario"


def test_question_config_accepts_behavioral_star() -> None:
    """Legacy kind accepted: relaxed str projection (D1) keeps v1 backstop green."""
    config = QuestionConfig(
        **_valid_kwargs(
            text="Walk me through your background and recent integration work experience.",
            question_kind="behavioral_star",
        ),
    )
    assert config.question_kind == "behavioral_star"


def test_question_config_accepts_behavioral() -> None:
    """New taxonomy kind accepted."""
    config = QuestionConfig(
        **_valid_kwargs(
            text="Walk me through your background and recent integration work experience.",
            question_kind="behavioral",
        ),
    )
    assert config.question_kind == "behavioral"


def test_question_config_accepts_any_str() -> None:
    """Relaxed str: enforcement is at DB CHECK + GeneratedQuestion, not at read projection.

    Previously this test verified rejection — after M2 D1 decision the field
    is an unconstrained str so the read projection never rejects a stored value.
    """
    config = QuestionConfig(
        **_valid_kwargs(
            text="Walk me through your background and recent integration work experience.",
            question_kind="not_a_real_kind",
        ),
    )
    assert config.question_kind == "not_a_real_kind"
