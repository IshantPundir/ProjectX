"""The Directive — the ONLY object that crosses Control Plane (brain) -> Conversation
Plane (mouth). Carries only speakable text + delivery metadata; never rubric/evidence
(no-leak by construction; see directive no-leak validator in this module). Supports
Option C staging/supersession. Design: DESIGN-SPEC §7 + doc 11.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DirectiveAct(StrEnum):
    """Closed interviewer move-set. The mouth has no behavior outside this enum."""

    INTRO = "INTRO"            # greeting + AI disclosure + format + hand-off
    ASK = "ASK"               # deliver the next main question (verbatim bank text)
    PROBE = "PROBE"           # deliver a follow-up (verbatim bank follow-up)
    CLARIFY = "CLARIFY"       # rephrase/explain the current question
    ACK_ADVANCE = "ACK_ADVANCE"  # brief neutral ack, then ask next
    REPEAT = "REPEAT"         # replay last question (mouth uses cached last question)
    REDIRECT = "REDIRECT"     # gently bring back on-topic
    HOLD = "HOLD"             # patience cue ("take your time")
    REASSURE = "REASSURE"     # calm a nervous candidate
    HINT = "HINT"             # technical nudge (brain-composed safe hint)
    ANSWER_META = "ANSWER_META"  # answer a logistics/role question (grounded)
    CONFIRM = "CONFIRM"       # reflect-to-confirm a garbled/ambiguous answer
    CLOSE = "CLOSE"           # warm close + next steps (terminal)


class DirectiveTone(StrEnum):
    WARM = "WARM"
    NEUTRAL = "NEUTRAL"
    ENCOURAGING = "ENCOURAGING"
    CALM = "CALM"


# Acts whose `say` is the VERBATIM bank text — the brain selects, never rewrites.
_SAY_REQUIRED: frozenset[DirectiveAct] = frozenset({DirectiveAct.ASK, DirectiveAct.PROBE})

# Structural no-leak backstop (DESIGN-SPEC §6/§12, doc 11). Plain lowercased
# substring matching — NOT intent classification, so the no-regex rule does not
# apply. The real guarantee is that the mouth holds no rubric; this catches a
# brain-authoring bug where evaluation text leaks into speakable fields. Keep the
# list tight to avoid false positives on natural speech.
FORBIDDEN_RUBRIC_TOKENS: tuple[str, ...] = (
    "positive_evidence",
    "red_flag",
    "red flag",
    "red flags",
    "rubric",
    "meets_bar",
    "meets bar",
    "below_bar",
    "below bar",
    "evaluation_hint",
    "signal_value",
    "we're looking for",
    "we are looking for",
    "what i'm listening for",
    "what we're scoring",
)


class RubricLeakError(ValueError):
    """A Directive's speakable text smelled like rubric/evaluation criteria."""


class Directive(BaseModel):
    """A single brain->mouth instruction. See DESIGN-SPEC §7 / doc 11."""

    model_config = {"frozen": False}

    id: str = Field(min_length=1, description="Unique id; used for supersession + audit.")
    turn_ref: str = Field(
        min_length=1,
        description="The candidate turn this was computed for (staleness guard).",
    )
    act: DirectiveAct
    say: str | None = Field(
        default=None,
        description=(
            "Verbatim speakable text. For ASK/PROBE this is the verbatim bank "
            "question/follow-up (brain selects, never rewrites). For other acts it "
            "is brain-composed safe text, or null when the mouth composes from "
            "`compose_hint`."
        ),
    )
    compose_hint: str | None = Field(
        default=None,
        description="SHORT, leak-safe styling for composed parts, or null.",
    )
    tone: DirectiveTone = DirectiveTone.NEUTRAL
    is_terminal: bool = Field(
        default=False, description="True only on CLOSE; session ends after delivery."
    )
    speculative: bool = Field(
        default=False, description="True = Option C pre-staged guess; discard if not confirmed."
    )
    supersedes: str | None = Field(
        default=None, description="id of a staged Directive this replaces, or null."
    )

    @model_validator(mode="after")
    def _validate_act_invariants(self) -> Directive:
        if self.act in _SAY_REQUIRED and not (self.say and self.say.strip()):
            raise ValueError(f"act {self.act.value} requires non-empty `say` (verbatim bank text)")
        if self.act is DirectiveAct.CLOSE and not self.is_terminal:
            raise ValueError("CLOSE directive must have is_terminal=True")
        if self.is_terminal and self.act is not DirectiveAct.CLOSE:
            raise ValueError("is_terminal=True is only valid on a CLOSE directive")
        haystack = " ".join(p for p in (self.say, self.compose_hint) if p).lower()
        for token in FORBIDDEN_RUBRIC_TOKENS:
            if token in haystack:
                raise RubricLeakError(
                    f"Directive {self.id} carries rubric-smelling text "
                    f"(token={token!r}); only speakable text may cross to the mouth"
                )
        return self
