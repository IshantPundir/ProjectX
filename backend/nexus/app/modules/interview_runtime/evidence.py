"""
Gen-3 Interview Engine — engine→report wire contract (append-only) and shared vocabulary enums.

This is the SINGLE SOURCE OF TRUTH for the SessionEvidence contract and all vocabulary enums
shared between the engine and report layers. The brain per-turn output (BrainTurnOutput,
SignalObservation, BrainMove) lives in the engine's own contracts module — not here.

DESIGN PRINCIPLES
  • APPEND-ONLY: the engine NEVER edits or deletes a prior note. Every utterance-level observation
    becomes an immutable `EvidenceNote`. A retraction is just a new note (stance=contradicts) that
    optionally links the note it walks back — both are kept. There are NO merge/overwrite rules; the
    engine's "accumulation" is literally: append notes + record what was asked + compute provenance.
  • COLLECTOR, NOT JUDGE: the engine records FACTS (what was said, what was asked, provenance). It
    emits NO coverage verdict and NO score — the report derives coverage/score from the full note set
    with whole-session context. (Overwriting/merging would itself be a judgment, so we don't.)
  • NEVER SKIP A QUESTION FOR COVERAGE: a dedicated question digs deeper than a passing mention, so
    cross-crediting only ADDS notes — it never causes a question to be skipped. Questions go un-asked
    ONLY by running out of time (`not_reached`).
  • PROVENANCE is first-class: the report MUST tell "asked and absent" (real negative) from "never
    reached" (no data). Provenance is a derived FACT, not a judgment, so the engine computes it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============================================================================
# Shared vocabulary
# ============================================================================

class SignalType(StrEnum):
    competency = "competency"
    experience = "experience"
    credential = "credential"
    behavioral = "behavioral"


class SignalPriority(StrEnum):
    required = "required"
    preferred = "preferred"


class CoverageState(StrEnum):
    """The brain's LIVE read of how much presence-evidence a signal has — used at runtime to choose
    probe-vs-advance. Emitted on BrainTurnOutput; held as ephemeral engine runtime state. NOT a score
    and NOT persisted as a verdict in SessionEvidence (the report derives coverage from the notes)."""
    none = "none"
    partial = "partial"
    sufficient = "sufficient"


class Provenance(StrEnum):
    """HOW a signal was approached over the session. Derived FACT (not a judgment). The single most
    important field for report accuracy — it disambiguates a real negative from missing data."""
    not_reached = "not_reached"        # never asked (ran out of time/budget) → NO DATA, not a negative
    asked_directly = "asked_directly"  # its own bank question was asked and produced supporting notes
    cross_credited = "cross_credited"  # supported only by answers to OTHER questions (no own-question note)
    probed_absent = "probed_absent"    # own question asked + fairly elicited; no support came → real negative


class EvidenceStance(StrEnum):
    supports = "supports"        # a note that the candidate HAS the signal
    contradicts = "contradicts"  # explicit disclaim ("I've never used X") / a retraction / evidence they lack it


class EvidenceTexture(StrEnum):
    """How specific the utterance was — a per-note OBSERVATION (drives the brain's live probe-vs-advance
    and gives the report a confidence cue). NOT a score and NOT a coverage verdict."""
    thin = "thin"          # generic, buzzwords, hypothetical "I would…", no real HOW
    concrete = "concrete"  # a real tool/example/action they actually did
    strong = "strong"      # concrete + tradeoffs / numbers / edge-cases


class CompletionReason(StrEnum):
    completed = "completed"            # ran the planned screen to the end
    knockout_close = "knockout_close"  # mandatory signal verified absent → early, warm close
    candidate_ended = "candidate_ended"
    unresponsive = "unresponsive"
    error = "error"


class QuestionOutcome(StrEnum):
    asked = "asked"
    not_reached = "not_reached"  # ran out of time/budget before reaching it (the ONLY reason a Q is un-asked)


class ThreadClosure(StrEnum):
    """For an `asked` question: HOW its thread ended (engine-inferred, deterministic).

    The load-bearing distinction for provenance: a FAIR elicitation (satisfied/tapped_out/absent)
    with no supporting notes → the signal is a real negative (`probed_absent`); a thread CUT by the
    time budget (truncated) → the signal stays `not_reached` (no data, NOT a negative).
    """
    satisfied = "satisfied"    # the brain advanced with the primary signal well-supported
    tapped_out = "tapped_out"  # fair elicitation, diminishing returns; support stayed thin/absent
    absent = "absent"          # fair elicitation surfaced a disclaim / evidence of absence
    truncated = "truncated"    # time budget / session end cut the thread before fair resolution


class Speaker(StrEnum):
    agent = "agent"
    candidate = "candidate"


class TimeSpan(BaseModel):
    """A [start, end] window in session-relative milliseconds."""
    model_config = ConfigDict(frozen=True)

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeSpan":
        if self.end_ms < self.start_ms:
            raise ValueError("TimeSpan.end_ms must be >= start_ms")
        return self


class Word(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


# ============================================================================
# SESSION EVIDENCE  (the engine→report contract — APPEND-ONLY)
# ============================================================================

class EvidenceNote(BaseModel):
    """One immutable, utterance-level evidence note — the atom of the append-only log.

    The engine appends one note per (utterance, signal-touched). It NEVER edits or deletes a note.
    A retraction is a new note with stance=contradicts and `retracts_seq` pointing at the earlier
    note (which stays in the log). Multi-signal answers produce several notes sharing a `turn_ref`.
    """
    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1, description="Monotonic append order across the whole session.")
    turn_ref: str
    signal: str = Field(description="The signal this note speaks to (one note per utterance×signal).")
    stance: EvidenceStance
    texture: EvidenceTexture
    quote: str = Field(min_length=1, description="Verbatim candidate words — the proof.")
    span: TimeSpan
    from_question_id: str = Field(description="Bank question on the floor when this was said.")
    via_probe: bool = Field(description="Elicited by a follow-up probe (True) vs the main question (False).")
    retracts_seq: int | None = Field(
        default=None, ge=1,
        description="If this note walks back an earlier claim, the `seq` of that note. The earlier "
                    "note is KEPT — the report weighs both (honest correction vs flip-flop is ITS call).",
    )


class SignalEvidence(BaseModel):
    """Thin per-signal index: identity + engine-derived provenance ONLY.

    There is deliberately NO coverage verdict and NO embedded evidence list here — the evidence lives
    in the append-only `SessionEvidence.notes` (filter by `signal`); the report derives coverage/score
    from those notes with full-session context. Identity is copied in so the report needs no JD join.
    """
    signal: str
    signal_type: SignalType
    weight: int = Field(ge=1, le=3)
    priority: SignalPriority
    knockout: bool
    provenance: Provenance  # derived FACT: not_reached | asked_directly | cross_credited | probed_absent


class QuestionRecord(BaseModel):
    """What the screen did with each bank question. Powers provenance + 'was this actually asked,
    and did we push for specifics?'. A question is only ever `asked` or `not_reached` (never skipped
    for coverage — a dedicated question always runs if reached)."""
    question_id: str
    primary_signal: str
    outcome: QuestionOutcome
    closure: ThreadClosure | None = Field(
        default=None,
        description="Set ONLY when outcome == asked. Engine-inferred from the brain's final "
                    "coverage_after + stance (and `truncated` when the session ended with the thread "
                    "still open). This is what lets the session-close pass tell `probed_absent` from `not_reached`.",
    )
    asked_at_turn: str | None = None
    probes_used: list[int] = Field(
        default_factory=list, description="Indices of the bank `follow_ups` that were actually fired.",
    )
    probes_available: int = Field(ge=0, description="How many follow-ups the bank offered for this question.")
    time_spent_s: float = Field(ge=0, default=0.0)


class KnockoutOutcome(BaseModel):
    """Recorded ONLY when a mandatory signal was VERIFIED absent (probe → all OR-alternatives
    checked → reflect-confirmed). The engine RECORDS; the report/human decides the consequence.
    The engine never auto-rejects (borderline → human)."""
    signal: str
    or_alternatives_checked: list[str] = Field(default_factory=list)
    reflect_confirmed: bool
    evidence_note_seqs: list[int] = Field(default_factory=list, description="seqs of the notes that ground this.")


class TranscriptTurn(BaseModel):
    """A word-timed turn. `pre_turn_gap_ms` is the cheap, IRRECOVERABLE signal the raw text loses
    (think-time before the candidate spoke); the report derives 'flatline / copilot' patterns from it
    across the session. The engine carries the timing but does NOT label it — collector, not judge."""
    turn_ref: str
    speaker: Speaker
    text: str
    span: TimeSpan
    pre_turn_gap_ms: int = Field(ge=0, description="Silence before this turn began (think-time).")
    words: list[Word] = Field(default_factory=list, description="Word-level timing (candidate turns).")
    question_id: str | None = Field(default=None, description="Bank question this turn relates to.")


class SessionMeta(BaseModel):
    session_id: str
    job_id: str
    candidate_id: str
    stage_id: str
    started_at: datetime
    ended_at: datetime
    duration_s: float = Field(ge=0)
    time_budget_s: float = Field(ge=0, description="The stage's planned time budget.")
    completion: CompletionReason
    questions_asked: int = Field(ge=0)


class SessionEvidence(BaseModel):
    """THE engine→report contract. Append-only notes + derived provenance + raw timing — everything
    the report needs to roll up coverage, score, and narrate WITH whole-session context. No grades,
    no coverage verdict, no bluff label — the report makes every judgment."""
    meta: SessionMeta
    signals: list[SignalEvidence]      # per-signal identity + derived provenance
    notes: list[EvidenceNote]          # APPEND-ONLY source of truth, chronological by `seq`
    questions: list[QuestionRecord]
    transcript: list[TranscriptTurn]
    knockout: KnockoutOutcome | None = None
