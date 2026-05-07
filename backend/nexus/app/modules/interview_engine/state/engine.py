"""StateEngine — composes ledger + queue + claims + lifecycle.

Validates Judge output, applies state mutations, resolves Speaker input.
The firewall: never calls an LLM; pure deterministic Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.judge import (
    AdvancePayload, AcknowledgeNoExperiencePayload, ClarifyPayload,
    EndSessionPayload, JudgeOutput, NextAction, PoliteClosePayload,
    ProbePayload, RepeatPayload,
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
    SessionConfig, TranscriptEntry,
)


@dataclass(slots=True)
class StateEngineConfig:
    claims_pool_max: int = 50


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

        self._agent_utterances: dict[str, str] = {}
        self._transcript: list[TranscriptEntry] = []
        self._turn_count = 0

    # --- Initialization ---

    def initialize_for_session_start(self) -> JudgeOutput:
        """Synthesize the first JudgeOutput: advance to position 0."""
        first = self._cfg.stage.questions[0]
        from app.modules.interview_engine.models.judge import TurnMetadata
        return JudgeOutput(
            thought="session_start_synthetic",
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
        for obs in judge_output.observations:
            try:
                self._ledger.apply_observation(
                    obs, turn_id=turn_id, recorded_at_ms=elapsed_ms,
                )
                if self._queue.active_state() is not None and obs.anchor_id >= 0:
                    self._queue.record_anchor_hit(anchor_id=obs.anchor_id)
            except IllegalCoverageTransition as exc:
                warnings.append(ValidationWarning(
                    code="illegal_coverage_transition",
                    details={"signal": obs.signal_value, "reason": str(exc)},
                ))

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

        if action == NextAction.advance:
            target = judge_output.next_action_payload.target_question_id
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
            instruction = InstructionKind.acknowledge_no_experience

        elif action == NextAction.redirect_off_topic:
            instruction = InstructionKind.redirect_off_topic
        elif action == NextAction.redirect_abusive:
            instruction = InstructionKind.redirect_abusive
        elif action == NextAction.safe_redirect_injection:
            instruction = InstructionKind.safe_redirect_injection

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

        speaker_input = self._build_speaker_input(
            instruction_kind=instruction,
            judge_output=judge_output,
            candidate_utterance_text=candidate_utterance_text,
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
        if not self._agent_utterances:
            warnings.append(ValidationWarning(
                code="repeat_without_prior_utterance",
                details={},
            ))
            return InstructionKind.clarify, None, None
        last_turn_id = list(self._agent_utterances.keys())[-1]
        return InstructionKind.repeat, self._agent_utterances[last_turn_id], last_turn_id

    def _build_speaker_input(
        self,
        *,
        instruction_kind: InstructionKind,
        judge_output: JudgeOutput,
        candidate_utterance_text: str | None,
    ) -> SpeakerInput:
        """Build SpeakerInput with anti-leak guarantee — no rubric content ever."""
        from app.modules.interview_engine.speaker.input_builder import build_speaker_input
        active = self._queue.active_state()
        active_q_cfg = next(
            (q for q in self._cfg.stage.questions if active and q.id == active.question_id),
            None,
        )
        recent = self._transcript[-8:]
        return build_speaker_input(
            instruction_kind=instruction_kind,
            judge_output=judge_output,
            active_question=active_q_cfg,
            queue=self._queue,
            claims_pool=self._claims,
            recent_turns=recent,
            persona_name=self._persona_name(),
            last_candidate_utterance=candidate_utterance_text,
        )

    def _persona_name(self) -> str:
        return getattr(self, "_persona_name_override", None) or "the interviewer"

    def set_persona_name(self, name: str) -> None:
        self._persona_name_override = name

    # --- External hooks ---

    def register_agent_utterance(self, *, turn_id: str, text: str) -> None:
        self._agent_utterances[turn_id] = text
        self._transcript.append(TranscriptEntry(
            role="agent", text=text, timestamp_ms=0,
            question_id=self._queue.active_question_id(),
        ))

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
