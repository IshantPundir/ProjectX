"""
Gen-3 Interview Engine — SessionDriver (F1).

LiveKit-free orchestration core — the testable heart of the drive loop.

The driver ties the six gen-3 modules together:
  Ear (commits turns) → loop `run_turn` (bridge ∥ brain → mouth + NoteLog) →
  on a terminal directive (close), assemble SessionEvidence (NoteLog + provenance)
  and persist it via an injectable `persist` callable.

architecture
~~~~~~~~~~~~
  - `SessionDriver` is completely LiveKit-free. All collaborators are duck-typed
    via protocols (Brain, Mouth, Voice) or constructor-injected callables. It can
    be driven from a unit test with fake collaborators OR from agent.py's `_drive`
    with real LiveKit session + DB callbacks.

  - `_CapturingVoice` wraps a duck-typed voice and records every spoken text so
    the driver can write agent TranscriptTurns without duplicating say() logic.

  - `build_session_driver` is the module-level factory that assembles everything
    from a `SessionConfig` — agent.py's `_drive` calls this.

invariants
~~~~~~~~~~
  - No LLM is called here. LLM calls live in brain.service / mouth.service /
    mouth.bridge — all injectable and mockable.
  - No LiveKit import at module level. Any livekit-dependent wiring lives in
    agent.py and is clearly marked `# F3-VALIDATE`.
  - Append-only NoteLog — driver never mutates notes, only calls notelog.append
    indirectly via run_turn.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Callable

import structlog

from app.modules.interview_engine.brain.input_builder import (
    CoverageProjection,
    active_question_rubric,
    build_turn_input,
)
from app.modules.interview_engine.brain.resolver import (
    ResolverQuestion,
    build_question_records,
    resolve_next,
)
from app.modules.interview_engine.contracts import (
    BrainDecision,
    BridgeRequest,
    Directive,
    DirectiveAct,
    DirectiveTone,
    MouthTurnInput,
    SignalSpec,
    WindowTurn,
)
from app.modules.interview_engine.loop import run_turn, TurnContext, ABORTED
from app.modules.interview_engine.turn_source import AssembledTurn
from app.modules.interview_engine.notes import NoteLog, compute_provenance
from app.modules.interview_engine.turn_taking import is_backchannel
from app.modules.interview_runtime.evidence import (
    CompletionReason,
    QuestionRecord,
    SessionEvidence,
    SessionMeta,
    SignalEvidence,
    SignalPriority,
    SignalType,
    Speaker,
    ThreadClosure,
    TimeSpan,
    TranscriptTurn,
    Word,
)
from app.modules.interview_runtime.schemas import QuestionConfig, SessionConfig

_log = structlog.get_logger("interview_engine.driver")

# Acts that mean the candidate did NOT give a gradeable answer this turn (used by the
# anti-stall counter). A run of these on one question = the candidate is stalling/dodging.
_NON_ANSWER_ACTS: frozenset[DirectiveAct] = frozenset({
    DirectiveAct.clarify,
    DirectiveAct.repeat,
    DirectiveAct.redirect,
    DirectiveAct.hold,
    DirectiveAct.answer_meta,
})

# Acts that DELIVER the floor question — only these may update the floor pointer.
# (clarify/hold/reassure/confirm/answer_meta/redirect/close must NOT clobber the floor,
#  or a later `repeat`/`clarify` would replay a non-question line. E2 invariant.)
_QUESTION_ACTS: frozenset[DirectiveAct] = frozenset({
    DirectiveAct.ask, DirectiveAct.probe, DirectiveAct.repeat,
})


def _is_question_act(act: DirectiveAct) -> bool:
    return act in _QUESTION_ACTS


# How many recent agent openers to feed back to the mouth (de-duplication window)
_RECENT_OPENERS_WINDOW: int = 5

# How many transcript turns to include in the brain's sliding window
_TRANSCRIPT_WINDOW_K: int = 6


# ============================================================================
# _CapturingVoice — wraps a voice, records what was said
# ============================================================================

class _CapturingVoice:
    """Wraps a duck-typed voice and records every said text.

    The driver uses this wrapper so it can write agent TranscriptTurns for
    the bridge text and the real line, without duplicating the say() logic.

    All say() calls are delegated to the underlying voice.
    """

    def __init__(self, voice: object) -> None:
        self._voice = voice
        self.captured: list[str] = []

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        self.captured.append(text)
        await self._voice.say(text, allow_interruptions=allow_interruptions)  # type: ignore[union-attr]


class _MouthAdapter:
    """Combines the two mouth halves into the single object `run_turn` expects.

    The loop's `Mouth` protocol needs BOTH `bridge()` and `real_line()` on one
    object, but gen-3 splits them: `BridgeComposer` (the immediate gist-mirror
    beat) and `ConversationPlane` (the real line). This thin adapter delegates
    each call to the right half so the driver can pass one `mouth` to `run_turn`.
    """

    def __init__(self, *, real_plane: object, bridge_composer: object) -> None:
        self._real_plane = real_plane
        self._bridge_composer = bridge_composer

    async def bridge(self, req):  # type: ignore[no-untyped-def]
        return await self._bridge_composer.bridge(req)  # type: ignore[union-attr]

    async def real_line(self, mouth_input):  # type: ignore[no-untyped-def]
        return await self._real_plane.real_line(mouth_input)  # type: ignore[union-attr]


class _BrainAdapter:
    """Adapts `ControlPlane.decide` to the loop's `Brain.decide(turn_input)` shape.

    `run_turn` calls `brain.decide(turn_input)` with no extra args, but
    `ControlPlane.decide` also needs the per-turn `asked_ids` (for the
    deterministic resolver). The driver refreshes that on this adapter right
    before each `run_turn`, so the loop stays decoupled from resolver state.
    """

    def __init__(self, control_plane: object) -> None:
        self._cp = control_plane
        self.asked_ids: set[str] = set()

    async def decide(self, turn_input):  # type: ignore[no-untyped-def]
        return await self._cp.decide(  # type: ignore[union-attr]
            turn_input,
            asked_ids=self.asked_ids,
        )


# ============================================================================
# SessionDriver
# ============================================================================

class SessionDriver:
    """Livekit-free orchestration core for one interview session.

    Parameters
    ----------
    config:
        The SessionConfig for this session (bank questions, signals, stage).
    brain:
        Async control-plane duck-typed via loop.Brain protocol.
    mouth:
        Spoken-word renderer duck-typed via loop.Mouth protocol (real_line).
    bridge:
        Bridge-composer duck-typed via loop.Mouth bridge protocol.
    notelog:
        The session's append-only NoteLog (constructed fresh per session).
    projection:
        CoverageProjection — ephemeral per-session signal coverage state.
    voice:
        TTS delivery surface duck-typed via loop.Voice protocol.
    persist:
        Async callable ``(SessionEvidence) -> None`` — injectable.
        Tests pass a fake; agent.py passes a record_session_evidence wrapper.
    time_budget_s:
        planned stage duration in seconds — persisted to SessionMeta for audit;
        NOT used for runtime question gating.
    started_at:
        Wall-clock datetime when the session started (UTC).
    now_fn:
        Optional callable ``() -> datetime`` returning the current UTC time.
        Defaults to ``datetime.now(UTC)``. Inject a fixed function in tests.
    """

    def __init__(
        self,
        *,
        config: SessionConfig,
        brain: object,
        mouth: object,
        bridge: object,
        notelog: NoteLog,
        projection: CoverageProjection,
        voice: object,
        persist: Callable,
        time_budget_s: float,
        started_at: datetime,
        now_fn: Callable[[], datetime] | None = None,
        is_superseded: Callable[[], bool] | None = None,
        on_committed: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._brain = brain
        self._mouth = mouth
        self._bridge = bridge
        # run_turn expects ONE mouth object with both bridge() + real_line().
        self._mouth_combined = _MouthAdapter(real_plane=mouth, bridge_composer=bridge)
        # run_turn calls brain.decide(turn_input); ControlPlane.decide also needs
        # per-turn asked_ids — refreshed before each run_turn.
        self._brain_adapter = _BrainAdapter(brain)
        self._notelog = notelog
        self._projection = projection
        self._voice = voice
        self._persist = persist
        self._time_budget_s = time_budget_s
        self._started_at = started_at
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._is_superseded_cb = is_superseded
        self._on_committed_cb = on_committed
        self._forced_superseded = False  # test hook only

        # Build resolver questions from the bank (mirrors build_control_plane logic)
        self._resolver_questions: list[ResolverQuestion] = [
            ResolverQuestion(
                question_id=q.id,
                primary_signal=q.primary_signal or (q.signal_values[0] if q.signal_values else ""),
                position=q.position,
            )
            for q in config.stage.questions
        ]

        # All signal specs (from signal_metadata)
        self._all_specs: list[SignalSpec] = [
            SignalSpec(
                signal=m.value,
                signal_type=SignalType(m.type),
                weight=m.weight,
                priority=SignalPriority(m.priority),
                knockout=m.knockout,
            )
            for m in config.signal_metadata
        ]

        # Anti-stall threshold (env-driven; consecutive non-answer turns before advancing)
        from app.ai.config import ai_config  # local import keeps the driver livekit-free
        self._stall_threshold: int = ai_config.engine_stall_reposes_before_advance

        # Per-question map for id lookups
        self._q_by_id: dict[str, QuestionConfig] = {q.id: q for q in config.stage.questions}

        # Mutable session state
        self._asked_ids: set[str] = set()
        self._closures: dict[str, ThreadClosure] = {}
        self._transcript: list[TranscriptTurn] = []
        self._recent_openers: list[str] = []
        self._turn_counter: int = 0
        self._active_q: QuestionConfig | None = None  # the question currently on the floor
        self._fired_dimensions: dict[str, list[str]] = {}   # question_id → fired dimension slugs
        self._thread_turn_counts: dict[str, int] = {}  # question_id → turns spent on its thread
        self._last_agent_line: str = ""                # for on_the_floor (last QUESTION asked)
        self._is_on_probe: bool = False                # whether the floor is a probe (not main Q)
        self._floor_interrupted: bool = False          # the floor question was cut off mid-delivery (P2)
        self._stall_count: int = 0                     # consecutive non-answer turns on the active question (anti-stall)

    def _set_superseded(self, value: bool) -> None:  # test hook only
        self._forced_superseded = value

    def _superseded(self) -> bool:
        if self._forced_superseded:
            return True
        return bool(self._is_superseded_cb and self._is_superseded_cb())

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _make_turn_ref(self) -> str:
        self._turn_counter += 1
        return f"t-{self._turn_counter}"

    def _add_to_recent_openers(self, text: str) -> None:
        """Extract the first word of `text` and add to the sliding window."""
        first_word = text.strip().split()[0] if text.strip() else ""
        if first_word:
            self._recent_openers.append(first_word)
            if len(self._recent_openers) > _RECENT_OPENERS_WINDOW:
                self._recent_openers.pop(0)

    def _build_transcript_window(self) -> list[WindowTurn]:
        """Return the last K turns as WindowTurn entries for the brain."""
        window = self._transcript[-_TRANSCRIPT_WINDOW_K:]
        return [
            WindowTurn(
                turn_ref=t.turn_ref,
                speaker=t.speaker.value,
                text=t.text,
            )
            for t in window
        ]

    def _record_agent_turn(
        self,
        text: str,
        turn_ref: str,
        span: TimeSpan | None = None,
        question_id: str | None = None,
    ) -> None:
        """Append an agent TranscriptTurn (no word-level timing needed)."""
        if not span:
            now_ms = int((self._now_fn() - self._started_at).total_seconds() * 1000)
            span = TimeSpan(start_ms=max(0, now_ms), end_ms=max(0, now_ms))
        self._transcript.append(
            TranscriptTurn(
                turn_ref=turn_ref,
                speaker=Speaker.agent,
                text=text,
                span=span,
                pre_turn_gap_ms=0,
                words=[],
                question_id=question_id,
            )
        )

    def _infer_closure(self, question_id: str, decision: BrainDecision) -> ThreadClosure:
        """Best-effort closure inference from the brain's final observation for this thread."""
        # Check if the primary signal for this question was well-supported
        q = self._q_by_id.get(question_id)
        if q is None:
            return ThreadClosure.tapped_out

        primary = q.primary_signal or (q.signal_values[0] if q.signal_values else "")

        # Look at the projection's coverage for this signal
        reads = {r.signal: r for r in self._projection.signal_reads()}
        read = reads.get(primary)

        if read is None:
            return ThreadClosure.tapped_out

        from app.modules.interview_runtime.evidence import CoverageState, EvidenceStance
        if read.coverage == CoverageState.sufficient and read.last_stance == EvidenceStance.supports:
            return ThreadClosure.satisfied
        if read.last_stance == EvidenceStance.contradicts:
            return ThreadClosure.absent
        return ThreadClosure.tapped_out

    # -----------------------------------------------------------------------
    # intro — warm greeting + job brief, BEFORE the first question
    # -----------------------------------------------------------------------

    async def intro(self) -> str:
        """Speak the opening greeting + one-line job brief, then return (the caller
        calls opener() right after to ask the first question).

        Spoken NON-INTERRUPTIBLY so the candidate hears the whole greeting, and it
        ends on a confident statement that flows into the first question — never a
        "shall we?" (enforced by intro.txt). Records the agent transcript turn.
        Never raises (the mouth returns a canned greeting on any failure)."""
        intro_text = await self._mouth.intro(  # type: ignore[union-attr]
            candidate_name=self._config.candidate.name,
            role_summary=self._config.role_summary,
            company_about=self._config.company.about,
        )
        await self._voice.say(intro_text, allow_interruptions=False)  # type: ignore[union-attr]
        self._record_agent_turn(intro_text, turn_ref="intro", question_id=None)
        self._add_to_recent_openers(intro_text)
        _log.info("driver.intro", chars=len(intro_text))
        return intro_text

    # -----------------------------------------------------------------------
    # opener — first agent turn
    # -----------------------------------------------------------------------

    async def opener(self) -> str:
        """Resolve and speak the first bank question; record the agent TranscriptTurn.

        If the bank is empty (no questions), immediately plays a close directive.

        Returns the spoken opener text.
        """
        nxt = resolve_next(
            questions=self._resolver_questions,
            asked_ids=self._asked_ids,
        )

        turn_ref = self._make_turn_ref()

        if nxt is None:
            # Empty bank — close immediately
            directive = Directive(
                act=DirectiveAct.close,
                say=None,
                tone=DirectiveTone.warm,
                is_terminal=True,
            )
            mouth_input = MouthTurnInput(
                directive=directive,
                just_said=None,
                recent_openers=[],
            )
            real_text = await self._mouth.real_line(mouth_input)  # type: ignore[union-attr]
            await self._voice.say(real_text)  # type: ignore[union-attr]
            self._last_agent_line = real_text
            self._record_agent_turn(real_text, turn_ref=turn_ref, question_id=None)
            return real_text

        # Advance to the first question
        self._active_q = self._q_by_id[nxt.question_id]
        self._asked_ids.add(nxt.question_id)
        self._fired_dimensions.setdefault(nxt.question_id, [])
        self._thread_turn_counts[nxt.question_id] = 0
        self._is_on_probe = False

        directive = Directive(
            act=DirectiveAct.ask,
            say=self._active_q.text,
            tone=DirectiveTone.warm,
            is_terminal=False,
        )
        mouth_input = MouthTurnInput(
            directive=directive,
            just_said=None,
            recent_openers=self._recent_openers[-3:],
        )
        real_text = await self._mouth.real_line(mouth_input)  # type: ignore[union-attr]
        await self._voice.say(real_text)  # type: ignore[union-attr]
        self._floor_interrupted = bool(getattr(self._voice, "last_interrupted", False))
        self._last_agent_line = real_text
        self._add_to_recent_openers(real_text)

        now_ms = int((self._now_fn() - self._started_at).total_seconds() * 1000)
        span = TimeSpan(start_ms=max(0, now_ms - 1), end_ms=max(0, now_ms))
        self._record_agent_turn(real_text, turn_ref=turn_ref, question_id=self._active_q.id)

        _log.info(
            "driver.opener",
            question_id=self._active_q.id,
            question_text=self._active_q.text[:60],
        )
        return real_text

    # -----------------------------------------------------------------------
    # handle_turn — one committed candidate turn
    # -----------------------------------------------------------------------

    async def handle_turn(
        self,
        *,
        turn: AssembledTurn,
        turn_ref: str,
        pre_turn_gap_ms: int = 0,
    ) -> bool:
        """Process one committed candidate turn.

        1. Record the CANDIDATE TranscriptTurn.
        2. Build brain_input + bridge_request.
        3. Wrap voice in _CapturingVoice; call run_turn (bridge ∥ brain → mouth).
        4. Record AGENT TranscriptTurns for bridge + real line.
        5. Update session state (active question, closures, asked_ids, probes_used).

        Returns:
            True when the session is terminal (close directive or bank exhausted).
        """
        utterance = turn.text
        span = turn.span

        # Guard: if no active question, treat as terminal
        if self._active_q is None:
            _log.warning("driver.handle_turn.no_active_question", turn_ref=turn_ref)
            return True

        # Backchannel gate (no LLM): a turn made entirely of engagement tokens
        # ("mm", "yeah", "uh-huh") is not a real turn — stay silent, keep the floor,
        # don't run the brain. An interrupted floor stays interrupted for the next
        # substantive turn. (gen-2 parity.)
        if is_backchannel(utterance):
            _log.info(
                "engine.driver.backchannel_dropped",
                turn_ref=turn_ref,
                utterance=(utterance or "")[:40],
            )
            # Release the assembler: this flushed turn is done (dropped), so the
            # next candidate speech is a fresh turn, not a continuation of it.
            if self._on_committed_cb is not None:
                self._on_committed_cb()
            return False

        # 1. Record candidate turn
        self._transcript.append(
            TranscriptTurn(
                turn_ref=turn_ref,
                speaker=Speaker.candidate,
                text=utterance,
                span=span,
                pre_turn_gap_ms=pre_turn_gap_ms,
                # AssembledTurn.words are turn-relative WordTiming (incl. STT
                # confidence); the evidence transcript keeps only text + bounds.
                words=[
                    Word(text=w.text, start_ms=w.start_ms, end_ms=w.end_ms)
                    for w in turn.words
                ],
                question_id=self._active_q.id,
            )
        )

        # Increment thread turn count for active question
        q_id = self._active_q.id
        self._thread_turn_counts[q_id] = self._thread_turn_counts.get(q_id, 0) + 1

        # 2. Build brain_input
        aq_rubric = active_question_rubric(
            self._active_q,
            fired_dimensions=self._fired_dimensions.get(q_id, []),
        )
        brain_input = build_turn_input(
            turn_ref=turn_ref,
            active_question=aq_rubric,
            on_the_floor=self._last_agent_line,
            candidate_utterance=utterance,
            thread_turn_count=self._thread_turn_counts.get(q_id, 1),
            projection=self._projection,
            all_specs=self._all_specs,
            transcript_window=self._build_transcript_window(),
            floor_interrupted=self._floor_interrupted,  # P2: was the last question cut off?
            stalled=self._stall_count >= self._stall_threshold,  # anti-stall: dodged this question too many times?
        )

        bridge_request = BridgeRequest(
            candidate_utterance=utterance,
            recent_openers=self._recent_openers[-3:],
        )

        # 3. Build TurnContext and run_turn with capturing voice
        ctx = TurnContext(
            turn_ref=turn_ref,
            utterance=utterance,
            utterance_span=span,
            from_question_id=q_id,
            via_probe=self._is_on_probe,
            brain_input=brain_input,
            bridge_request=bridge_request,
            recent_openers=self._recent_openers[-3:],
            supersession_check=self._superseded,
            suppress_bridge=turn.suppress_bridge,
            on_committed=self._on_committed_cb,
        )

        capturing = _CapturingVoice(self._voice)

        # Refresh the brain adapter's per-turn resolver state before run_turn.
        self._brain_adapter.asked_ids = set(self._asked_ids)

        decision = await run_turn(
            ctx,
            brain=self._brain_adapter,  # decide(turn_input) → ControlPlane.decide(+resolver state)
            mouth=self._mouth_combined,  # bridge() + real_line() combined
            voice=capturing,
            notelog=self._notelog,
        )

        if decision is ABORTED:
            # Merge-back: a continuation superseded this turn. Pop the candidate
            # TranscriptTurn appended at the top (no notes were committed; the
            # assembler will re-flush the merged turn), do not advance state.
            self._transcript.pop()
            _log.info("engine.driver.turn_aborted_merge_back", turn_ref=turn_ref)
            return False

        # 4. Record agent turns for what was spoken (bridge text + real line)
        # run_turn speaks: bridge first, then the real line — both via capturing voice.
        # captured[0] = bridge text, captured[1] = real line (if both spoken).
        # The real line is always the last captured text.
        agent_turn_ref_prefix = f"{turn_ref}-agent"
        for i, said_text in enumerate(capturing.captured):
            self._record_agent_turn(
                said_text,
                turn_ref=f"{agent_turn_ref_prefix}-{i}",
                question_id=q_id,
            )

        # recent_openers (sentence-start variety) tracks EVERY spoken line; the
        # "question on the floor" (on_the_floor → used by repeat/clarify) tracks
        # ONLY question acts (ask/probe). A non-question filler — hold, reassure,
        # confirm, answer_meta, redirect, clarify — must NOT clobber the floor, or
        # a later `repeat` replays the filler ("Take your time, no rush.") instead
        # of the real question. (gen-2 parity: non-question acts leave the floor;
        # P0 fix for the stuck-loop, session b3c16e7c.)
        if capturing.captured:
            real_line_text = capturing.captured[-1]
            self._add_to_recent_openers(real_line_text)
            if _is_question_act(decision.directive.act):
                self._last_agent_line = real_line_text  # floor = latest question-bearing line

        # P2: track whether the floor question's DELIVERY was cut off. Only the
        # question-delivering acts (ask/probe/repeat) update it — they speak THE floor
        # question, so their interruption status is "did the candidate hear it?". A
        # non-question act (hold/clarify/…) doesn't re-deliver, so an un-heard question
        # stays flagged until it is successfully re-delivered. (last_interrupted is set
        # by the interrupt-aware voice in agent.py; absent in tests → False.)
        if _is_question_act(decision.directive.act):
            self._floor_interrupted = bool(getattr(self._voice, "last_interrupted", False))

        # Anti-stall counter: a non-answer turn (clarify / repeat / redirect / hold /
        # answer_meta with NO gradeable answer) means the candidate dodged/didn't answer
        # this question — increment. ANY real answer (observations) or a forward move
        # (ask / probe) resets it, so a genuinely-confused candidate who clarifies then
        # answers is never flagged. (confirm = an STT re-check, not a dodge → resets.)
        if (
            decision.directive.act in _NON_ANSWER_ACTS
            and not decision.observations
        ):
            self._stall_count += 1
        else:
            self._stall_count = 0

        # 5. Update session state based on decision
        act = decision.directive.act

        if decision.is_terminal:
            # Terminal (close) — record closure for the current question
            self._closures[q_id] = self._infer_closure(q_id, decision)
            _log.info("driver.handle_turn.terminal", turn_ref=turn_ref, q_id=q_id)
            return True

        if act == DirectiveAct.ask:
            # Advance to a new question
            # Record closure for the question we're leaving
            self._closures[q_id] = self._infer_closure(q_id, decision)

            next_q_id = decision.next_question_id
            if next_q_id is None or next_q_id not in self._q_by_id:
                # Resolver found nothing — terminal
                self._active_q = None
                _log.info(
                    "driver.handle_turn.bank_exhausted",
                    turn_ref=turn_ref,
                    next_question_id=next_q_id,
                )
                return True

            self._active_q = self._q_by_id[next_q_id]
            self._asked_ids.add(next_q_id)
            self._fired_dimensions.setdefault(next_q_id, [])
            self._thread_turn_counts[next_q_id] = 0
            self._is_on_probe = False
            _log.info(
                "driver.handle_turn.advanced",
                turn_ref=turn_ref,
                from_q=q_id,
                to_q=next_q_id,
            )

        elif act == DirectiveAct.probe:
            # Probe on current question — record the served dimension slug so it is
            # never fired again on this thread (fire-once ledger).
            self._is_on_probe = True
            served = decision.probe_dimension
            if served:
                fired = self._fired_dimensions.setdefault(q_id, [])
                if served not in fired:
                    fired.append(served)
        else:
            # clarify / redirect / reassure / answer_meta / repeat — stay on same Q, no probe
            self._is_on_probe = False

        return False

    # -----------------------------------------------------------------------
    # finalize — session-end packaging + persist
    # -----------------------------------------------------------------------

    async def finalize(self, completion: CompletionReason) -> SessionEvidence:
        """Assemble and persist the SessionEvidence at session end.

        1. Build per-signal SignalEvidence identity list (placeholder provenance).
        2. Compute provenance from the notelog via compute_provenance.
        3. Build QuestionRecord list via build_question_records.
        4. Assemble SessionMeta.
        5. Call notelog.to_session_evidence.
        6. Persist via self._persist(evidence).

        Returns:
            The persisted SessionEvidence.
        """
        now = self._now_fn()
        duration_s = (now - self._started_at).total_seconds()

        # Ensure any active question still on the floor gets a closure
        if self._active_q is not None and self._active_q.id not in self._closures:
            self._closures[self._active_q.id] = ThreadClosure.truncated

        # 1. Build identity SignalEvidence list (provenance is a placeholder, recomputed next)
        from app.modules.interview_runtime.evidence import Provenance
        identity_signals: list[SignalEvidence] = [
            SignalEvidence(
                signal=m.value,
                signal_type=SignalType(m.type),
                weight=m.weight,
                priority=SignalPriority(m.priority),
                knockout=m.knockout,
                provenance=Provenance.not_reached,  # placeholder; overwritten below
            )
            for m in self._config.signal_metadata
        ]

        # 2. Compute provenance from the append-only notes.
        # QuestionRecord.probes_used is index-based; map fired dimension slugs → indices.
        probes_used_idx: dict[str, list[int]] = {}
        for rq in self._resolver_questions:
            cfg = self._q_by_id.get(rq.question_id)
            if cfg is None:
                continue
            slug_to_idx = {d.dimension: i for i, d in enumerate(cfg.follow_ups)}
            probes_used_idx[rq.question_id] = [
                slug_to_idx[s]
                for s in self._fired_dimensions.get(rq.question_id, [])
                if s in slug_to_idx
            ]
        question_records_for_prov = build_question_records(
            questions=self._resolver_questions,
            asked_ids=self._asked_ids,
            closures=self._closures,
            probes_used=probes_used_idx,
            probes_available={
                q.question_id: len(self._q_by_id[q.question_id].follow_ups)
                for q in self._resolver_questions
                if q.question_id in self._q_by_id
            },
        )
        signals_with_prov = compute_provenance(
            signals=identity_signals,
            notes=self._notelog.notes,
            questions=question_records_for_prov,
        )

        # 3. Final question records (same call, same data)
        questions: list[QuestionRecord] = question_records_for_prov

        # 4. SessionMeta
        meta = SessionMeta(
            session_id=self._config.session_id,
            job_id=self._config.job_id,
            candidate_id=self._config.candidate_id,
            stage_id=self._config.stage.stage_id,
            started_at=self._started_at,
            ended_at=now,
            duration_s=max(0.0, duration_s),
            time_budget_s=self._time_budget_s,
            completion=completion,
            questions_asked=len(self._asked_ids),
        )

        # 5. Assemble evidence
        evidence: SessionEvidence = self._notelog.to_session_evidence(
            meta=meta,
            signals=signals_with_prov,
            questions=questions,
            transcript=self._transcript,
        )

        # 6. Persist
        await self._persist(evidence)

        _log.info(
            "driver.finalize.persisted",
            session_id=self._config.session_id,
            questions_asked=len(self._asked_ids),
            notes_count=len(self._notelog.notes),
            transcript_turns=len(self._transcript),
            completion=completion.value,
        )

        return evidence


