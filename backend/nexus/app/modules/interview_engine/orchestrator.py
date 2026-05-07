"""InterviewOrchestrator — drives the per-turn pipeline.

This is the LiveKit hook surface. on_enter delivers the first question via a
synthesized JudgeOutput. on_user_turn_completed runs Judge → State Engine →
Speaker on each candidate turn. on_close builds the SessionResult.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.modules.interview_engine.audit_events import (
    FrontendAttributePayload, JudgeSyntheticPayload,
    SpeakerCallPayload, SpeakerOutputPayload,
    TurnCompletedPayload, TurnStartedPayload,
)
from app.modules.interview_engine.event_kinds import (
    FRONTEND_ATTRIBUTE_PUBLISHED, JUDGE_SYNTHETIC,
    SPEAKER_CALL, SPEAKER_OUTPUT, TURN_COMPLETED, TURN_STARTED,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_TIME_REMAINING_SECONDS,
    ATTR_TOTAL_QUESTIONS, AttributePublisher,
)
from app.modules.interview_engine.judge.service import JudgeService
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.speaker.service import SpeakerService
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_runtime import SessionConfig


@dataclass(slots=True)
class OrchestratorConfig:
    recent_turns_window: int = 8
    checkpoint_turns: int = 10
    checkpoint_seconds: int = 30


class InterviewOrchestrator:
    def __init__(
        self,
        *,
        session_config: SessionConfig,
        tenant_settings: Any,
        state_engine: StateEngine,
        judge: JudgeService,
        speaker: SpeakerService,
        attr_publisher: AttributePublisher,
        event_collector: EventCollector,
        correlation_id: str,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._cfg = session_config
        self._tenant = tenant_settings
        self._state = state_engine
        self._judge = judge
        self._speaker = speaker
        self._attr = attr_publisher
        self._collector = event_collector
        self._correlation_id = correlation_id
        self._config = config or OrchestratorConfig()
        self._turn_index = -1  # incremented to 0 on session-start synthetic turn
        self._session_started_monotonic: float | None = None

    # --- LiveKit lifecycle hooks ---

    async def on_enter(self, agent: Any) -> None:
        self._session_started_monotonic = time.monotonic()
        turn_id = str(uuid.uuid4())
        self._turn_index += 1

        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=None, stt_text_used=None,
        ).model_dump())

        synthetic = self._state.initialize_for_session_start()
        self._append(JUDGE_SYNTHETIC, JudgeSyntheticPayload(
            turn_id=turn_id, output=synthetic.model_dump(mode="json"),
            reason="session_start",
        ).model_dump())

        decision = self._state.process_judge_output(
            turn_id=turn_id, judge_output=synthetic,
            candidate_utterance_text=None, elapsed_ms=0,
        )

        # Determine total questions (located on stage.questions per the actual schema).
        total_questions = len(self._cfg.stage.questions)
        await self._publish_attributes(
            turn_id=turn_id,
            current_question_index=self._state.queue_snapshot().active_index or 0,
            total_questions=total_questions,
            time_remaining_seconds=int(
                self._state.lifecycle_snapshot().time_remaining_seconds()
            ),
        )

        await self._stream_speaker_and_say(
            agent=agent, turn_id=turn_id,
            speaker_input=decision.speaker_input,
        )

        self._append(TURN_COMPLETED, TurnCompletedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            duration_ms=int((time.monotonic() - self._session_started_monotonic) * 1000),
        ).model_dump())

    async def on_user_turn_completed(self, agent: Any, turn_ctx: Any, new_message: Any) -> None:
        # Implementation in Task 8.3.
        raise NotImplementedError("on_user_turn_completed implemented in Task 8.3")

    async def on_close(self, agent: Any, audio_tuning_summary: dict | None) -> Any:
        # Implementation in Task 8.5.
        raise NotImplementedError("on_close implemented in Task 8.5")

    # --- Internals ---

    async def _stream_speaker_and_say(
        self, *, agent: Any, turn_id: str, speaker_input: Any,
    ) -> str:
        handle = await self._speaker.stream(
            turn_id=turn_id, speaker_input=speaker_input,
            correlation_id=self._correlation_id,
            tenant_id=str(self._cfg.session_id),
        )
        stream = handle.stream()
        await agent.session.say(stream, allow_interruptions=True, add_to_chat_ctx=True)
        final_text = await handle.final_text()

        self._append(SPEAKER_CALL, SpeakerCallPayload(
            turn_id=turn_id, model="speaker",
            prompt_hash="sha256:speaker",
            instruction_kind=speaker_input.instruction_kind.value,
            bank_text_present=speaker_input.bank_text is not None,
            latency_ms_first_token=handle.latency_ms_first_token,
            latency_ms_total=handle.latency_ms_total,
            usage=handle.usage,
            final_utterance=final_text,
        ).model_dump())
        self._append(SPEAKER_OUTPUT, SpeakerOutputPayload(
            turn_id=turn_id, final_utterance=final_text,
        ).model_dump())

        # Register the agent utterance with State Engine for repeat support.
        self._state.register_agent_utterance(turn_id=turn_id, text=final_text)
        return final_text

    async def _publish_attributes(
        self, *, turn_id: str | None,
        current_question_index: int | None = None,
        total_questions: int | None = None,
        time_remaining_seconds: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if total_questions is not None:
            kwargs[ATTR_TOTAL_QUESTIONS] = total_questions
        if current_question_index is not None:
            kwargs[ATTR_CURRENT_QUESTION_INDEX] = current_question_index
        if time_remaining_seconds is not None:
            kwargs[ATTR_TIME_REMAINING_SECONDS] = time_remaining_seconds
        pushed = await self._attr.publish(**kwargs)
        for k, v in pushed.items():
            self._append(FRONTEND_ATTRIBUTE_PUBLISHED, FrontendAttributePayload(
                turn_id=turn_id, attribute_name=k, value=v,
            ).model_dump())

    def _append(self, kind: str, payload: dict) -> None:
        wall_ms = int(time.time() * 1000)
        self._collector.append(kind=kind, payload=payload, wall_ms=wall_ms)
