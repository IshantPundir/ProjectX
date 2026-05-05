"""StructuredInterviewAgent — the deterministic-flow LiveKit Agent.

Phase C drives candidate utterances via:
1. Pattern 2 hard guardrail — `on_user_turn_completed` raises
   `StopResponse` (cancels the framework's auto-reply) and `llm_node`
   is overridden to emit zero chunks (defense-in-depth for any path
   that reaches it).
2. The orchestrator's main loop drives every agent utterance through
   the `SpeechAgent` rendering service. Each utterance is pre-rendered
   via `deliveries.render_*` into `self._pending_next_render` BEFORE
   the prior `_say` call, then drained at the consumption site via
   `_consume_pending_or_render`. The helper is the SOLE catch site for
   `SpeechRenderError`: it catches both synchronous render-time errors
   (template_not_found / placeholder_missing) AND asynchronous
   infrastructure errors raised by `handle.ready_to_commit()`
   (openai_timeout / openai_5xx /
   openai_connection_dropped_pre_first_token / openai_429), substituting
   a `StaticFallbackHandle` (built by `deliveries.fallback_for`) with
   the same render_id so the SPEECH_FALLBACK_USED + SPEECH_RENDERED
   envelope events correlate (spec §4.5).
3. `_say(handle)` receives a handle that has ALREADY passed
   `ready_to_commit()` (the helper awaits it before returning). It just
   pipes `handle.commit()`'s AsyncIterable[str] into `session.say()`.
   The handle's internal Task drains alongside session.say's consumer;
   SPEECH_RENDERED fires from inside the handle.
4. Wait for the framework's turn-detector-confirmed end-of-utterance
   (resolved via `on_user_turn_completed`), treat the full turn text
   as substantive, advance.

Phase C scope (no Sufficiency Checker, no Intent Classifier, no Disclaim
Classifier, no follow-ups, no deepening probes, no silence handling
beyond LiveKit's defaults, no reconnect protocol):
* Linear walk through `config.stage.questions` in position order.
* Always `asked_mode="standard"`.
* Three reachable exit modes: `completed` (all questions asked),
  `candidate_disconnected` (participant disconnect handler), `error`
  (unhandled exception). Two wired-but-unreachable modes: `KNOCKOUT_EXIT`
  and `CANDIDATE_INITIATED_EXIT` (their trigger paths land in H / I).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from livekit.agents import Agent, ChatContext, ChatMessage
from livekit.agents.llm import StopResponse

from app.modules.interview_engine.event_kinds import (
    ORCHESTRATOR_EXIT,
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
)
from app.modules.interview_engine.event_log import EventCollector, EventLogSink
from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewPhase,
    InterviewState,
    LedgerPersistence,
    QuestionState,
    SignalLedger,
    pick_next_question,
)
from app.modules.interview_engine.speech import (
    SpeechAgent,
    SpeechRenderError,
    SpeechRenderHandle,
)
from app.modules.interview_engine.speech import deliveries
from app.modules.interview_runtime import (
    QuestionConfig,
    QuestionResult,
    SessionConfig,
    SessionResult,
    TranscriptEntry,
)

log = structlog.get_logger("interview-engine.structured-agent")

INERT_SYSTEM_PROMPT = "Wait for explicit instructions. Do not speak unless told."

SessionOutcome = Literal[
    "completed",
    "candidate_ended",
    "candidate_disconnected",
    "error",
]


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _wall_ms() -> int:
    return int(time.time() * 1000)


class StructuredInterviewAgent(Agent):
    """Phase B structured interview agent.

    Owns the InterviewState, SignalLedger (no-op-updated in Phase B),
    LedgerPersistence, and the EventCollector reference for envelope
    emission. Inert system prompt + llm_node override + single
    utterance entry point implement the three-layer guardrail from
    spec §3.1.
    """

    def __init__(
        self,
        *,
        config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        persistence: LedgerPersistence,
        speech_agent: SpeechAgent,
    ) -> None:
        self._config = config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._persistence = persistence
        self._speech_agent = speech_agent
        self._envelope_written: bool = False
        self._persisted: bool = False
        self._end_outcome: SessionOutcome | None = None
        self._session_start_monotonic: float = time.monotonic()
        self._main_loop_task: asyncio.Task[None] | None = None

        # Per-question candidate transcripts, keyed by question_id.
        # Built up during the main loop; folded into SessionResult on close.
        self._candidate_transcripts: dict[str, str] = {}

        # Phase C — pre-render slot. At each pre-render trigger site
        # (intro pre-roll in on_enter, Q0 at INTRO→MAIN_LOOP boundary,
        # Qn+1 + wrap inside _ask_one_question), the orchestrator spawns
        # an asyncio.Task that calls `deliveries.render_*` and stashes
        # the resulting Future-of-handle here. `_consume_pending_or_render`
        # is the central drain point: it awaits the slot, catches
        # SpeechRenderError, and substitutes a fallback handle via
        # `deliveries.fallback_for` reusing the failed render's
        # render_id (spec §4.5).
        self._pending_next_render: asyncio.Task[SpeechRenderHandle] | None = None

        # The orchestrator's pending "next user turn" future, set by
        # `_arm_user_turn` immediately before each `_say` and resolved
        # by `on_user_turn_completed` when the framework's turn detector
        # confirms end-of-utterance. See `on_user_turn_completed` for
        # the rationale on EOU vs STT-`is_final` (smoke gate
        # 8cbc0ff4-...).
        self._next_user_turn_future: asyncio.Future[str] | None = None

        # Initialize state + ledger from SessionConfig.
        # job_id and candidate_id come from the C2 SessionConfig fields;
        # missing values raise here at construction time — the correct
        # fail-loud boundary for the wire-format identity invariant.
        target_duration_seconds = config.stage.duration_minutes * 60
        self._state = InterviewState(
            session_id=config.session_id,
            tenant_id=str(tenant_id),
            job_id=config.job_id,
            candidate_id=config.candidate_id,
            target_duration_seconds=target_duration_seconds,
            started_at=_now_utc(),
            questions=[
                QuestionState(
                    question_id=q.id,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                )
                for q in config.stage.questions
            ],
            prompt_versions={
                "speech_agent.intro": "v1",
                "speech_agent.ask_question_standard": "v1",
                "speech_agent.wrap_normal": "v1",
            },
            model_versions={},  # populated in Phase C+ when LLM roles are wired
        )
        self._ledger = SignalLedger.from_metadata(config.signal_metadata)

        super().__init__(instructions=INERT_SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    # Layer 1a — hard guardrail + turn-detector EOU bridge.
    #
    # Two responsibilities, both load-bearing for Phase B's UX:
    #
    # 1. Cancel the framework's auto-reply (`StopResponse`). Without
    #    this, every user turn fires
    #    `AgentSession._generate_reply`, which schedules a competing
    #    SpeechHandle that races with the orchestrator's
    #    `session.say()`. (Smoke gate 2c226524-...: silenced Q2+.)
    #
    # 2. Resolve the orchestrator's pending "next user turn" future
    #    using the framework's turn-detector-confirmed end-of-utterance
    #    signal — NOT raw STT `is_final` events. The previous
    #    implementation listened to `user_input_transcribed`
    #    `is_final=True`, but Deepgram fires `is_final=True` after
    #    every ~1s pause, not at end-of-turn. The candidate's
    #    thinking pauses caused premature advancement: Q0 cut at
    #    "...map business systems to" (smoke gate 8cbc0ff4-...).
    #
    #    The framework's turn detector (`MultilingualModel`) consumes
    #    STT output + dynamic endpointing + EOU context to decide when
    #    the user is REALLY done. `on_user_turn_completed` fires only
    #    after EOU is confirmed; `new_message.text_content` is the
    #    full turn text, not a fragment.
    #
    # Pattern lifted from `examples/voice_agents/push_to_talk.py` and
    # the transcriber/translator examples — `StopResponse` is the
    # documented mechanism for "transcribe but don't auto-respond"
    # agents.
    # ------------------------------------------------------------------
    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        del turn_ctx
        text = (new_message.text_content or "").strip()
        # Resolve the orchestrator's pending future, if armed.
        future = self._next_user_turn_future
        if future is not None and not future.done():
            future.set_result(text)
        # Cancel the auto-reply regardless. The orchestrator drives
        # every utterance via `session.say()`; the framework must not
        # generate its own reply.
        raise StopResponse()

    # ------------------------------------------------------------------
    # Layer 1b — hard guardrail (token-level). Even with auto-reply
    # cancelled, an explicit `session.generate_reply(...)` call (or
    # any other path that reaches `llm_node`) must produce zero chunks.
    # Belt-and-suspenders against future code that grows a path to
    # `generate_reply`. Verified against livekit-agents>=1.5.4
    # examples/voice_agents/structured_output.py.
    # ------------------------------------------------------------------
    async def llm_node(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return
        # async-generator contract: yield is unreachable but required to
        # make this an async generator (Pattern 2 hard guardrail; see
        # livekit-agents/examples/voice_agents/structured_output.py).
        # mypy --strict at the pinned version does not warn on the
        # unreachable yield, so no `type: ignore[unreachable]` is needed.
        yield

    # ------------------------------------------------------------------
    # Layer 3 — single utterance entry point. AST-invariant test (Task 8)
    # asserts session.say(...) is only called from inside this method.
    #
    # `allow_interruptions=True` (default). Candidates can barge in on
    # the agent — natural voice UX. Interruptions are processed by the
    # framework's turn detector + endpointing pipeline and surface as
    # `on_user_turn_completed` once the candidate's turn is fully done.
    # The orchestrator's main loop arms `_arm_user_turn` BEFORE the
    # `_say`, so a candidate-initiated interruption that produces an
    # immediate turn (and a final transcript) is captured by the
    # already-armed future without race.
    #
    # Phase B treats the interruption text as the candidate's answer
    # to the current question — no Intent Classifier yet (that's
    # Phase F, where meta-requests like "can you repeat?" get semantic
    # gating).
    # ------------------------------------------------------------------
    async def _say(
        self,
        handle: SpeechRenderHandle,
        *,
        allow_interruptions: bool = True,
    ) -> None:
        """Single utterance entry point.

        Phase C: takes a SpeechRenderHandle that has ALREADY passed
        ready_to_commit() (the consumption helper awaits it before
        returning). _say() commits and pipes to TTS.
        """
        await self.session.say(
            handle.commit(), allow_interruptions=allow_interruptions,
        )
        # handle.completed_text + handle.metadata resolve as a side
        # effect of the internal Task draining alongside session.say()'s
        # consumer. SPEECH_RENDERED fires from inside the handle.

    async def _consume_pending_or_render(
        self,
        render_fn,  # render_intro / render_ask_question_standard / render_wrap_normal
        **inputs,
    ) -> SpeechRenderHandle:
        """Use the pending slot if hot; otherwise render synchronously.

        Returns a handle that is GUARANTEED to be past
        ``ready_to_commit()``. Catches ``SpeechRenderError`` from BOTH
        the synchronous ``render()`` path (template_not_found /
        placeholder_missing) AND the asynchronous ``ready_to_commit()``
        path (openai_timeout / openai_5xx /
        openai_connection_dropped_pre_first_token / openai_429),
        substituting a fallback handle in either case.

        The helper is the SOLE catch site for ``SpeechRenderError``.
        ``_say`` receives a handle that has already passed
        ``ready_to_commit()``.
        """
        if self._pending_next_render is not None:
            try:
                handle = await self._pending_next_render
                await handle.ready_to_commit()
                return handle
            except SpeechRenderError as exc:
                log.warning(
                    "speech.pre_render.failed",
                    reason=exc.reason,
                    render_id=exc.render_id,
                )
                return await deliveries.fallback_for(
                    self._speech_agent,
                    template_name=render_fn.template_name,
                    failure_reason=exc.reason,
                    render_id=exc.render_id,
                    **inputs,
                )
            finally:
                self._pending_next_render = None

        # Cold path — no pending slot was spawned (e.g. the very first
        # call, or a code path that didn't pre-render).
        try:
            handle = await render_fn(self._speech_agent, **inputs)
            await handle.ready_to_commit()
            return handle
        except SpeechRenderError as exc:
            log.warning(
                "speech.render.failed",
                reason=exc.reason,
                render_id=exc.render_id,
            )
            return await deliveries.fallback_for(
                self._speech_agent,
                template_name=render_fn.template_name,
                failure_reason=exc.reason,
                render_id=exc.render_id,
                **inputs,
            )

    async def _transition_with_persist(
        self,
        target: InterviewPhase,
        *,
        reason: str,
    ) -> None:
        """Single-entry-point for phase transitions.

        Calls state.transition() (legality check + sequence bump) and
        emits orchestrator.phase_changed envelope event with the old +
        new phase. Persistence write is best-effort and never blocks.
        """
        old_phase = self._state.phase
        self._state.transition(target)
        self._collector.append(
            kind=ORCHESTRATOR_PHASE_CHANGED,
            payload={
                "old_phase": old_phase.value,
                "new_phase": target.value,
                "reason": reason,
            },
            wall_ms=_wall_ms(),
        )
        await self._persistence.write_state(self._state)

    async def on_enter(self) -> None:
        """LiveKit calls this on session.start(). Launch the main loop
        as a background task so on_enter returns promptly; the loop
        manages the entire interview lifecycle.

        Trigger 1 (intro pre-render): spawn the intro `deliveries.render_intro`
        Task into the pending slot BEFORE creating the main loop Task.
        The intro LLM round-trip overlaps the room-join window so the
        candidate hears the first audio with minimal perceived gap once
        the main loop reaches its `_say(intro_handle)` call site.
        """
        self._session_start_monotonic = time.monotonic()
        log.info(
            "structured_agent.on_enter",
            session_id=self._config.session_id,
            candidate_name=self._config.candidate.name,
            job_title=self._config.job_title,
            question_count=len(self._config.stage.questions),
        )

        # Trigger 1 — pre-render intro in the room-join window.
        first_name = (
            self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there"
        )
        self._pending_next_render = asyncio.create_task(
            deliveries.render_intro(
                self._speech_agent,
                candidate_first_name=first_name,
                role_title=self._config.job_title,
                target_duration_minutes=self._config.stage.duration_minutes,
            )
        )

        self._main_loop_task = asyncio.create_task(self._run_main_loop())
        # Add a done-callback so a crashing loop produces a logged error
        # rather than an unobserved-task warning.
        self._main_loop_task.add_done_callback(self._on_main_loop_done)

    def _on_main_loop_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            log.info("structured_agent.main_loop.cancelled")
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "structured_agent.main_loop.errored",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Guard symmetric to _wire_participant_disconnect: only stamp
            # if no other path has already labeled the outcome. Without
            # this, a participant_disconnected callback that fires before
            # the loop raises would be silently overwritten with "error".
            if self._end_outcome is None:
                self._end_outcome = "error"
        else:
            log.info("structured_agent.main_loop.completed")

    def _arm_user_turn(self) -> asyncio.Future[str]:
        """Arm a future resolved by the next turn-detector-confirmed
        end-of-utterance.

        The framework's `MultilingualModel` turn detector consumes STT
        output + dynamic endpointing + EOU context to decide when the
        user is really done. It fires `Agent.on_user_turn_completed`
        with the FULL turn text — not a per-pause STT fragment.

        Listening to `on_user_turn_completed` (this method's resolution
        path) instead of raw STT `is_final` events fixes the
        premature-advance bug (smoke gate 8cbc0ff4-...): Deepgram fires
        `is_final=True` on every ~1s pause, but candidates pause to
        think mid-sentence; the previous implementation cut Q0 at
        "...map business systems to" because the first STT pause
        triggered advancement.

        This must be called BEFORE the awaitable operation it's racing
        (typically `_say` for the next question). Otherwise, an EOU
        that fires DURING that operation is missed. Cleanup of
        `_next_user_turn_future` happens via the done callback so a
        cancelled awaiter doesn't leave a stale reference behind.

        Only one user-turn future may be armed at a time. Re-arming
        before the previous future resolves replaces the reference
        (the unresolved future is garbage-collected — its awaiter
        gets a CancelledError).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._next_user_turn_future = future

        def _detach(fut: asyncio.Future[str]) -> None:
            # Clear the agent's reference so a stray `on_user_turn_completed`
            # after the orchestrator has moved on doesn't try to resolve
            # this future. Best-effort — the field may already point to
            # a freshly armed future from the next question.
            if self._next_user_turn_future is fut:
                self._next_user_turn_future = None

        future.add_done_callback(_detach)
        return future

    async def _run_main_loop(self) -> None:
        """The Phase C linear orchestration loop.

        Sequence:
          CONNECTING → CONSENT → INTRO → MAIN_LOOP → NORMAL_WRAP → CLOSED.

        CONSENT is a real state machine step traversed even though the
        candidate already consented in the pre-room wizard; the phase
        exists as a brief audit-recordable acknowledgment (design doc
        §6.1, §4.2 enum comment). Phase F may add behavior here later.

        Pre-render pipeline (spec §3): each utterance after the intro is
        spawned BEFORE the prior `_say` call so the LLM round-trip
        overlaps with playout / persistence I/O. Triggers:
          1. Intro — spawned in `on_enter` (room-join window).
          2. Q0 — spawned here at the INTRO→MAIN_LOOP boundary,
             AFTER consuming the intro slot and BEFORE awaiting intro
             playout.
          3. Qn+1 / wrap — spawned inside `_ask_one_question` after
             the prior transcript arrives.
        """
        # 1. Brief CONSENT traversal (wizard already captured consent).
        await self._transition_with_persist(
            InterviewPhase.CONSENT,
            reason="wizard_consent_already_captured",
        )

        # 2. Move into INTRO. Consume the intro slot pre-rendered in
        # on_enter (or render synchronously with fallback if missing).
        await self._transition_with_persist(
            InterviewPhase.INTRO, reason="intro_phase",
        )
        first_name = (
            self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there"
        )
        intro_handle = await self._consume_pending_or_render(
            deliveries.render_intro,
            candidate_first_name=first_name,
            role_title=self._config.job_title,
            target_duration_minutes=self._config.stage.duration_minutes,
        )

        # Trigger 2 — spawn Q0 pre-render BEFORE awaiting intro playout.
        # This overlaps the Q0 LLM round-trip with the intro audio.
        first_q = pick_next_question(self._state, self._config)
        if first_q is not None:
            self._pending_next_render = asyncio.create_task(
                deliveries.render_ask_question_standard(
                    self._speech_agent, question_text=first_q.text,
                )
            )

        await self._say(intro_handle)

        # 3. Enter MAIN_LOOP and walk the questions.
        await self._transition_with_persist(
            InterviewPhase.MAIN_LOOP, reason="begin_main_loop",
        )
        while True:
            next_q = pick_next_question(self._state, self._config)
            if next_q is None:
                break
            await self._ask_one_question(next_q)

        # 4. Wrap normally. The wrap slot was pre-rendered at the end of
        # the last `_ask_one_question` (Trigger 3 — the no-more-questions
        # branch).
        wrap_handle = await self._consume_pending_or_render(
            deliveries.render_wrap_normal,
        )
        await self._transition_with_persist(
            InterviewPhase.NORMAL_WRAP, reason="all_questions_completed",
        )
        await self._say(wrap_handle)

        # 5. Close. Stamp _end_outcome BEFORE the CLOSED transition so
        # a participant_disconnected callback that fires in the tiny
        # window between WRAP playout and the orchestrator's final
        # bookkeeping cannot race-overwrite the outcome label
        # (`_wire_participant_disconnect` only stamps when
        # _end_outcome is still None — see agent.py).
        self._end_outcome = "completed"
        await self._transition_with_persist(
            InterviewPhase.CLOSED, reason="normal_close",
        )
        self._state.set_exit_mode(ExitMode.COMPLETED, ended_at=_now_utc())
        self._collector.append(
            kind=ORCHESTRATOR_EXIT,
            payload={
                "exit_mode": ExitMode.COMPLETED.value,
                "reason": "all_questions_completed",
            },
            wall_ms=_wall_ms(),
        )

    async def _ask_one_question(self, q: QuestionConfig) -> None:
        """Ask a single question, wait for one transcribed utterance,
        record both into ledger + envelope + state, advance.

        Trigger 3 (Qn+1 / wrap pre-render) fires inside this method
        AFTER the candidate's transcript arrives but BEFORE the
        persistence + envelope writes. This is load-bearing: the LLM
        round-trip for the next utterance overlaps the persistence
        I/O window (~150-280ms perceived gap savings). If no further
        questions remain, the wrap utterance is pre-rendered instead.
        """
        # Locate the QuestionState for this question (created in __init__).
        qs = next(
            (s for s in self._state.questions if s.question_id == q.id),
            None,
        )
        if qs is None:
            # Defensive — should never happen because __init__ creates
            # one QuestionState per QuestionConfig.
            log.error(
                "structured_agent.question_state.missing",
                question_id=q.id,
            )
            return

        qs.asked_at = _now_utc()
        qs.asked_mode = "standard"
        await self._persistence.write_state(self._state)

        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_ASKED,
            payload={
                "question_id": q.id,
                "position": q.position,
                "mode": "standard",
            },
            wall_ms=_wall_ms(),
        )

        # Consume the pre-rendered handle for THIS question (or render
        # synchronously / fall back if the slot is missing or errored).
        handle = await self._consume_pending_or_render(
            deliveries.render_ask_question_standard,
            question_text=q.text,
        )

        # Arm the user-turn future BEFORE saying. The framework's
        # turn-detector-confirmed EOU fires via `on_user_turn_completed`
        # and resolves this future; pre-arming guarantees no race even
        # when the candidate's turn completes during the agent's say
        # (e.g., a fast barge-in). See `_arm_user_turn` and
        # `on_user_turn_completed` for full rationale (smoke gates
        # ca971b63 + 8cbc0ff4 confirmed both the race and the
        # premature-advance bug from listening to STT `is_final`).
        transcript_future = self._arm_user_turn()

        await self._say(handle)

        # Wait for the candidate's final transcribed utterance. If the
        # listener already caught one during `_say`, this resolves
        # immediately.
        transcript = await transcript_future
        self._candidate_transcripts[q.id] = transcript

        qs.completed_at = _now_utc()
        qs.elapsed_seconds = (
            qs.completed_at - qs.asked_at
        ).total_seconds() if qs.asked_at else 0.0

        # Trigger 3 — spawn the next utterance's pre-render BEFORE the
        # persistence + envelope writes below. Order is load-bearing:
        # the LLM round-trip overlaps the persistence I/O window.
        next_q = pick_next_question(self._state, self._config)
        if next_q is not None:
            self._pending_next_render = asyncio.create_task(
                deliveries.render_ask_question_standard(
                    self._speech_agent, question_text=next_q.text,
                )
            )
        else:
            # No more questions — pre-render the wrap utterance for the
            # main loop's post-loop `_say(wrap_handle)` call site.
            self._pending_next_render = asyncio.create_task(
                deliveries.render_wrap_normal(self._speech_agent)
            )

        await self._persistence.write_ledger(self._ledger)

        self._collector.append(
            kind=ORCHESTRATOR_QUESTION_COMPLETED,
            payload={
                "question_id": q.id,
                "elapsed_seconds": qs.elapsed_seconds,
                "followups_asked": qs.followups_asked,
                # Phase B omits coverage_at_close; added in Phase D when
                # Sufficiency Checker provides it.
            },
            wall_ms=_wall_ms(),
        )

    def _build_session_result(self, outcome: SessionOutcome) -> SessionResult:
        """Compose the SessionResult from accumulated per-question state."""
        question_results: list[QuestionResult] = []
        full_transcript: list[TranscriptEntry] = []

        questions_asked = 0
        questions_skipped = 0

        # Compute timestamp_ms relative to session start for transcript entries.
        def _ts_ms_for(asked_at: datetime | None) -> int:
            if asked_at is None:
                return 0
            return int((asked_at.timestamp() - self._state.started_at.timestamp()) * 1000)

        # Phase B per-question result invariant:
        #   - asked + completed (transcript captured): was_skipped=False,
        #     elapsed_seconds>0, transcript_entries=[<one entry>].
        #   - asked + NOT completed (disconnect mid-question, candidate
        #     went silent and timed out, etc.): was_skipped=False,
        #     elapsed_seconds=0 (default from QuestionState),
        #     transcript_entries=[]. The question WAS asked; the
        #     candidate just didn't produce a final transcript.
        #   - never asked (disconnect before reaching this question):
        #     was_skipped=True, transcript_entries=[].
        # Phase D's Sufficiency Checker will refine the partial-asked
        # case once observations carry coverage data.
        for q in self._config.stage.questions:
            qs = next(
                (s for s in self._state.questions if s.question_id == q.id),
                None,
            )
            asked = qs is not None and qs.asked_at is not None
            transcript_text = self._candidate_transcripts.get(q.id)

            entries: list[TranscriptEntry] = []
            if asked and transcript_text is not None:
                entries.append(
                    TranscriptEntry(
                        role="candidate",
                        text=transcript_text,
                        timestamp_ms=_ts_ms_for(qs.asked_at if qs else None),
                        question_id=q.id,
                    )
                )
                full_transcript.extend(entries)

            was_skipped = not asked
            if was_skipped:
                questions_skipped += 1
            else:
                questions_asked += 1

            question_results.append(
                QuestionResult(
                    question_id=q.id,
                    question_text=q.text,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                    was_skipped=was_skipped,
                    probes_fired=0,
                    observations=[],
                    transcript_entries=entries,
                )
            )

        return SessionResult(
            session_id=self._config.session_id,
            job_title=self._config.job_title,
            stage_id=self._config.stage.stage_id,
            stage_type=self._config.stage.stage_type,
            candidate_name=self._config.candidate.name,
            duration_seconds=time.monotonic() - self._session_start_monotonic,
            questions_asked=questions_asked,
            questions_skipped=questions_skipped,
            total_probes_fired=0,
            question_results=question_results,
            full_transcript=full_transcript,
            completed_at=_now_utc().isoformat(),
            knockout_failures=[],
        )

    def get_state(self) -> InterviewState:
        """Read-only access for the close handler in agent.py."""
        return self._state

    def get_ledger(self) -> SignalLedger:
        """Read-only access for the close handler in agent.py."""
        return self._ledger

    def get_persistence(self) -> LedgerPersistence:
        """Read-only access for the close handler in agent.py."""
        return self._persistence

    async def _persist_session_result(self, outcome: SessionOutcome) -> None:
        """Atomic SessionResult persistence via bypass session."""
        if self._persisted:
            return
        from app.database import get_bypass_session  # lazy import to mirror agent.py
        from app.modules.interview_runtime import record_session_result

        result = self._build_session_result(outcome)
        async with get_bypass_session() as db:
            await record_session_result(
                db,
                session_id=uuid.UUID(self._config.session_id),
                tenant_id=self._tenant_id,
                result=result,
                correlation_id=self._correlation_id,
            )
            await db.commit()
        self._persisted = True
        log.info(
            "structured_agent.result.persisted",
            session_id=self._config.session_id,
            outcome=outcome,
        )

    async def _publish_session_outcome(self, outcome: SessionOutcome) -> None:
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes(
                {"session_outcome": outcome},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "structured_agent.outcome.publish_failed",
                outcome=outcome,
                error=str(exc),
            )

    async def _finalize_event_log(
        self,
        *,
        reason: str,
        sink: EventLogSink | None,
    ) -> None:
        if self._envelope_written or sink is None:
            return
        self._envelope_written = True
        closed_at = _now_utc().isoformat().replace("+00:00", "Z")
        try:
            envelope = self._collector.close(closed_at=closed_at)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "structured_agent.event_log.envelope_validation_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            target = await asyncio.to_thread(sink.write, envelope)
            log.info(
                "structured_agent.event_log.written", reason=reason, target=target,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "structured_agent.event_log.sink_write_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