# ============================================================================
# Module-level factory — used by agent.py
# ============================================================================

def build_session_driver(
    config: SessionConfig,
    *,
    voice: object,
    persist: Callable,
    started_at: datetime,
    projection: CoverageProjection | None = None,
    now_fn: Callable[[], datetime] | None = None,
    is_superseded: Callable[[], bool] | None = None,
    on_committed: Callable[[], None] | None = None,
) -> SessionDriver:
    """Assemble a SessionDriver from a SessionConfig for production use.

    agent.py calls this once per session at the top of _drive. It assembles:
      - ControlPlane (brain) via build_control_plane
      - ConversationPlane (mouth real_line) via E2
      - BridgeComposer (mouth bridge) via E3
      - NoteLog (fresh)
      - CoverageProjection (fresh or provided)
      - persona_name + job_title from ai_config + config

    Parameters
    ----------
    config:
        The session's SessionConfig (built by interview_runtime.build_session_config).
    voice:
        The TTS delivery surface (duck-typed Voice protocol — in production this is
        the AgentSession from LiveKit; in tests it's a fake).
    persist:
        Async callable ``(SessionEvidence) -> None``. In production, this is a
        closure over ``record_session_evidence(db, ...)``; in tests it's a fake.
    started_at:
        Wall-clock session-start datetime (UTC). Used to compute elapsed time.
    projection:
        Optional pre-built CoverageProjection. Defaults to a fresh one.
    now_fn:
        Optional time-injection for tests. Defaults to ``datetime.now(UTC)``.
    """
    from app.ai.config import ai_config
    from app.modules.interview_engine.brain.service import build_control_plane
    from app.modules.interview_engine.mouth.bridge import BridgeComposer
    from app.modules.interview_engine.mouth.service import ConversationPlane

    _projection = projection or CoverageProjection()
    notelog = NoteLog()

    persona_name: str = getattr(ai_config, "engine_mouth_persona_name", "Arjun")
    job_title: str = config.job_title

    brain = build_control_plane(config, projection=_projection)
    mouth = ConversationPlane(persona_name=persona_name, job_title=job_title)
    bridge = BridgeComposer(persona_name=persona_name, job_title=job_title)

    return SessionDriver(
        config=config,
        brain=brain,
        mouth=mouth,
        bridge=bridge,
        notelog=notelog,
        projection=_projection,
        voice=voice,
        persist=persist,
        time_budget_s=float(config.stage.duration_minutes * 60),
        started_at=started_at,
        now_fn=now_fn,
        is_superseded=is_superseded,
        on_committed=on_committed,
    )
