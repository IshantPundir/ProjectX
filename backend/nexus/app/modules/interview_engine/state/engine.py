"""StateEngine — composes ledger + queue + claims + lifecycle.

Validates Judge output, applies state mutations, resolves Speaker input.
The firewall: never calls an LLM; pure deterministic Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.judge import (
    AdvancePayload, AcknowledgeNoExperiencePayload, ClarifyPayload,
    CoverageQuality, CoverageTransition, EndSessionPayload, JudgeOutput,
    NextAction, Observation, PoliteClosePayload, ProbePayload, PushBackPayload,
    RepeatPayload,
)
from app.modules.interview_engine.models.ledger import (
    CoverageState, SignalLedgerSnapshot,
)
from app.modules.interview_engine.models.queue import (
    QuestionQueueSnapshot, QuestionState,
)
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
    KnockoutFailure, QuestionConfig, SessionConfig, SignalMetadata,
    TranscriptEntry,
)


def _maybe_promote_meta_confession(
    *,
    judge_output: JudgeOutput,
    active_question: QuestionConfig,
    question_state: QuestionState,
    remaining_probes: dict[str, str],
    ledger: SignalLedgerSnapshot,
    session_signal_metadata: list[SignalMetadata],
) -> ValidationWarning | None:
    """Bluff-catch promotion (v2, see spec 2026-05-17 §6).

    Trigger: candidate_meta_confession=true AND active question is
    mandatory AND push_back_count >= 1 AND no remaining probes on this
    question AND the question's primary signal (highest weight from
    session_signal_metadata filtered by question.signal_values) is
    uncovered (coverage in {none, partial}).

    The Judge classifies; the State Engine decides. The Judge's emitted
    action (typically push_back) is overridden to acknowledge_no_experience
    so the existing knockout-policy override and post-session report
    pipelines treat this exactly as an explicit no-experience disclosure.

    Returns a ValidationWarning with code="meta_confession_knockout" if
    promotion fires, or None if any guard fails.

    NOTE: this function is PURE — it inspects state but does NOT mutate
    judge_output, the ledger, or the lifecycle. The caller (StateEngine.
    process_judge_output) is responsible for applying the mutations when
    the return value is non-None.
    """
    if not judge_output.turn_metadata.candidate_meta_confession:
        return None
    if not active_question.is_mandatory:
        return None
    if question_state.push_back_count < 1:
        return None
    if remaining_probes:
        return None  # let probes run first
    # Find the primary signal: highest weight among the signals this
    # question targets, looked up in session_signal_metadata.
    question_signal_meta = [
        m for m in session_signal_metadata
        if m.value in active_question.signal_values
    ]
    if not question_signal_meta:
        return None  # no metadata to fail on; defensive
    primary_signal = max(question_signal_meta, key=lambda s: s.weight)
    snap = ledger.snapshots.get(primary_signal.value)
    if snap is not None and snap.coverage == CoverageState.sufficient:
        return None  # already proven; don't reverse that evidence
    return ValidationWarning(
        code="meta_confession_knockout",
        level="warning",
        details={
            "active_question_id": active_question.id,
            "original_action": judge_output.next_action.value,
            "promoted_to": NextAction.acknowledge_no_experience.value,
            "failed_signal_value": primary_signal.value,
            "push_back_count": question_state.push_back_count,
            "reason": (
                f"meta_confession + mandatory + "
                f"push_back_count={question_state.push_back_count} + "
                f"no_probes_remain + primary_signal_uncovered"
            ),
        },
    )


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
                    "signal_values": q.signal_values,
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
            reasoning="Session start: initializing by advancing to the first question in the bank.",
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

        # Capture per-signal coverage BEFORE any observations are applied
        # this turn. The Cluster G reverse-rule guard (step 6 below) needs
        # the pre-mutation coverage to detect whether a knockout signal was
        # already `sufficient` at the START of this turn — after applying
        # a `sufficient→failed` observation the snapshot would show `failed`,
        # causing the guard to miss the reverse-rule case.
        pre_turn_signal_snapshots = dict(self._ledger.snapshot().snapshots)

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

        # 5. Resolve next action with self-healing.
        action = judge_output.next_action
        # Q-2 (Phase 9.3) — track whether the resulting deliver_question
        # is the consequence of a cap-forced advance (push_back hit the
        # per-difficulty cap [easy 1 / medium 2 / hard 3] and downgraded).
        # The Speaker scaffold uses this to add a soft
        # topic-shift segue instead of jumping cold into the next question.
        is_post_cap_advance: bool = False
        # A2 (Option A) — set True when acknowledge_no_experience (or a
        # later promotion path) acknowledges the candidate bowing out AND
        # advances to the next question in the SAME turn. Threaded to the
        # deliver_question Speaker scaffold so it opens with a warm "fair
        # enough, let me try something different" before the next question.
        is_post_acknowledge: bool = False
        # Q-3 (Phase 9.3) — when polite_close fires due to a knockout
        # policy override, populate this with the failed signal so the
        # Speaker scaffold can acknowledge the no-experience disclosure
        # inline before the canned close. Stays None on every other
        # close path (clean completion, candidate-ended, time_expired).
        closing_disclosure_signal: str | None = None

        if action == NextAction.advance:
            target = judge_output.next_action_payload.target_question_id
            # Capture push_back_count BEFORE any queue mutation. When the
            # Judge picks advance VOLUNTARILY at the cap (session a13ec188
            # T8 reproducer: "With push-back already at the cap, I should
            # not continue looping; the cleanest move is to advance"), the
            # SE-forced downgrade branch below never fires — so without
            # this we miss the post-cap signal entirely. Speaker scaffold
            # needs the flag to emit the topic-shift segue ("Thanks for
            # that. Now —") instead of jumping cold.
            prior_push_back_count = (
                self._queue.active_push_back_count()
                if self._queue.active_state() is not None else 0
            )
            # Capture the cap for the question the candidate is LEAVING,
            # BEFORE advance_to mutates the active question. After the
            # mutation _push_back_cap() would resolve _active_difficulty()
            # against the NEW active question — the wrong one.
            prior_push_back_cap = self._push_back_cap()
            # Quality gate (Phase 9.2): downgrade `advance` to `push_back`
            # when no observation on the active question has reached
            # `concrete` or `strong`. The Judge prompt §4.5 instructs the
            # model to emit push_back itself in this case; this gate is
            # the deterministic backstop. Skipped on the synthetic
            # session-start advance (no active question yet) and when the
            # cap is already reached (avoid infinite downgrade loops).
            quality_downgrade = (
                self._queue.active_state() is not None
                and not self._advance_quality_met()
                and self._queue.active_push_back_count() < self._push_back_cap()
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
                    # Set the post-cap flag whenever the prior question
                    # had push_back_count at the cap, regardless of who
                    # chose advance (Judge or the SE-forced downgrade in
                    # the push_back branch below). Mirrors the existing
                    # is_post_cap_advance=True at the SE-downgrade site
                    # for consistency in Speaker behavior.
                    if prior_push_back_count >= prior_push_back_cap:
                        is_post_cap_advance = True
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
            still_confused = judge_output.turn_metadata.candidate_still_confused
            active_state = self._queue.active_state()
            prior_confused = active_state.still_confused_count if active_state else 0
            # Escalation: candidate has been generically confused on this
            # question twice already; the 3rd confusion (>= 2 prior) routes to
            # acknowledge-and-advance instead of a 3rd clarify (kills the
            # death-spiral). Two clarify attempts is the configured budget.
            if still_confused and prior_confused >= 2 and active_state is not None:
                warnings.append(ValidationWarning(
                    code="still_confused_cap_reached",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "still_confused_count": prior_confused,
                        "escalated_to": "acknowledge_and_advance",
                        "reason": (
                            "Candidate generically confused on this question "
                            "after 2 clarify attempts; acknowledging and moving "
                            "on rather than clarifying a 3rd time."
                        ),
                    },
                ))
                primary = self._primary_signal_value(active_state.question_id)
                instruction, is_post_acknowledge, closing_disclosure_signal = (
                    self._acknowledge_and_advance(
                        failed_signal_value=primary, elapsed_ms=elapsed_ms,
                        turn_id=turn_id, warnings=warnings,
                        knockout_failures_this_turn=knockout_failures_this_turn,
                    )
                )
            else:
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
                # Option A: acknowledge AND advance in one turn.
                failed_payload = judge_output.next_action_payload
                assert isinstance(failed_payload, AcknowledgeNoExperiencePayload)
                # Reverse-rule carve-out: if the candidate already PROVED this
                # signal (coverage was `sufficient` at the START of this turn)
                # and now disclaims it, do NOT route through Option A. The later
                # disclaimer contradicts proven evidence; the step-6 reverse-rule
                # guard keeps the session open on the strength of that proof.
                # Advancing/closing here would pre-empt that guard and discard a
                # proven knockout signal. Keep the legacy non-advancing
                # acknowledgment so step 6 can swallow any override.
                pre = pre_turn_signal_snapshots.get(failed_payload.failed_signal_value)
                already_sufficient = (
                    pre is not None and pre.coverage == CoverageState.sufficient
                )
                if already_sufficient:
                    instruction = InstructionKind.acknowledge_no_experience
                else:
                    instruction, is_post_acknowledge, closing_disclosure_signal = (
                        self._acknowledge_and_advance(
                            failed_signal_value=failed_payload.failed_signal_value,
                            elapsed_ms=elapsed_ms, turn_id=turn_id, warnings=warnings,
                            knockout_failures_this_turn=knockout_failures_this_turn,
                        )
                    )

        elif action == NextAction.redirect:
            # The collapsed redirect action. Tone selection happens in the
            # Speaker via turn_metadata, which build_speaker_input threads
            # through ONLY for this kind.
            instruction = InstructionKind.redirect

        elif action == NextAction.push_back:
            # Phase 9.5 (2026-05-12) — inverse quality gate. Mirror of
            # the existing quality_gated_advance check above. When the Judge
            # emits push_back paired with at least one `concrete`/`strong`
            # observation, the model is internally inconsistent: push_back
            # asks for more depth, but a concrete observation says depth
            # was already produced. Downgrade in-place rather than letting
            # this fall through to the (now-softened) JudgeOutput validator
            # which would otherwise route via the validation_error fallback
            # and force-advance the queue.
            has_concrete_obs = any(
                o.quality in (CoverageQuality.concrete, CoverageQuality.strong)
                for o in applied_observations
            )
            if has_concrete_obs and self._queue.active_state() is not None:
                active_q_state = self._queue.active_state()
                warnings.append(ValidationWarning(
                    code="inverse_quality_gate",
                    level="warning",
                    details={
                        "active_question_id": self._queue.active_question_id(),
                        "original_action": "push_back",
                        "downgraded_to": (
                            "deliver_probe"
                            if active_q_state.probes_remaining_ids
                            else "advance"
                        ),
                        "concrete_observations": [
                            {"signal": o.signal_value, "quality": o.quality.value}
                            for o in applied_observations
                            if o.quality in (
                                CoverageQuality.concrete, CoverageQuality.strong,
                            )
                        ],
                        "reason": (
                            "push_back is incoherent when paired with concrete/"
                            "strong observations (model produced depth but "
                            "asked for more). Downgraded to probe (or advance "
                            "if probes exhausted) to honor the evidence the "
                            "model already extracted."
                        ),
                    },
                ))
                if active_q_state.probes_remaining_ids:
                    first_probe_id = active_q_state.probes_remaining_ids[0]
                    self._queue.apply_probe(
                        probe_id=first_probe_id, at_turn=self._turn_count,
                    )
                    instruction = InstructionKind.deliver_probe
                else:
                    instruction = self._fallback_advance_to_next_pending(warnings)
            else:
                # Phase 9.2 push_back: candidate engaged but answer was thin
                # / evasive / partial. No queue mutation, no probe
                # consumption, no ledger gating. The State Engine increments
                # the per-question push_back_count and enforces the cap=2
                # invariant.
                #
                # Cap behavior: a 3rd incoming push_back is downgraded to
                # advance (or polite_close if no mandatory remains) and emits
                # the ``push_back_cap_reached`` warning. This breaks loops on
                # candidates who genuinely cannot give specifics.
                current_count = self._queue.active_push_back_count()
                cap = self._push_back_cap()
                if current_count >= cap and self._queue.active_state() is not None:
                    warnings.append(ValidationWarning(
                        code="push_back_cap_reached",
                        level="warning",
                        details={
                            "active_question_id": self._queue.active_question_id(),
                            "push_back_count": current_count,
                            "downgraded_to": "advance",
                            "reason": (
                                f"Push_back cap ({cap}) already reached on this "
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

        # 5a. meta_confession promotion (v2, spec 2026-05-17 §6).
        #
        # Runs AFTER the action dispatch (including inverse_quality_gate which
        # short-circuits on remaining probes) and BEFORE knockout_policy so the
        # promoted acknowledge_no_experience chains through the existing
        # record_only / close_polite logic unchanged.
        #
        # Condition: Judge classified candidate_meta_confession=true AND the
        # active question is mandatory AND push_back_count >= 1 AND no probes
        # remain AND the primary signal is still uncovered. When all five hold,
        # the Judge's emitted action is overridden to acknowledge_no_experience
        # with the primary signal as failed_signal_value. A synthetic →failed
        # observation is applied to the ledger so the acknowledge_without_
        # failure_obs guard passes, and a KnockoutFailure is recorded if the
        # primary signal is a knockout signal (so the knockout_policy block
        # below fires correctly for close_polite tenants).
        active_q_state = self._queue.active_state()
        active_q_cfg_for_meta = next(
            (
                q for q in self._cfg.stage.questions
                if active_q_state and q.id == active_q_state.question_id
            ),
            None,
        )
        if active_q_cfg_for_meta is not None and active_q_state is not None:
            remaining_probes_map = {
                pid: pid for pid in active_q_state.probes_remaining_ids
            }
            meta_warn = _maybe_promote_meta_confession(
                judge_output=judge_output,
                active_question=active_q_cfg_for_meta,
                question_state=active_q_state,
                remaining_probes=remaining_probes_map,
                ledger=self._ledger.snapshot(),
                session_signal_metadata=self._cfg.signal_metadata,
            )
            if meta_warn is not None:
                warnings.append(meta_warn)
                failed_signal_value = str(meta_warn.details["failed_signal_value"])
                # Override judge_output in-place so _build_speaker_input and the
                # knockout_policy block see the promoted action.
                judge_output.next_action = NextAction.acknowledge_no_experience
                judge_output.next_action_payload = AcknowledgeNoExperiencePayload(
                    failed_signal_value=failed_signal_value,
                )
                # Option A: the helper applies the synthetic ->failed obs to the
                # ledger, records + surfaces the KnockoutFailure (if knockout),
                # and advances to the next question (or polite_close if none).
                # The previously-inline synthetic-obs + knockout logic now lives
                # entirely in _acknowledge_and_advance.
                instruction, is_post_acknowledge, closing_disclosure_signal = (
                    self._acknowledge_and_advance(
                        failed_signal_value=failed_signal_value,
                        elapsed_ms=elapsed_ms, turn_id=turn_id, warnings=warnings,
                        knockout_failures_this_turn=knockout_failures_this_turn,
                    )
                )

        # 6. Policy override: knockout recorded this turn AND policy is
        # close_polite → force polite_close regardless of the Judge's
        # action choice. The audit envelope still shows the original
        # JudgeOutput for replay; only the orchestrator-facing decision
        # changes. `record_only` keeps the audit trail (the
        # KnockoutFailure was recorded above) but lets the interview
        # continue.
        #
        # Cluster G reverse-rule guard: if a candidate already proved a
        # knockout signal on a MANDATORY question (coverage=sufficient),
        # then later disclaims experience on a NON-MANDATORY question
        # that targets the SAME knockout signal, the earlier proof stands.
        # We record the contradiction for review but do NOT close the
        # session. Matches the equivalent guard in
        # _maybe_promote_meta_confession.
        if (
            knockout_failures_this_turn
            and self._eng_cfg.knockout_policy == "close_polite"
        ):
            # Use the PRE-TURN snapshot for the reverse-rule check. The
            # ledger has already applied `sufficient→failed` transitions
            # by the time we reach this block, so the post-turn snapshot
            # would show `failed` (not `sufficient`) and the guard would
            # never fire. The pre-turn snapshot reflects the coverage state
            # BEFORE this turn's observations, which is what we want:
            # "was this signal already proven before the candidate's current
            # contradiction?"
            actionable_failures = [
                f for f in knockout_failures_this_turn
                if not any(
                    pre_turn_signal_snapshots.get(sv) is not None
                    and pre_turn_signal_snapshots[sv].coverage == CoverageState.sufficient
                    for sv in f.signal_values
                )
            ]
            skipped_failures = [
                f for f in knockout_failures_this_turn
                if f not in actionable_failures
            ]

            if skipped_failures:
                warnings.append(ValidationWarning(
                    code="knockout_policy_reverse_rule_skipped",
                    level="warning",
                    details={
                        "skipped_signals": [
                            str(f.signal_values[0])
                            for f in skipped_failures
                        ],
                        "reason": "signal already proven sufficient on earlier turn",
                    },
                ))

            if actionable_failures:
                warnings.append(ValidationWarning(
                    code="knockout_policy_override",
                    level="warning",  # not an error — this is correct enforcement
                    details={
                        "policy": "close_polite",
                        "knockout_signals": [
                            str(f.signal_values[0])
                            for f in actionable_failures
                        ],
                        "original_action": action.value,
                    },
                ))
                instruction = InstructionKind.polite_close
                # Q-3: thread the failed signal so polite_close.txt can
                # acknowledge the disclosure ("Got it on Jira — thanks for
                # being upfront. We'll be in touch with next steps.") before
                # the canned close. Pick the first actionable failure's first
                # signal_value — typical case is a single failure per turn.
                closing_disclosure_signal = actionable_failures[0].signal_values[0]
                self._lifecycle.set_last_outcome(SessionOutcome.knockout_closed)
                # Guard against double-transition: if the action was already
                # polite_close (or end_session, fallback_advance with no
                # remaining mandatory) the lifecycle has already moved to
                # closing.
                if self._lifecycle.snapshot().state.value == "active":
                    self._lifecycle.transition_to_closing()

        # Track consecutive still-confused turns on the active question.
        # Driven by the Judge flag (LLM classification), not a regex. Reset on
        # any turn that is not a still-confused clarify, and naturally reset
        # when the question advances (the new active question starts at 0).
        if self._queue.active_state() is not None:
            if (
                action == NextAction.clarify
                and judge_output.turn_metadata.candidate_still_confused
                and instruction == InstructionKind.clarify
            ):
                self._queue.increment_active_still_confused_count()
            else:
                self._queue.reset_active_still_confused_count()

        speaker_input = self._build_speaker_input(
            instruction_kind=instruction,
            judge_output=judge_output,
            candidate_utterance_text=candidate_utterance_text,
            is_post_cap_advance=is_post_cap_advance,
            is_post_acknowledge=is_post_acknowledge,
            closing_disclosure_signal=closing_disclosure_signal,
        )
        return StateEngineDecision(
            speaker_input=speaker_input,
            validation_warnings=warnings,
            lifecycle_state=self._lifecycle.snapshot().state.value,
        )

    # --- Helpers ---

    def _active_difficulty(self) -> str:
        """Difficulty of the active question; 'medium' when unknown."""
        qid = self._queue.active_question_id()
        if qid is None:
            return "medium"
        q_cfg = next((q for q in self._cfg.stage.questions if q.id == qid), None)
        return getattr(q_cfg, "difficulty", None) or "medium"

    def _push_back_cap(self) -> int:
        """Per-difficulty push-back cap: easy=1, medium=2, hard=3."""
        return {"easy": 1, "medium": 2, "hard": 3}.get(self._active_difficulty(), 2)

    def _advance_quality_met(self) -> bool:
        """Whether the active question's observations clear the advance gate
        for its difficulty.

          easy   : always True (engaged answer advances; gate OFF)
          medium : >=1 concrete or strong
          hard   : >=1 strong OR >=2 concrete
        """
        difficulty = self._active_difficulty()
        if difficulty == "easy":
            return True
        if difficulty == "hard":
            return (
                self._queue.active_has_quality_at_least_strong()
                or self._queue.active_concrete_or_strong_count() >= 2
            )
        return self._queue.active_has_quality_at_least_concrete()

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

    def _acknowledge_and_advance(
        self,
        *,
        failed_signal_value: str,
        elapsed_ms: int,
        turn_id: str,
        warnings: list[ValidationWarning],
        knockout_failures_this_turn: list[KnockoutFailure],
    ) -> tuple[InstructionKind, bool, str | None]:
        """Option A: acknowledge the candidate bowing out AND move on in one turn.

        1. Record a synthetic ->failed observation on failed_signal_value
           ONLY if it is not already failed. Record a KnockoutFailure ONLY
           when this helper actually applied that synthetic failure: when the
           Judge already emitted the ->failed obs this turn, process_judge_output
           steps 1/1a already applied + recorded it, so recording again would
           double-count. When the helper does synthesize the failure, it also
           appends the KnockoutFailure to knockout_failures_this_turn so the
           step-6 close_polite override sees it (step 1a only appends
           Judge-emitted failures, which the meta/stuck promotion paths never
           produce).
        2. Advance to the next pending question -> deliver_question with
           is_post_acknowledge=True; OR polite_close (with the disclosure
           signal) when no pending question remains.

        Returns (instruction_kind, is_post_acknowledge, closing_disclosure_signal).
        """
        current = self._ledger.snapshot().snapshots.get(failed_signal_value)
        current_state = current.coverage if current is not None else CoverageState.none
        synthetic_applied = False
        if current_state != CoverageState.failed:
            transition = (
                CoverageTransition.partial_to_failed
                if current_state == CoverageState.partial
                else CoverageTransition.none_to_failed
            )
            try:
                self._ledger.apply_observation(
                    Observation(
                        signal_value=failed_signal_value,
                        anchor_id=-1,
                        evidence_quote="[acknowledge_and_advance: synthetic failure]",
                        coverage_transition=transition,
                    ),
                    turn_id=turn_id, recorded_at_ms=elapsed_ms,
                )
                synthetic_applied = True
            except IllegalCoverageTransition:
                pass
        # Record a KnockoutFailure ONLY when we synthesized the failure here.
        # If the Judge supplied the ->failed obs, process_judge_output step 1a
        # already recorded + appended it; recording again would double-count.
        if synthetic_applied and failed_signal_value in self._knockout_signals:
            failure = KnockoutFailure(
                question_id=self._queue.active_question_id() or "",
                reason=f"acknowledge_and_advance on {failed_signal_value!r}"[:200],
                signal_values=[failed_signal_value],
                occurred_at_ms=elapsed_ms,
            )
            self._lifecycle.record_knockout(failure)
            # Surface to this turn's local list so the step-6 knockout_policy
            # override sees a meta/stuck-promoted failure (step 1a only appends
            # Judge-emitted failures, which the meta/stuck paths never produce).
            knockout_failures_this_turn.append(failure)
        next_pending = self.next_pending_question()
        if next_pending is None:
            self._lifecycle.set_last_outcome(
                SessionOutcome.knockout_closed
                if self._lifecycle.snapshot().has_knockout()
                else SessionOutcome.completed
            )
            if self._lifecycle.snapshot().state.value == "active":
                self._lifecycle.transition_to_closing()
            return InstructionKind.polite_close, False, failed_signal_value
        next_id, _is_mandatory = next_pending
        # NOTE: under close_polite + knockout + a pending-next question, this
        # advance happens BEFORE step 6 may override the instruction to
        # polite_close. That leaves the next question marked asked-but-
        # undelivered — a known questions_asked report-rollup over-count. It is
        # inert while the reporting module is stubbed and should be reconciled
        # when reporting is built. Do NOT change the advance behavior to "fix"
        # it here; the advance is load-bearing for the non-overridden paths.
        try:
            self._queue.advance_to(next_id, at_turn=self._turn_count)
        except QueueError as exc:
            warnings.append(ValidationWarning(
                code="acknowledge_advance_failed",
                details={"target": next_id, "reason": str(exc)},
            ))
            return InstructionKind.polite_close, False, failed_signal_value
        return self._first_or_continuing_instruction(), True, None

    def _primary_signal_value(self, question_id: str) -> str:
        """Highest-weight signal among the question's signal_values.

        Falls back to the first signal_value, or empty string if none. Used by
        the still-confused escalation to pick which signal to mark failed."""
        q_cfg = next(
            (q for q in self._cfg.stage.questions if q.id == question_id), None,
        )
        if q_cfg is None or not q_cfg.signal_values:
            return ""
        meta = [m for m in self._cfg.signal_metadata if m.value in q_cfg.signal_values]
        if not meta:
            return q_cfg.signal_values[0]
        return max(meta, key=lambda s: s.weight).value

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
        is_post_acknowledge: bool = False,
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
        recent_starts = self._recent_reply_starts()
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
            recent_reply_starts=recent_starts,
            is_post_cap_advance=is_post_cap_advance,
            is_post_acknowledge=is_post_acknowledge,
            closing_disclosure_signal=closing_disclosure_signal,
            # Role-context payload: passed for every kind, but the
            # input_builder only stamps them onto SpeakerInput when
            # clarify_kind == role_context. Strips them everywhere else.
            role_context_job_title=self._cfg.job_title,
            role_context_hiring_company_name=getattr(
                self._cfg, "hiring_company_name", None,
            ),
            role_context_role_summary=getattr(
                self._cfg, "role_summary", None,
            ),
            role_context_jd_text=getattr(self._cfg, "jd_text", None),
        )

    # Anti-repetition signal: number of recent agent utterances we
    # extract the first-words slug from. 3 covers windowed anti-repetition
    # without blowing prompt tokens. The Speaker uses these to vary its
    # first 2-4 words across consecutive non-contextual replies.
    _RECENT_REPLY_WINDOW: ClassVar[int] = 3
    # Words per slug. 4 captures "I hear you,", "Let's stay focused",
    # "Got it -" naturally without overfitting to specific phrasings.
    _REPLY_START_WORD_COUNT: ClassVar[int] = 4

    def _recent_reply_starts(self) -> list[str]:
        """Extract first-N-words slugs from the last few agent utterances.

        Returns the first ``_REPLY_START_WORD_COUNT`` whitespace-tokens
        of each of the last ``_RECENT_REPLY_WINDOW`` agent transcript
        entries (oldest -> newest order). Used as the SpeakerInput
        ``recent_reply_starts`` payload for non-contextual kinds, where
        the full ``recent_turns`` list is dropped to save tokens but the
        Speaker still needs an anti-repetition signal.
        """
        agent_turns = [t for t in self._transcript if t.role == "agent"]
        recent = agent_turns[-self._RECENT_REPLY_WINDOW:]
        out: list[str] = []
        for entry in recent:
            words = entry.text.strip().split()
            if not words:
                continue
            slug = " ".join(words[:self._REPLY_START_WORD_COUNT])
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
        """Record an agent utterance to the transcript. ALWAYS appends
        regardless of text length — empty text is a valid historical
        fact (the agent emitted nothing on this turn, e.g. interrupted
        before any output).

        Does NOT update the repeat-cache. Use
        ``register_agent_question_for_repeat`` for that — the two
        intents are deliberately separated (Phase 9.9, see spec
        ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
        §4.1) so an interrupted/empty turn cannot poison the repeat
        cache and cause silent-agent replay.
        """
        self._transcript.append(TranscriptEntry(
            role="agent", text=text, timestamp_ms=0,
            question_id=self._queue.active_question_id(),
        ))

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
        """Public accessor used by JudgeService.next_pending_mandatory_resolver.

        .. deprecated::
            Use ``next_pending_question()`` instead. This method returns
            only mandatory question IDs; ``next_pending_question()`` also
            returns non-mandatory questions when the mandatory queue is
            exhausted and uncovered signals remain (Cluster G).
        """
        return self._queue.next_pending_mandatory_id()

    def next_pending_question(self) -> tuple[str, bool] | None:
        """Return (question_id, is_mandatory) of the next question to ask.

        Public accessor used by the orchestrator to populate
        JudgeInputPayload.next_pending_question_id + next_pending_question_is_mandatory.

        Selection rule: mandatory pending first (position order), then
        non-mandatory pending in position order if any of their signals
        still have coverage in {none, partial}. Returns None when no
        question qualifies (→ polite_close).
        """
        return self._queue.next_pending_question_id(
            signal_coverage=self._ledger.snapshot().snapshots,
        )

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

    def turn_count_snapshot(self) -> int:
        return self._turn_count

    # --- Full snapshot / restore (2026-05-17 conversational-continuation design) ---
    #
    # snapshot_full + restore_from let the orchestrator wrap an entire turn
    # (Judge → State Engine → Speaker) in a two-phase commit. The orchestrator
    # takes a snapshot at the top of on_user_turn_completed; mutations during
    # the turn happen on the live engine; if the cancellation watcher fires
    # before the commit point (first TTS audio frame), the orchestrator calls
    # restore_from(snapshot) and the pre-turn state is back, byte-identical.
    #
    # Implementation: the existing EngineCheckpoint already serializes
    # ledger / queue / claims / lifecycle. v2 (added 2026-05-17) extends it
    # with turn_count + transcript + question_utterances so the snapshot
    # captures EVERY field process_judge_output mutates. The restore path
    # rebuilds the four sub-state machines via their from_snapshot
    # constructors and re-assigns the three scalar/collection fields.

    def snapshot_full(
        self,
        *,
        last_audit_seq_flushed: int = 0,
        captured_at_ms: int = 0,
    ) -> EngineCheckpoint:
        """Capture all mutable in-process state into an EngineCheckpoint.

        The audit/timing fields default to 0 because in-turn snapshots are
        forensic-only — the orchestrator does not persist them. Crash
        recovery still goes through ``to_checkpoint`` which the caller
        supplies real values for.
        """
        return EngineCheckpoint(
            schema_version=2,
            session_id=self._cfg.session_id,
            ledger=self.ledger_snapshot(),
            queue=self.queue_snapshot(),
            claims=self.claims_snapshot(),
            lifecycle=self.lifecycle_snapshot(),
            last_audit_seq_flushed=last_audit_seq_flushed,
            captured_at_ms=captured_at_ms,
            turn_count=self._turn_count,
            transcript=[t.model_copy() for t in self._transcript],
            question_utterances=dict(self._question_utterances),
        )

    def restore_from(self, checkpoint: EngineCheckpoint) -> None:
        """Atomically replace all mutable state with the checkpoint's contents.

        After this call ``self`` is byte-identical to the state at the
        time ``snapshot_full`` was taken. Wiring (config, knockout_signals,
        persona_name_override) is preserved — only data state is replaced.

        Used by the orchestrator's pre-Speaker cancellation path to wipe
        in-turn mutations cleanly. Safe to call repeatedly with the same
        checkpoint.
        """
        signal_values = [s.value for s in self._cfg.signal_metadata]
        self._ledger = SignalLedger.from_snapshot(
            checkpoint.ledger, signal_values=signal_values,
        )
        self._queue = QuestionQueue.from_snapshot(checkpoint.queue)
        self._claims = CandidateClaimsPool.from_snapshot(
            checkpoint.claims, max_size=self._eng_cfg.claims_pool_max,
        )
        self._lifecycle = SessionLifecycle.from_snapshot(checkpoint.lifecycle)
        self._turn_count = checkpoint.turn_count
        self._transcript = [t.model_copy() for t in checkpoint.transcript]
        self._question_utterances = dict(checkpoint.question_utterances)

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
