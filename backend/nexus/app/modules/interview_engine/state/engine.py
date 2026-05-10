"""StateEngine — composes ledger + queue + claims + lifecycle.

Validates Judge output, applies state mutations, resolves Speaker input.
The firewall: never calls an LLM; pure deterministic Python.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.judge import (
    AdvancePayload, AcknowledgeNoExperiencePayload, ClarifyPayload,
    CoverageQuality, EndSessionPayload, JudgeOutput, NextAction, Observation,
    PoliteClosePayload, ProbePayload, PushBackPayload, RepeatPayload,
)
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.models.speaker import (
    InstructionKind, SpeakerInput,
)
from app.modules.interview_engine.state.checkpoint import EngineCheckpoint
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.ledger import (
    IllegalCoverageTransition, SignalLedger,
)
from app.modules.interview_engine.state.lifecycle import (
    LifecycleSnapshot, SessionLifecycle, SessionOutcome,
)
from app.modules.interview_engine.state.queue import (
    NoActiveQuestionError, QueueError, QuestionQueue,
)
from app.modules.interview_runtime import (
    KnockoutFailure, SessionConfig, TranscriptEntry,
)


# Phase 9.4 — matches "I don't know" intent variants at the head of a
# short utterance. Used by StateEngine to bump
# QuestionState.consecutive_dont_know_count. Permissive on what follows
# the "I don't know" head (variants like "I don't know how to answer",
# "I don't know what to say", "I'm not sure how to respond"), but
# bounded by an overall length cap (60 chars) so a long substantive
# answer that happens to start with "I don't know but..." does NOT
# match.
_DONT_KNOW_HEAD_REGEX: re.Pattern[str] = re.compile(
    r"^\s*("
    r"i\s*don'?t\s*know"
    r"|i'?m\s*not\s*sure"
    r"|i\s*have\s*no\s*idea"
    r"|no\s*idea"
    r"|don'?t\s*know"
    r")\b",
    re.IGNORECASE,
)
# Conjunction that signals the candidate is qualifying / continuing
# with a real answer ("I don't know off the top of my head, BUT I would
# look at..."). When the utterance contains one of these AFTER the
# I-don't-know head, treat as a substantive answer (no count bump).
_QUALIFYING_CONJUNCTIONS: re.Pattern[str] = re.compile(
    r"\b(but|however|though|although|that said|i would|i'?d|i'?ll|let me|let's)\b",
    re.IGNORECASE,
)
# Hard cap on total stripped length. A real "I don't know" utterance is
# almost always under this; longer utterances are answers that happen
# to start with "I don't know".
_DONT_KNOW_MAX_LEN: int = 60


def _is_dont_know_utterance(text: str | None) -> bool:
    """True iff the candidate's utterance is an "I don't know" variant.

    Three guards keep this conservative and avoid false-positives on
    substantive answers that happen to contain a "don't know" head:

      1. Must START with one of the I-don't-know head phrases.
      2. Total stripped length <= 60 chars (real I-don't-know is short).
      3. Must NOT contain a qualifying conjunction (but/however/I would
         /let me) anywhere — those mean the candidate is qualifying or
         continuing with a real answer.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _DONT_KNOW_MAX_LEN:
        return False
    if not _DONT_KNOW_HEAD_REGEX.match(stripped):
        return False
    if _QUALIFYING_CONJUNCTIONS.search(stripped):
        return False
    return True


@dataclass(slots=True)
class StateEngineConfig:
    claims_pool_max: int = 50
    # Default mirrors the tenant_settings default flipped in migration 0030.
    # When the entrypoint passes through a tenant override it wins; this
    # is just the safe-by-default fallback used by tests.
    knockout_policy: Literal["record_only", "close_polite"] = "close_polite"


@dataclass(slots=True)
class ValidationWarning:
    code: str
    level: Literal["warning", "error"] = "warning"
    details: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class StateEngineDecision:
    """What the orchestrator receives after process_judge_output."""

    speaker_input: SpeakerInput
    cached_utterance: str | None = None  # set when instruction_kind == repeat
    cached_source_turn_id: str | None = None
    validation_warnings: list[ValidationWarning] = field(default_factory=list)
    lifecycle_state: str = "active"


class StateEngine:
    """Composes ledger + queue + claims + lifecycle. Drives all per-turn mutations."""

    # The repeat-cache eligibility set. When the candidate asks "repeat,"
    # the State Engine plays back the most recently cached agent
    # utterance — so this set defines which kinds count as "questions on
    # the table" the candidate could meaningfully be referring to.
    #
    # History:
    #
    # * Bug B (session 8317142f, turn 5): `_resolve_repeat` previously
    #   replayed the most recent agent utterance regardless of kind, so
    #   a candidate who said "repeat" right after a redirect heard the
    #   redirect again, not the actual question. Filter at insertion:
    #   only question-bearing kinds get cached.
    #
    # * Bug C (session 403e7d45, turns 2 + 5): the previous filter only
    #   admitted deliver_first_question / deliver_question / deliver_probe.
    #   When the agent's most recent utterance was a push_back ("which
    #   specific issue types would you define?") or a clarify ("Sure,
    #   let me rephrase. Imagine a client..."), the candidate's "repeat"
    #   replayed the bank's original Q1 instead — wrong question entirely.
    #   Both push_back and clarify ARE question-shaped (drilling sub-Q
    #   and rephrased main Q respectively), so they belong in the cache.
    #
    # Excluded (not questions):
    #   * redirect / repeat / acknowledge_no_experience / polite_close
    _QUESTION_KINDS: ClassVar[frozenset[InstructionKind]] = frozenset({
        InstructionKind.deliver_first_question,
        InstructionKind.deliver_question,
        InstructionKind.deliver_probe,
        InstructionKind.push_back,
        InstructionKind.clarify,
    })

    def __init__(
        self,
        *,
        session_config: SessionConfig,
        config: StateEngineConfig | None = None,
    ) -> None:
        self._cfg = session_config
        self._eng_cfg = config or StateEngineConfig()

        signal_values = [s.value for s in session_config.signal_metadata]
        self._ledger = SignalLedger(signal_values=signal_values)

        self._queue = QuestionQueue.from_initial(
            questions=[
                {
                    "question_id": q.id,
                    "is_mandatory": q.is_mandatory,
                    "follow_ups": q.follow_ups,
                }
                for q in session_config.stage.questions
            ],
        )

        self._claims = CandidateClaimsPool(max_size=self._eng_cfg.claims_pool_max)

        budget_seconds = session_config.stage.duration_minutes * 60
        self._lifecycle = SessionLifecycle(time_budget_total_seconds=budget_seconds)

        # Map signal_value → knockout flag for fast lookup during
        # process_judge_output. Populated once at construction; the
        # State Engine never mutates signal_metadata mid-session.
        self._knockout_signals: set[str] = {
            sig.value for sig in session_config.signal_metadata if sig.knockout
        }

        # Renamed from _agent_utterances. Holds question-bearing
        # utterances ONLY (deliver_first_question / deliver_question /
        # deliver_probe). The full transcript still lives on
        # self._transcript; this cache is the source of truth for
        # `repeat` replay.
        self._question_utterances: dict[str, str] = {}
        self._transcript: list[TranscriptEntry] = []
        self._turn_count = 0

    # --- Initialization ---

    def initialize_for_session_start(self) -> JudgeOutput:
        """Synthesize the first JudgeOutput: advance to position 0."""
        first = self._cfg.stage.questions[0]
        from app.modules.interview_engine.models.judge import TurnMetadata
        return JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id=first.id),
            turn_metadata=TurnMetadata(),
        )

    # --- Public mutation entry point ---

    def process_judge_output(
        self,
        *,
        turn_id: str,
        judge_output: JudgeOutput,
        candidate_utterance_text: str | None,
        elapsed_ms: int,
    ) -> StateEngineDecision:
        """Validate, mutate, resolve Speaker input."""
        warnings: list[ValidationWarning] = []
        self._turn_count += 1

        if self._lifecycle.snapshot().state.value == "pre_start":
            self._lifecycle.transition_to_active()

        # 1. Apply observations (drop on illegal transition).
        # Track which observations succeeded so the knockout-detection
        # pass below only counts observations that actually mutated the
        # ledger — an illegal-transition drop must NOT trigger a knockout.
        applied_observations: list[Observation] = []
        # `→failed` observations are only coherent when the Judge also
        # emitted an action that acknowledges the failure — anything else
        # is fabrication. Computed once per turn so we can drop bogus
        # failure obs that slipped through the schema (see Bug E below).
        failure_action_allowed = judge_output.next_action in (
            NextAction.acknowledge_no_experience,
            NextAction.polite_close,
        )
        for obs in judge_output.observations:
            transition = obs.coverage_transition.value
            # Hard invariant: ->failed transitions require the sentinel
            # anchor_id=-1 (per Judge prompt §4). Any ->failed observation with
            # a positive anchor is the Judge mis-classifying a positive answer
            # span as a no-experience disclosure (Bug C from session
            # 8317142f-3166-4236-a43c-18c8ab4592e1, turn 7). Drop without
            # applying — do NOT propagate into the ledger or knockout
            # detection. The illegal_failure_observation warning is recorded
            # for audit so the prompt drift is visible downstream.
            if transition.endswith("→failed") and obs.anchor_id != -1:
                warnings.append(ValidationWarning(
                    code="illegal_failure_observation",
                    level="warning",
                    details={
                        "signal": obs.signal_value,
                        "anchor_id": obs.anchor_id,
                        "transition": transition,
                        "reason": "failure transition requires sentinel anchor (-1)",
                    },
                ))
                continue
            # Bug E (session 33f044ce-fb25-4872-a85f-10c19fe7f253, turn 6):
            # candidate said "I didn't understand the question. Can you
            # please elaborate?" The Judge correctly emitted `clarify` BUT
            # also fabricated a `none→failed` observation on a knockout
            # signal with the candidate's clarification request as the
            # evidence quote. The State Engine recorded the knockout,
            # close_polite policy fired, and the session ended on a
            # candidate who was just asking for help.
            #
            # A `→failed` observation only makes sense when the Judge ALSO
            # picked an action that acknowledges the failure
            # (acknowledge_no_experience, polite_close). With any other
            # action — clarify, redirect, probe, repeat, advance — a
            # failure observation is incoherent: the Judge cannot
            # simultaneously decide "the candidate has no experience" and
            # "let's clarify and continue." Drop the observation;
            # preserve the action. The candidate gets the clarification
            # they asked for; the bogus knockout never enters the ledger.
            if transition.endswith("→failed") and not failure_action_allowed:
                warnings.append(ValidationWarning(
                    code="failure_obs_without_acknowledge_action",
                    level="warning",
                    details={
                        "signal": obs.signal_value,
                        "transition": transition,
                        "next_action": judge_output.next_action.value,
                        "reason": (
                            "→failed observation requires next_action in "
                            "{acknowledge_no_experience, polite_close}; got "
                            f"{judge_output.next_action.value!r}. Observation "
                            "dropped; action preserved."
                        ),
                    },
                ))
                continue
            try:
                self._ledger.apply_observation(
                    obs, turn_id=turn_id, recorded_at_ms=elapsed_ms,
                )
                if self._queue.active_state() is not None and obs.anchor_id >= 0:
                    self._queue.record_anchor_hit(anchor_id=obs.anchor_id)
                # Bookkeep per-quality counts on the active question so the
                # advance-gate can check "at least one concrete or strong
                # observation on this question" without re-walking the
                # ledger. Quality default is `concrete` for back-compat
                # with pre-v2 sessions and the synthesizer fallback.
                self._queue.record_quality_observation(quality=obs.quality.value)
                applied_observations.append(obs)
            except IllegalCoverageTransition as exc:
                warnings.append(ValidationWarning(
                    code="illegal_coverage_transition",
                    details={"signal": obs.signal_value, "reason": str(exc)},
                ))

        # 1a. Knockout detection: any successfully-applied observation that
        # ends in `?→failed` AND targets a knockout=True signal records a
        # KnockoutFailure on the lifecycle. Recording happens regardless of
        # policy — `record_only` still gets the audit trail. Policy decides
        # whether to *also* override the action below.
        knockout_failures_this_turn: list[KnockoutFailure] = []
        active_q_id = self._queue.active_question_id() or ""
        for obs in applied_observations:
            transition = obs.coverage_transition.value
            if not transition.endswith("→failed"):
                continue
            if obs.signal_value not in self._knockout_signals:
                continue
            failure = KnockoutFailure(
                question_id=active_q_id,
                # Use the verbatim evidence quote as the reason; the
                # KnockoutFailure validator runs _scrub_pii on this field
                # so emails/phone numbers don't leak into the persisted
                # record. 200-char clamp matches the persisted-summary
                # convention even though the schema allows up to 500.
                reason=obs.evidence_quote[:200],
                signal_values=[obs.signal_value],
                occurred_at_ms=elapsed_ms,
            )
            self._lifecycle.record_knockout(failure)
            knockout_failures_this_turn.append(failure)

        # 2. Apply claims (capped).
        for claim in judge_output.candidate_claims:
            self._claims.add(
                claim,
                captured_at_turn=self._turn_count,
                captured_at_seq=self._ledger.snapshot().next_seq,
            )

        # 3. Append to transcript (candidate utterance, if any).
        if candidate_utterance_text:
            active_qid = self._queue.active_question_id()
            self._transcript.append(TranscriptEntry(
                role="candidate", text=candidate_utterance_text,
                timestamp_ms=elapsed_ms, question_id=active_qid,
            ))

        # 4. Increment active question turn counters.
        if self._queue.active_state() is not None and candidate_utterance_text:
            self._queue.increment_active_turn(elapsed_ms=elapsed_ms)

        # 4a. Phase 9.4 — track consecutive "I don't know" utterances on
        # the active question. This count is surfaced to the Judge via
        # JudgeInputPayload.active_question_consecutive_dont_know_count
        # so the Judge can escalate to acknowledge_no_experience after
        # the first I-don't-know on an experience-class signal instead
        # of looping on clarify (the death-spiral pattern from session
        # f665498d turns 14-18). The streak resets on any non-I-don't-
        # know utterance.
        if self._queue.active_state() is not None and candidate_utterance_text:
            if _is_dont_know_utterance(candidate_utterance_text):
                self._queue.increment_active_dont_know_count()
            else:
                self._queue.reset_active_dont_know_count()

        # 5. Resolve next action with self-healing.
        action = judge_output.next_action
        # Q-2 (Phase 9.3) — track whether the resulting deliver_question
        # is the consequence of a cap-forced advance (push_back hit cap=2
        # and downgraded). The Speaker scaffold uses this to add a soft
        # topic-shift segue instead of jumping cold into the next question.
        is_post_cap_advance: bool = False
        # Q-3 (Phase 9.3) — when polite_close fires due to a knockout
        # policy override, populate this with the failed signal so the
        # Speaker scaffold can acknowledge the no-experience disclosure
        # inline before the canned close. Stays None on every other
        # close path (clean completion, candidate-ended, time_expired).
        closing_disclosure_signal: str | None = None

        if action == NextAction.advance:
            target = judge_output.next_action_payload.target_question_id
            # Quality gate (Phase 9.2): downgrade `advance` to `push_back`
            # when no observation on the active question has reached
            # `concrete` or `strong`. The Judge prompt §4.5 instructs the
            # model to emit push_back itself in this case; this gate is
            # the deterministic backstop. Skipped on the synthetic
            # session-start advance (no active question yet) and when the
            # cap is already reached (avoid infinite downgrade loops).
            quality_downgrade = (
                self._queue.active_state() is not None
                and not self._queue.active_has_quality_at_least_concrete()
                and self._queue.active_push_back_count() < 2
            )
            if quality_downgrade:
                warnings.append(ValidationWarning(
                    code="quality_gated_advance",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "original_target": target,
                        "downgraded_to": "push_back",
                        "reason_code": "missing_specifics",
                        "reason": (
                            "advance requires at least one observation on "
                            "the active question to have quality 'concrete' "
                            "or 'strong'; all observations were 'thin'. "
                            "Downgraded to push_back so the Speaker asks "
                            "for specifics. To unblock advance, the next "
                            "Judge turn must emit a concrete observation."
                        ),
                    },
                ))
                self._queue.increment_active_push_back_count()
                instruction = InstructionKind.push_back
                # Mutate the JudgeOutput in-place so build_speaker_input
                # picks up the synthetic push_back payload + reason_code.
                judge_output.next_action = NextAction.push_back
                judge_output.next_action_payload = PushBackPayload(
                    reason_code="missing_specifics",
                )
            else:
                try:
                    self._queue.advance_to(target, at_turn=self._turn_count)
                    instruction = self._first_or_continuing_instruction()
                except QueueError as exc:
                    warnings.append(ValidationWarning(
                        code="invalid_target_question_id",
                        details={"target": target, "reason": str(exc)},
                    ))
                    instruction = self._fallback_advance_to_next_pending(warnings)

        elif action == NextAction.probe:
            payload = judge_output.next_action_payload
            try:
                self._queue.apply_probe(probe_id=payload.probe_id, at_turn=self._turn_count)
                instruction = InstructionKind.deliver_probe
            except (QueueError, NoActiveQuestionError) as exc:
                warnings.append(ValidationWarning(
                    code="invalid_probe_id",
                    details={"probe_id": payload.probe_id, "reason": str(exc)},
                ))
                instruction = self._fallback_to_first_unused_probe(warnings)

        elif action == NextAction.clarify:
            instruction = InstructionKind.clarify

        elif action == NextAction.repeat:
            instruction, cached, source_turn = self._resolve_repeat(warnings)
            speaker_input = self._build_speaker_input(
                instruction_kind=instruction,
                judge_output=judge_output,
                candidate_utterance_text=candidate_utterance_text,
            )
            return StateEngineDecision(
                speaker_input=speaker_input,
                cached_utterance=cached,
                cached_source_turn_id=source_turn,
                validation_warnings=warnings,
                lifecycle_state=self._lifecycle.snapshot().state.value,
            )

        elif action == NextAction.acknowledge_no_experience:
            # Bug F (session 06013de7-8e33-4eb5-8edc-f67470aa8a64, turn 6):
            # the Judge mis-classified the candidate's substantive answer
            # ("I'd use shared schemes and pilots…") as a no-experience
            # disclosure, emitting acknowledge_no_experience with a
            # `→failed` observation that anchored to a positive
            # anchor_id=3. The existing illegal_failure_observation guard
            # dropped the bogus obs, but the action survived — so the
            # Speaker was asked to acknowledge a non-existent disclosure
            # against a candidate utterance showing actual experience.
            # The contradictory inputs caused the Speaker to emit nothing,
            # falling back to a canned re-statement.
            #
            # Inverse coupling guard: an acknowledge_no_experience action
            # is only coherent when at least one `→failed` observation
            # actually entered the ledger. If every failure obs got
            # dropped (or none was emitted), there is no failure to
            # acknowledge — downgrade to clarify so the Speaker rephrases
            # the question rather than fabricating an acknowledgement.
            failure_obs_applied = any(
                o.coverage_transition.value.endswith("→failed")
                for o in applied_observations
            )
            if not failure_obs_applied:
                warnings.append(ValidationWarning(
                    code="acknowledge_without_failure_obs",
                    level="warning",
                    details={
                        "original_action": action.value,
                        "downgraded_to": "clarify",
                        "reason": (
                            "acknowledge_no_experience requires at least one "
                            "surviving →failed observation; none applied this "
                            "turn (either none emitted, or all dropped by the "
                            "illegal_failure_observation guard). Downgrading "
                            "to clarify so the Speaker rephrases the question."
                        ),
                    },
                ))
                instruction = InstructionKind.clarify
            else:
                instruction = InstructionKind.acknowledge_no_experience

        elif action == NextAction.redirect:
            # The collapsed redirect action. Tone selection happens in the
            # Speaker via turn_metadata, which build_speaker_input threads
            # through ONLY for this kind.
            instruction = InstructionKind.redirect

        elif action == NextAction.push_back:
            # Phase 9.2 push_back: candidate engaged but answer was thin /
            # evasive / partial. No queue mutation, no probe consumption,
            # no ledger gating. The State Engine increments the per-question
            # push_back_count and enforces the cap=2 invariant.
            #
            # Cap behavior: a 3rd incoming push_back is downgraded to advance
            # (or polite_close if no mandatory remains) and emits the
            # ``push_back_cap_reached`` warning. This breaks loops on
            # candidates who genuinely cannot give specifics.
            current_count = self._queue.active_push_back_count()
            if current_count >= 2 and self._queue.active_state() is not None:
                warnings.append(ValidationWarning(
                    code="push_back_cap_reached",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "push_back_count": current_count,
                        "downgraded_to": "advance",
                        "reason": (
                            "Push_back cap (2) already reached on this "
                            "question; downgrading to advance to avoid "
                            "loops. Some candidates genuinely cannot give "
                            "concrete specifics — accept the partial "
                            "coverage and move on."
                        ),
                    },
                ))
                instruction = self._fallback_advance_to_next_pending(warnings)
                # Q-2: tell the deliver_question Speaker scaffold this
                # was a cap-forced topic shift so it adds a segue.
                is_post_cap_advance = True
            elif self._queue.active_state() is None:
                # Defensive: push_back without an active question is
                # incoherent. Treat as fallback advance to next pending.
                warnings.append(ValidationWarning(
                    code="push_back_without_active_question",
                    level="error",
                    details={"reason": "push_back requires an active question"},
                ))
                instruction = self._fallback_advance_to_next_pending(warnings)
            else:
                self._queue.increment_active_push_back_count()
                instruction = InstructionKind.push_back

        elif action == NextAction.polite_close:
            instruction = InstructionKind.polite_close
            self._lifecycle.set_last_outcome(
                SessionOutcome.knockout_closed
                if self._lifecycle.snapshot().has_knockout()
                else SessionOutcome.completed
            )
            self._lifecycle.transition_to_closing()

        elif action == NextAction.end_session:
            payload = judge_output.next_action_payload
            assert isinstance(payload, EndSessionPayload)
            allowed = (
                self._lifecycle.snapshot().has_knockout()
                or self._queue.all_mandatory_complete()
                or self._lifecycle.snapshot().time_exhausted()
                or payload.initiated_by == "candidate_initiated"
            )
            if not allowed:
                warnings.append(ValidationWarning(
                    code="end_session_not_allowed", level="error",
                    details={"reason": "no knockout, mandatory incomplete, time remaining"},
                ))
                instruction = self._fallback_advance_to_next_pending(warnings)
            else:
                instruction = InstructionKind.polite_close
                self._lifecycle.set_last_outcome(
                    SessionOutcome.candidate_ended
                    if payload.initiated_by == "candidate_initiated"
                    else SessionOutcome.completed
                )
                self._lifecycle.transition_to_closing()

        else:
            warnings.append(ValidationWarning(
                code="unhandled_next_action",
                details={"action": action.value},
            ))
            instruction = self._fallback_advance_to_next_pending(warnings)

        # 6. Policy override: knockout recorded this turn AND policy is
        # close_polite → force polite_close regardless of the Judge's
        # action choice. The audit envelope still shows the original
        # JudgeOutput for replay; only the orchestrator-facing decision
        # changes. `record_only` keeps the audit trail (the
        # KnockoutFailure was recorded above) but lets the interview
        # continue.
        if (
            knockout_failures_this_turn
            and self._eng_cfg.knockout_policy == "close_polite"
        ):
            warnings.append(ValidationWarning(
                code="knockout_policy_override",
                level="warning",  # not an error — this is correct enforcement
                details={
                    "policy": "close_polite",
                    "knockout_signals": [
                        str(f.signal_values[0])
                        for f in knockout_failures_this_turn
                    ],
                    "original_action": action.value,
                },
            ))
            instruction = InstructionKind.polite_close
            # Q-3: thread the failed signal so polite_close.txt can
            # acknowledge the disclosure ("Got it on Jira — thanks for
            # being upfront. We'll be in touch with next steps.") before
            # the canned close. Pick the first knockout failure's first
            # signal_value — typical case is a single failure per turn.
            closing_disclosure_signal = knockout_failures_this_turn[0].signal_values[0]
            self._lifecycle.set_last_outcome(SessionOutcome.knockout_closed)
            # Guard against double-transition: if the action was already
            # polite_close (or end_session, fallback_advance with no
            # remaining mandatory) the lifecycle has already moved to
            # closing.
            if self._lifecycle.snapshot().state.value == "active":
                self._lifecycle.transition_to_closing()

        speaker_input = self._build_speaker_input(
            instruction_kind=instruction,
            judge_output=judge_output,
            candidate_utterance_text=candidate_utterance_text,
            is_post_cap_advance=is_post_cap_advance,
            closing_disclosure_signal=closing_disclosure_signal,
        )
        return StateEngineDecision(
            speaker_input=speaker_input,
            validation_warnings=warnings,
            lifecycle_state=self._lifecycle.snapshot().state.value,
        )

    # --- Helpers ---

    def _first_or_continuing_instruction(self) -> InstructionKind:
        """deliver_first_question on the very first advance; deliver_question after."""
        if self._turn_count == 1:
            return InstructionKind.deliver_first_question
        return InstructionKind.deliver_question

    def _fallback_advance_to_next_pending(
        self, warnings: list[ValidationWarning]
    ) -> InstructionKind:
        """Self-heal: pick next pending mandatory; polite_close if none."""
        next_id = self._queue.next_pending_mandatory_id()
        if next_id is None:
            warnings.append(ValidationWarning(
                code="no_advance_target",
                details={"reason": "all mandatory complete"},
            ))
            self._lifecycle.set_last_outcome(SessionOutcome.completed)
            self._lifecycle.transition_to_closing()
            return InstructionKind.polite_close
        try:
            self._queue.advance_to(next_id, at_turn=self._turn_count)
        except QueueError:
            return InstructionKind.polite_close
        return self._first_or_continuing_instruction()

    def _fallback_to_first_unused_probe(
        self, warnings: list[ValidationWarning]
    ) -> InstructionKind:
        active = self._queue.active_state()
        if active is not None and active.probes_remaining_ids:
            self._queue.apply_probe(
                probe_id=active.probes_remaining_ids[0],
                at_turn=self._turn_count,
            )
            return InstructionKind.deliver_probe
        warnings.append(ValidationWarning(
            code="no_probes_remaining",
            details={"active": active.question_id if active else None},
        ))
        return self._fallback_advance_to_next_pending(warnings)

    def _resolve_repeat(
        self, warnings: list[ValidationWarning]
    ) -> tuple[InstructionKind, str | None, str | None]:
        if not self._question_utterances:
            warnings.append(ValidationWarning(
                code="repeat_without_prior_question",
                details={},
            ))
            return InstructionKind.clarify, None, None
        last_turn_id = list(self._question_utterances.keys())[-1]
        return InstructionKind.repeat, self._question_utterances[last_turn_id], last_turn_id

    # Mirror of orchestrator._RECENT_TURNS_WINDOW: cap the transcript slice
    # the Speaker LLM sees per turn. Kept consistent with the Judge-side
    # slice so neither call carries unbounded conversation history. The
    # State Engine still owns the FULL transcript for SessionResult / audit
    # — only the LLM-input slice is bounded.
    _RECENT_TURNS_WINDOW: ClassVar[int] = 8

    def _build_speaker_input(
        self,
        *,
        instruction_kind: InstructionKind,
        judge_output: JudgeOutput,
        candidate_utterance_text: str | None,
        is_post_cap_advance: bool = False,
        closing_disclosure_signal: str | None = None,
    ) -> SpeakerInput:
        """Build SpeakerInput with anti-leak guarantee — no rubric content ever."""
        from app.modules.interview_engine.speaker.input_builder import build_speaker_input
        active = self._queue.active_state()
        active_q_cfg = next(
            (q for q in self._cfg.stage.questions if active and q.id == active.question_id),
            None,
        )
        # Slice transcript: last N entries only (see _RECENT_TURNS_WINDOW).
        recent = (
            self._transcript[-self._RECENT_TURNS_WINDOW:]
            if len(self._transcript) > self._RECENT_TURNS_WINDOW
            else self._transcript
        )
        recent_openers = self._recent_agent_openers()
        return build_speaker_input(
            instruction_kind=instruction_kind,
            judge_output=judge_output,
            active_question=active_q_cfg,
            queue=self._queue,
            claims_pool=self._claims,
            recent_turns=recent,
            persona_name=self._persona_name(),
            last_candidate_utterance=candidate_utterance_text,
            candidate_name=self._cfg.candidate.name,
            recent_agent_openers=recent_openers,
            is_post_cap_advance=is_post_cap_advance,
            closing_disclosure_signal=closing_disclosure_signal,
        )

    # Q-1 (Phase 9.3) — number of recent agent utterances we extract opener
    # slugs from. 3 covers the windowed-anti-repetition behavior without
    # blowing prompt tokens. The Speaker uses these to vary its first
    # 2-4 words across consecutive non-contextual replies.
    _RECENT_OPENER_WINDOW: ClassVar[int] = 3
    # Words per opener slug. 4 captures "I hear you,", "Let's stay focused",
    # "Got it -" naturally without overfitting to specific phrasings.
    _OPENER_WORD_COUNT: ClassVar[int] = 4

    def _recent_agent_openers(self) -> list[str]:
        """Extract opener slugs from the last few agent utterances.

        Returns the first ``_OPENER_WORD_COUNT`` whitespace-tokens of each
        of the last ``_RECENT_OPENER_WINDOW`` agent transcript entries
        (oldest -> newest order). Used as the SpeakerInput
        ``recent_agent_openers`` payload for non-contextual kinds, where
        the full ``recent_turns`` list is dropped to save tokens but the
        Speaker still needs anti-repetition signal.
        """
        agent_turns = [t for t in self._transcript if t.role == "agent"]
        recent = agent_turns[-self._RECENT_OPENER_WINDOW:]
        out: list[str] = []
        for entry in recent:
            words = entry.text.strip().split()
            if not words:
                continue
            slug = " ".join(words[:self._OPENER_WORD_COUNT])
            out.append(slug)
        return out

    def _persona_name(self) -> str:
        return getattr(self, "_persona_name_override", None) or "the interviewer"

    def set_persona_name(self, name: str) -> None:
        self._persona_name_override = name

    # --- External hooks ---

    def register_agent_utterance(
        self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
    ) -> None:
        """Append the spoken utterance to the transcript; for question-
        bearing kinds also seed the repeat-cache.

        Phase 9.8 — the cache stores text directly. The opener prefetch
        architecture (orchestrator's parallel opener dispatch) means
        ``text`` is already opener-free by construction.
        """
        self._transcript.append(TranscriptEntry(
            role="agent", text=text, timestamp_ms=0,
            question_id=self._queue.active_question_id(),
        ))
        if instruction_kind in self._QUESTION_KINDS:
            self._question_utterances[turn_id] = text

    def register_agent_question_for_repeat(
        self, *, turn_id: str, text: str, instruction_kind: InstructionKind,
    ) -> None:
        """Update the repeat-cache. Only call this when the agent
        SUCCESSFULLY emitted a question-bearing utterance — empty text
        and non-question kinds are silently no-ops.

        The repeat cache (``_question_utterances``) holds the most
        recent good question text for ``NextAction.repeat`` resolution.
        Empty entries would cause silent-agent replays — strictly
        forbidden by the Phase 9.9 cache integrity contract.

        See spec ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
        §4.1 for the rationale.
        """
        if not text.strip():
            return
        if instruction_kind not in self._QUESTION_KINDS:
            return
        self._question_utterances[turn_id] = text

    # --- Snapshot accessors ---

    def next_pending_mandatory_id(self) -> str | None:
        """Public accessor used by JudgeService.next_pending_mandatory_resolver."""
        return self._queue.next_pending_mandatory_id()

    def transcript_snapshot(self) -> list[TranscriptEntry]:
        return [t.model_copy() for t in self._transcript]

    def ledger_snapshot(self) -> SignalLedgerSnapshot:
        return self._ledger.snapshot()

    def queue_snapshot(self) -> QuestionQueueSnapshot:
        return self._queue.snapshot()

    def claims_snapshot(self) -> ClaimsPoolSnapshot:
        return self._claims.snapshot()

    def lifecycle_snapshot(self) -> LifecycleSnapshot:
        return self._lifecycle.snapshot()

    def set_time_elapsed(self, seconds: float) -> None:
        """Public accessor for the orchestrator to update elapsed time per turn.

        Without this the lifecycle's ``time_elapsed_seconds`` stays at 0,
        which makes ``time_remaining_seconds()`` always return the full
        budget — and the frontend's ``time_remaining_seconds`` attribute
        appears stuck.
        """
        self._lifecycle.set_time_elapsed(seconds)

    # --- Checkpoint ---

    def to_checkpoint(self, *, last_audit_seq_flushed: int, captured_at_ms: int) -> EngineCheckpoint:
        return EngineCheckpoint(
            session_id=self._cfg.session_id,
            ledger=self.ledger_snapshot(),
            queue=self.queue_snapshot(),
            claims=self.claims_snapshot(),
            lifecycle=self.lifecycle_snapshot(),
            last_audit_seq_flushed=last_audit_seq_flushed,
            captured_at_ms=captured_at_ms,
        )

    @classmethod
    def from_checkpoint(
        cls, checkpoint: EngineCheckpoint, *, session_config: SessionConfig,
    ) -> "StateEngine":
        eng = cls(session_config=session_config)
        signal_values = [s.value for s in session_config.signal_metadata]
        eng._ledger = SignalLedger.from_snapshot(
            checkpoint.ledger, signal_values=signal_values,
        )
        eng._queue = QuestionQueue.from_snapshot(checkpoint.queue)
        eng._claims = CandidateClaimsPool.from_snapshot(
            checkpoint.claims, max_size=eng._eng_cfg.claims_pool_max,
        )
        eng._lifecycle = SessionLifecycle.from_snapshot(checkpoint.lifecycle)
        return eng
