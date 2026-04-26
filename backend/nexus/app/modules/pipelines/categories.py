"""Server-side mirror of the spec §6 capability matrix.

Used by the activation gate (and possibly future modules) to derive category-aware
predicates from stage_type without re-encoding the matrix.
"""


def bank_eligible_stage_types() -> set[str]:
    """Stage types that have question banks. Per spec §6 + §11.1."""
    return {"phone_screen", "ai_screening", "human_interview", "take_home"}


def middle_stage_types_for_activation() -> set[str]:
    """Stage types that count as 'middle' for the activation predicate.
    Per spec §7.1 predicate #2: take_home is excluded (disabled for now)."""
    return {"phone_screen", "ai_screening", "human_interview"}


def human_led_stage_types() -> set[str]:
    """Stage types requiring an interviewer."""
    return {"phone_screen", "human_interview"}


def is_paused(stage) -> bool:
    return getattr(stage, "paused_at", None) is not None
