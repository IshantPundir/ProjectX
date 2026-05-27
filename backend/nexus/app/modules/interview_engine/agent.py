"""Interview Engine — LiveKit entrypoint + worker bootstrap.

The ControlPlane (the brain) drives the conversation: on_enter voices the deterministic
opener; at each turn boundary on_user_turn_completed emits an IMMEDIATE persona acknowledgment
to MASK the brain, then runs the brain as a cancellable Task IN PARALLEL (never a silent wait)
and stages its confirmed Directive when it lands, superseding the speculative pre-stage. While
the candidate is still answering, a NON-voiced speculative pre-stage is staged from the
user-speaking handler so the controller's supersede/discard runs live; barge-in cancels the
in-flight brain Task. The mouth is a _MouthAgent that overrides llm_node to voice the
controller's current Directive in persona (GPT-5.4-mini "Arjun") via a bounded, cache-stable
prompt. The AgentSession wiring + behavioral silence-timer + EOU config carry forward; the
hold-space / unresponsive-ladder / ack-mask reflex cues are persona pre-rendered once at session
start (canned Settings strings are the seed + fallback). Per-turn latency is sourced from
ChatMessage.metrics via conversation_item_added (the working 1.5.9 signal; session-level
metrics_collected emits no llm/eou metrics).

This module also carries the worker bootstrap (`server`/`prewarm`/`entrypoint`). It imports
livekit, and is only ever imported lazily via interview_engine.__getattr__('run' / 'server')
inside the engine container, so the FastAPI/nexus process never loads livekit.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    StopResponse,
    TurnHandlingOptions,
    UserStateChangedEvent,
    room_io,
)
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.ai.config import ai_config
from app.ai.otel import bootstrap_tracer_provider
from app.ai.prompts import PromptLoader
from app.ai.realtime import (
    build_interruption_options,
    build_mouth_llm_plugin,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
    build_vad,
)
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.audio_metrics import compute_audio_summary
from app.modules.interview_engine.brain import ControlPlane
from app.modules.interview_engine.brain.service import build_speculative_directive
from app.modules.interview_engine.controller import DirectiveController
from app.modules.interview_engine.coverage import CoverageTracker
from app.modules.interview_engine.directive import Directive, DirectiveAct, DirectiveTone
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.event_log.sink import LocalFileSink
from app.modules.interview_engine.mouth.input_builder import is_question_bearing
from app.modules.interview_engine.mouth.service import ConversationPlane
from app.modules.interview_engine.result_builder import build_v2_session_result
from app.modules.interview_engine.triage import TriagePlane
from app.modules.interview_engine.triage.decision import TriageKind, TriageRoute
from app.modules.interview_engine.turn_taking.eou import (
    EouConfig,
    LadderAction,
    UnresponsiveLadder,
    is_backchannel,
)
from app.modules.interview_engine.turn_taking.floor import (
    ResumptionSignals,
    classify_resumption,
    should_yield,
)
from app.modules.interview_engine.turn_taking.pacing import (
    EndpointingSettings,
    HoldSpacePacer,
    build_endpointing_options,
)
from app.modules.interview_runtime import (
    SessionConfig,
    TranscriptEntry,
    build_session_config,
    record_engine_heartbeat,
    record_session_result,
)
from app.modules.session import classify_engine_exception, transition_to_error

log = structlog.get_logger("interview_engine")

_KEYTERM_CAP = 50


server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Process-startup hook: bootstrap an OTel TracerProvider so livekit-agents'
    built-in spans ship to whatever OTLP endpoint the operator configures.
    Production-safe default: no env vars set -> spans go nowhere."""
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)


server.setup_fnc = prewarm


def assemble_v2_keyterms(*, candidate_first_name: str, bank_keyterms: list[str]) -> list[str]:
    """v2-native keyterm pass (self-contained; no import of interview_engine/).

    Candidate first name + cached bank keyterms, case-insensitive dedup, capped.
    """
    terms: list[str] = []

    def _add(term: str) -> None:
        t = term.strip()
        if not t or len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == x.lower() for x in terms):
            return
        terms.append(t)

    if candidate_first_name.strip():
        _add(candidate_first_name.split()[0])
    for term in bank_keyterms:
        _add(term)
    return terms


# RETAINED (not instantiated by run() in M5): a tested pure reference / debug-harness seed. The
# M5 run() drives Directives from the ControlPlane brain instead; this scripted source + its
# test_harness_script.py stay green as a deterministic harness for offline debugging.
@dataclass
class DirectiveScript:
    """Hand-scripted Directives for the M4 talk-test (no brain). Mirrors the M3 bank flow:
    INTRO + ASK(q1) at startup, then ACK_ADVANCE to each remaining question per turn, then CLOSE.
    `scenario="supersession"` exposes a speculative+superseder pair for the CMI-4 live test."""

    questions: list[str]
    scenario: str = ""
    _startup_idx: int = field(default=0, init=False)
    _q_idx: int = field(default=1, init=False)      # q[0] asked at startup; q[1..] via ACK_ADVANCE
    _closed: bool = field(default=False, init=False)
    _seq: int = field(default=0, init=False)

    def _id(self) -> str:
        self._seq += 1
        return f"d-{self._seq}"

    def next_startup(self) -> Directive | None:
        """INTRO (idx 0), then ASK q[0] (idx 1); None afterwards."""
        if self._startup_idx == 0:
            self._startup_idx = 1
            return Directive(id=self._id(), turn_ref="t-0", act=DirectiveAct.INTRO,
                             say=None, compose_hint="warm, brief, set them at ease",
                             tone=DirectiveTone.WARM)
        if self._startup_idx == 1:
            self._startup_idx = 2
            if not self.questions:
                return None
            return Directive(id=self._id(), turn_ref="t-0", act=DirectiveAct.ASK,
                             say=self.questions[0])
        return None

    def next_after_turn(self, *, turn_ref: str) -> Directive | None:
        """ACK_ADVANCE to the next question, else one CLOSE. Empty-bank edge: with no
        questions, _q_idx (1) is never < len (0), so the first call falls straight through
        to a single CLOSE; the _closed flag returns None thereafter."""
        if self._closed:
            return None
        if self._q_idx < len(self.questions):
            say = self.questions[self._q_idx]
            self._q_idx += 1
            return Directive(id=self._id(), turn_ref=turn_ref, act=DirectiveAct.ACK_ADVANCE, say=say)
        self._closed = True
        return Directive(id=self._id(), turn_ref=turn_ref, act=DirectiveAct.CLOSE, say=None,
                         compose_hint="thank warmly; recruiter will follow up",
                         tone=DirectiveTone.WARM, is_terminal=True)

    def supersession_pair(self, *, turn_ref: str) -> tuple[Directive, Directive]:
        """CMI-4: a speculative PROBE pre-stage + a superseding ACK_ADVANCE for the same turn."""
        spec = Directive(id=self._id(), turn_ref=turn_ref, act=DirectiveAct.PROBE,
                         say="What part of that did you build yourself?", speculative=True)
        real = Directive(id=self._id(), turn_ref=turn_ref, act=DirectiveAct.ACK_ADVANCE,
                         say=(self.questions[1] if len(self.questions) > 1 else "Let's move on."),
                         supersedes=spec.id)
        return spec, real


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _leading_bridge(text: str) -> str | None:
    """Extract the opening connective from a spoken line for recent_bridges variety-tracking.

    LEXICAL slice — purely for bookkeeping that the mouth varies its opener; NOT intent/semantic
    classification (no regex ban applies here; this is the same pattern as the test-suite's
    _leading_chunk used in the double-open evals).

    Rules:
    - If an em-dash '—' appears within the first ~12 words, return everything up to and
      including that dash (e.g. 'and on that —').
    - Otherwise return the first ~6 words.
    - Returns None if the result would be empty.
    """
    if not text or not text.strip():
        return None
    words = text.strip().split()
    dash_pos = text.find("—")
    if dash_pos != -1:
        # Check the dash is within the first ~12 words (i.e. roughly the opening phrase).
        words_before_dash = text[:dash_pos].split()
        if len(words_before_dash) <= 12:
            bridge = text[:dash_pos + 1].strip()   # include the em-dash itself
            return bridge if bridge else None
    # No early em-dash — just take the first 6 words as the "opening" for tracking.
    snippet = " ".join(words[:6])
    return snippet if snippet else None


class _MouthAgent(Agent):
    """Voices the controller's current Directive in persona via the LLM (no canned text)."""

    def __init__(self, *, controller: DirectiveController, mouth: ConversationPlane,
                 brain: ControlPlane, collector: EventCollector,
                 ladder: UnresponsiveLadder, started_at: float, state: dict[str, object],
                 pose_question: Callable[[float], None], correlation_id: str,
                 triage: TriagePlane) -> None:
        super().__init__(instructions="")          # persona lives in the per-turn ctx, not here
        self._controller = controller
        self._mouth = mouth
        self._brain = brain
        self._collector = collector
        self._ladder = ladder
        self._started_at = started_at
        self._state = state
        self._pose_question = pose_question
        self._correlation_id = correlation_id
        self._turn_seq = 0
        self._current_turn_ref = "t-0"              # INTRO/first-ASK live on t-0
        self._last_candidate_text: str | None = None
        self._transcript: list[tuple[str, str]] = []   # (role, text) window for the brain
        self._brain_task: asyncio.Task | None = None   # in-flight confirm/decide (barge-in cancels)
        self._spec_id: str | None = None               # id of the staged speculative pre-stage
        self._result_transcript: list[TranscriptEntry] = []  # reliable result transcript
        self._triage = triage
        self._triage_task: asyncio.Task | None = None
        self._pending_answer: list[str] = []     # candidate fragments in the current answer episode
        self._last_filler: str | None = None      # triage just spoke -> mouth Pass-2 bridge
        self._hold_count: int = 0                  # consecutive still-pending holds this episode
        # per-turn delivery guards (reset at the top of on_user_turn_completed)
        self._answer_delivered: bool = False       # brain delivered the question this turn
        self._handled_log_only: bool = False       # dev disagreement-log: brain ran but stays mute
        self._recent_fillers: deque[str] = deque(maxlen=4)  # fed to triage so it varies its opener
        # the mouth's recent opening connectives -> fed back so it varies its bridge
        self._recent_bridges: deque[str] = deque(maxlen=4)

    def _t_ms(self) -> int:
        return int((time.monotonic() - self._started_at) * 1000)

    def prestage_speculative(self) -> None:
        """Option C: while the candidate answers, stage a NON-voiced speculative directive for the
        anticipated next turn so the controller's supersede/discard runs live (CMI-4). Best-effort.
        """
        if self._state.get("closing"):
            return
        anticipated = f"t-{self._turn_seq + 1}"
        spec = build_speculative_directive(self._brain, anticipated_turn_ref=anticipated)
        self._controller.stage(spec)
        self._spec_id = spec.id
        self._collector.record(
            "directive.speculative_staged",
            {"id": spec.id, "act": spec.act.value, "turn_ref": anticipated},
            t_ms=self._t_ms(), wall_ms=_now_ms())

    def cancel_brain(self) -> None:
        """Barge-in / teardown: cancel the in-flight brain decide() Task cleanly (CMI-4)."""
        task = self._brain_task
        if task is not None and not task.done():
            task.cancel()

    def cancel_triage(self) -> None:
        """Barge-in / HANDLED: cancel the in-flight triage Task cleanly."""
        task = self._triage_task
        if task is not None and not task.done():
            task.cancel()

    def _active_question_text(self) -> str | None:
        """The active question for triage's completeness judgment = the mouth's REPEAT cache."""
        return self._mouth.last_question

    def _finish_answer_episode(self) -> None:
        """A directive was delivered -> the answer is consumed; reset the accumulation episode."""
        self._pending_answer.clear()
        self._hold_count = 0
        self._answer_delivered = True

    def _say_filler(self, line: str) -> None:
        """TO_BRAIN: speak the masking filler now; store it so Pass-2 continues from it."""
        if self._answer_delivered:        # brain already delivered (rare race) -> no stray filler
            return
        self._last_filler = line
        self._state["responding"] = True
        self._result_transcript.append(                  # record what the candidate actually heard
            TranscriptEntry(role="agent", text=line, timestamp_ms=self._t_ms()))
        self.session.say(line, add_to_chat_ctx=False)   # fire-and-forget; question queues behind it

    def _say_hold_cue(self, line: str) -> None:
        """HANDLED still-pending: a continuation cue, NOT a question. §9: skip if an acoustic
        hold-space cue fired within the cooldown (don't double-cue). When no cue actually fires,
        clear `responding` so the silence watcher / unresponsive ladder is not left muted (the
        turn set responding=True to mask reasoning; a suppressed cue would otherwise wedge it)."""
        if self._answer_delivered:                   # brain already delivered -> no stray cue
            self._state["responding"] = False
            return
        now = time.monotonic()
        last = self._state.get("last_cue_at")
        if isinstance(last, (int, float)) and (now - last) < settings.engine_v2_cue_cooldown_s:
            self._state["responding"] = False        # cooldown-suppressed: nothing is playing
            return
        self._state["last_cue_at"] = now
        self._state["responding"] = True
        self._result_transcript.append(                  # record what the candidate actually heard
            TranscriptEntry(role="agent", text=line, timestamp_ms=self._t_ms()))
        self.session.say(line, add_to_chat_ctx=False)
        # The candidate still owes an answer; re-arm the unresponsive ladder when this cue's TTS
        # drains (agent_state -> 'listening' consumes pending_arm -> _pose_question), so a
        # hold-then-silent candidate still reaches the 7s/15s/CLOSE escalation (HANDLED turns
        # deliver no directive, so llm_node never sets pending_arm).
        self._state["pending_arm"] = True

    def _deliver_repeat(self, turn_ref: str, *, lead_in: str | None) -> None:
        """HANDLED repeat: stage a REPEAT directive (mouth replays the cached last question) and
        deliver via Pass-2; the triage lead-in ('Sure —') flows in as the filler."""
        if self._answer_delivered:               # brain already delivered -> don't double-deliver
            return
        self._last_filler = lead_in
        self._controller.stage(Directive(
            id=f"rpt-{turn_ref}", turn_ref=turn_ref, act=DirectiveAct.REPEAT, say=None))
        self._finish_answer_episode()
        self._state["brain_pending"] = False
        self.session.generate_reply()    # routes through llm_node -> mouth REPEAT (filler-aware)

    async def on_enter(self) -> None:
        # Deliver the deterministic opener: INTRO then ASK(first bank question) proactively (no
        # candidate turn precedes them). Each generate_reply routes through llm_node, which voices
        # the staged directive. No brain call before the first answer (D4).
        intro, ask = self._brain.opener()
        for d in (intro, ask):
            self._controller.stage(d)
            self._current_turn_ref = d.turn_ref           # both live on t-0
            if d.say:
                self._transcript.append(("agent", d.say))
            await self.session.generate_reply()

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Separate clocks (D3 / design §3): launch TRIAGE ∥ BRAIN at the same instant; never await.
        # Triage gates the immediate voice (filler / hold / repeat); the brain gates the eventual
        # question, delivered when it lands (StopResponse + done-callbacks). HANDLED cancels the
        # speculatively-launched brain (accepted waste). Barge-in cancels both.
        text = new_message.text_content or ""
        self._last_candidate_text = text
        self._transcript.append(("candidate", text))
        if text.strip():
            self._pending_answer.append(text)        # accumulate fragments for this answer episode
            self._result_transcript.append(
                TranscriptEntry(role="candidate", text=text, timestamp_ms=self._t_ms()))
        word_count = len([w for w in text.split() if w])
        backchannel = is_backchannel(text, min_words=settings.engine_v2_backchannel_min_words)

        _now = time.monotonic()
        _last_listen = self._state.get("last_listening_at")
        pause_before_commit_ms = (int((_now - _last_listen) * 1000)
                                  if isinstance(_last_listen, (int, float)) else None)
        if should_yield(word_count=word_count, is_backchannel=backchannel):
            self._ladder.on_candidate_responded()
        label = classify_resumption(ResumptionSignals(
            prior_utterance_complete=True, gap_ms=0, ai_prompt_fully_delivered=True,
            word_count=word_count, is_backchannel=backchannel))
        self._collector.record(
            "turn.captured",
            {"word_count": word_count, "is_backchannel": backchannel,
             "resumption_label": label.value, "pause_before_commit_ms": pause_before_commit_ms},
            t_ms=self._t_ms(), wall_ms=_now_ms())
        log.info("engine.v2.turn_committed", word_count=word_count,
                 pause_before_commit_ms=pause_before_commit_ms, resumption_label=label.value)

        self._turn_seq += 1
        turn_ref = f"t-{self._turn_seq}"
        self._current_turn_ref = turn_ref
        accumulated = " ".join(self._pending_answer).strip() or text

        # reset per-turn delivery guards
        self._answer_delivered = False
        self._handled_log_only = False
        self._state["responding"] = True
        self._state["brain_pending"] = True          # mutes reflex cues for the reasoning window

        # --- launch both tiers at the SAME instant (separate clocks; neither awaited) ---
        self._triage_task = asyncio.create_task(self._triage.triage(
            active_question=self._active_question_text(),
            accumulated_answer=accumulated,
            last_spoken_question=self._mouth.last_question,
            recent_fillers=list(self._recent_fillers),
            correlation_id=self._correlation_id))
        self._brain_task = asyncio.create_task(self._brain.decide(
            turn_ref=turn_ref, candidate_utterance=accumulated,
            transcript_window=list(self._transcript), correlation_id=self._correlation_id))

        def _on_triage_done(task: asyncio.Task) -> None:
            if task.cancelled() or self._current_turn_ref != turn_ref:
                return                               # barge-in / stale turn -> no-op
            try:
                d = task.result()
            except Exception:                        # noqa: BLE001 — triage callback must not crash
                log.warning("engine.v2.triage.callback_failed", exc_info=True)
                return
            self._collector.record(
                "engine.v2.triage.decision",
                {"kind": d.kind.value, "route": d.route.value, "answer_complete": d.answer_complete,
                 "replay": d.replay_last_question, "spoken_line": d.spoken_line,
                 "turn_ref": turn_ref},
                t_ms=self._t_ms(), wall_ms=_now_ms())
            if d.spoken_line:
                self._recent_fillers.append(d.spoken_line)   # so the next turn varies its opener
            still_pending = (d.kind is TriageKind.answering and not d.answer_complete)

            if d.route is TriageRoute.handled and d.kind is TriageKind.repeat_request:
                self.cancel_brain()
                self._deliver_repeat(turn_ref, lead_in=d.spoken_line)
                return

            if d.route is TriageRoute.handled and still_pending:
                self._hold_count += 1
                if self._hold_count > settings.engine_triage_hold_cap:
                    # hold cap reached -> force TO_BRAIN: keep the brain running, neutral filler,
                    # the brain delivers on the FULL accumulated answer (design §4.4/§4.6).
                    self._say_filler(d.spoken_line)
                    return
                # genuine hold: speak a continuation cue, accumulate, skip the brain this turn.
                if settings.engine_v2_triage_brain_disagreement_log:
                    self._handled_log_only = True    # let the brain finish only to log (dev §7)
                else:
                    self.cancel_brain()
                self._say_hold_cue(d.spoken_line)
                self._state["brain_pending"] = False
                return

            # route == to_brain (or any non-handled): masking filler, brain delivers the question.
            self._say_filler(d.spoken_line)

        def _on_brain_done(task: asyncio.Task) -> None:
            if self._current_turn_ref != turn_ref:
                return                               # stale: a newer turn owns _brain_task + flags
            self._brain_task = None
            if task.cancelled():                     # this turn's brain was cancelled (HANDLED/etc)
                self._state["brain_pending"] = False
                return
            try:
                directive, record = task.result()
            except Exception:                        # noqa: BLE001 — brain callback must not crash
                log.warning("engine.v2.brain.callback_failed", exc_info=True)
                self._state["brain_pending"] = False
                return
            if self._handled_log_only:
                # dev disagreement-log: triage HANDLED this turn; record the brain's would-be move
                # but DO NOT speak it (never change what's spoken — design §7).
                self._collector.record(
                    "engine.v2.triage_brain_disagreement",
                    {"turn_ref": turn_ref, "brain_act": directive.act.value},
                    t_ms=self._t_ms(), wall_ms=_now_ms())
                self._state["brain_pending"] = False
                return
            # supersede a still-staged speculative pre-stage (Option C / CMI-4); stage the directive
            if self._spec_id is not None and self._controller.staged_id() == self._spec_id:
                directive = directive.model_copy(update={"supersedes": self._spec_id})
            self._spec_id = None
            self._controller.stage(directive)
            self._collector.record_decision(record, t_ms=self._t_ms(), wall_ms=_now_ms())
            if directive.say:
                self._transcript.append(("agent", directive.say))
            if directive.is_terminal:
                self._state["closing"] = True
            self._finish_answer_episode()            # answer consumed -> reset the episode
            self._state["brain_pending"] = False
            self.session.generate_reply()            # mouth Pass-2 continues from the filler

        self._triage_task.add_done_callback(_on_triage_done)
        self._brain_task.add_done_callback(_on_brain_done)
        raise StopResponse()                         # suppress the framework auto-reply (spike §1)

    async def llm_node(self, chat_ctx, tools, model_settings):
        directive = self._controller.current_for_turn(self._current_turn_ref)
        if directive is None:
            raise StopResponse()                       # nothing current (stale/discarded)
        self._state["responding"] = True               # cleared by agent_state once playout ends
        self._controller.mark_delivered(directive.id)
        self._collector.record("directive.delivered",
            {"id": directive.id, "act": directive.act.value, "turn_ref": directive.turn_ref,
             "speculative": directive.speculative}, t_ms=self._t_ms(), wall_ms=_now_ms())
        messages = self._mouth.build_turn_messages(
            directive, candidate_utterance=self._last_candidate_text,
            just_said_filler=self._last_filler,
            recent_bridges=list(self._recent_bridges))
        self._last_candidate_text = None               # consumed; not carried to the next turn
        self._last_filler = None                       # consumed; one bridge per delivery
        ctx = ChatContext.empty()
        for m in messages:
            ctx.add_message(role=m["role"], content=m["content"])
        if directive.is_terminal:
            self._state["closing"] = True
        # Defer arming the unresponsive ladder until the agent actually FINISHES speaking
        # this question (agent_state -> 'listening'), so the candidate gets the full patience
        # window AFTER hearing it. INTRO is not a posed question; CLOSE is terminal.
        if not directive.is_terminal and directive.act is not DirectiveAct.INTRO:
            self._state["pending_arm"] = True
        spoken_parts: list[str] = []
        async for chunk in Agent.default.llm_node(self, ctx, tools, model_settings):
            delta = getattr(getattr(chunk, "delta", None), "content", None)
            if isinstance(delta, str):
                spoken_parts.append(delta)
            yield chunk
        spoken = "".join(spoken_parts).strip()
        if spoken:
            self._result_transcript.append(
                TranscriptEntry(role="agent", text=spoken, timestamp_ms=self._t_ms()))
            # For question-bearing acts, record the leading connective so the next turn avoids it.
            # LEXICAL slice for variety-tracking only — NOT intent/semantic classification (no ban).
            if is_question_bearing(directive.act):
                bridge = _leading_bridge(spoken)
                if bridge:
                    self._recent_bridges.append(bridge)
        # NOTE: no _pose_question / aclose here. The ladder is armed by the agent_state
        # 'listening' handler (after TTS playout); termination is M5 (CMI-1).


async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Per-session engine run: connect, drive the triage ∥ brain → mouth turn loop, record the SessionResult."""
    started_at = time.monotonic()
    collector = EventCollector(
        session_id=config.session_id,
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    collector.record(
        "engine.v2.dispatched",
        {"job_title": config.job_title, "question_count": len(config.stage.questions)},
        t_ms=0, wall_ms=_now_ms(),
    )

    await ctx.connect()
    await ctx.wait_for_participant()

    keyterms = assemble_v2_keyterms(
        candidate_first_name=config.candidate.name,
        bank_keyterms=list(config.keyterms),
    )
    endpointing = build_endpointing_options(EndpointingSettings(
        mode=ai_config.engine_v2_endpointing_mode,
        min_delay=ai_config.engine_v2_endpointing_min_delay,
        max_delay=ai_config.engine_v2_endpointing_max_delay,
    ))
    session = AgentSession(
        stt=build_stt_plugin(keyterms=keyterms),
        llm=build_mouth_llm_plugin(),                 # the mouth voices via the LLM node
        tts=build_tts_plugin(),
        vad=build_vad(),
        user_away_timeout=None,                       # the manual ladder owns unresponsive behavior
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(
                unlikely_threshold=ai_config.engine_v2_turn_detector_unlikely_threshold,
            ),
            preemptive_generation={"enabled": False},  # quality-before-latency lock
            endpointing=endpointing,
            interruption=build_interruption_options(),
        ),
    )

    # --- legacy session metrics (kept; tts still flows here). CMI-3 authoritative source is below. ---
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        # NOTE: in LK 1.5.9 this session-level event yields TTS audio.metrics only — NOT
        # llm/eou latency. The CMI-3 latency source is the conversation_item_added handler.
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        collector.record(f"audio.metrics.{m.type}", payload,
                         t_ms=int((time.monotonic() - started_at) * 1000),
                         wall_ms=_now_ms())

    # --- CMI-3 (mouth half): per-turn latency from ChatMessage.metrics (the working signal) ---
    @session.on("conversation_item_added")
    def _on_item(ev: object) -> None:
        item = getattr(ev, "item", None)
        if not isinstance(item, ChatMessage):
            return
        m = item.metrics or {}
        if item.role == "assistant" and m.get("llm_node_ttft") is not None:
            collector.record(
                "turn.latency.assistant",
                {
                    "llm_node_ttft": m.get("llm_node_ttft"),
                    "tts_node_ttfb": m.get("tts_node_ttfb"),
                    "e2e_latency": m.get("e2e_latency"),
                },
                t_ms=int((time.monotonic() - started_at) * 1000),
                wall_ms=_now_ms(),
            )
        elif item.role == "user" and m.get("end_of_turn_delay") is not None:
            collector.record(
                "turn.latency.user",
                {
                    "end_of_turn_delay": m.get("end_of_turn_delay"),
                    "transcription_delay": m.get("transcription_delay"),
                },
                t_ms=int((time.monotonic() - started_at) * 1000),
                wall_ms=_now_ms(),
            )

    # --- behavioral layer: one silence timer ticks the pure pacer + ladder ---
    ladder = UnresponsiveLadder(EouConfig(
        prompt_1_s=ai_config.engine_v2_unresponsive_prompt_1_s,
        prompt_2_s=ai_config.engine_v2_unresponsive_prompt_2_s,
        max_no_responses=ai_config.engine_v2_unresponsive_max_no_responses,
    ))
    pacer = HoldSpacePacer(
        enabled=ai_config.engine_v2_hold_space_enabled,
        delay_s=ai_config.engine_v2_hold_space_delay_s,
    )
    state: dict[str, object] = {
        "started_answering": False, "responding": False,
        "closing": False, "silence_task": None,
        "last_listening_at": None, "reflex": None, "reflex_task": None,
        "pending_arm": False,
        # True from turn-commit until the brain's directive is staged. The masking ack flips
        # agent_state->listening (clearing `responding`) WHILE the brain still reasons, so
        # `responding` alone doesn't cover the reasoning window — without this, the hold-space cue
        # fires "take your time" at a candidate who's actually waiting on the AGENT (ec11e237).
        "brain_pending": False,
        "result_recorded": False,   # once-guard for _finalize_and_record
        "close_initiated": False,   # once-guard for session.aclose() after terminal CLOSE
        "last_cue_at": None,    # §9 cue-cooldown timestamp (set by triage hold + acoustic pacer)
    }

    mouth = ConversationPlane(
        loader=PromptLoader(version=ai_config.engine_mouth_prompt_version),
        persona_name=(ai_config.engine_mouth_persona_name or settings.engine_agent_name),
        job_title=config.job_title,
        role_summary=config.role_summary,   # for the INTRO brief (warm the candidate on the role)
    )
    triage = TriagePlane(
        persona_name=(ai_config.engine_mouth_persona_name or settings.engine_agent_name),
        job_title=config.job_title,
    )
    # The brain (ControlPlane) — not a script — sources every Directive in M5 (D3).
    mandatory = [q.primary_signal for q in config.stage.questions
                 if q.is_mandatory and q.primary_signal]
    coverage = CoverageTracker(
        signals=(list(config.signals)
                 or [q.primary_signal for q in config.stage.questions if q.primary_signal]),
        mandatory_signals=mandatory,
        soft_probe_cap=2,
    )
    brain = ControlPlane(config=config, coverage=coverage)
    controller = DirectiveController()

    def _reflex(kind: str, fallback: str) -> str:
        """Pick a persona-voiced reflex variant if pre-rendered, else the canned seed."""
        variants = state.get("reflex")
        pool = getattr(variants, kind, None) if variants is not None else None
        return random.choice(pool) if pool else fallback

    def _pose_question(at_s: float) -> None:
        """Arm the ladder for a freshly-posed question and reset turn state."""
        ladder.on_question_posed(at_s=at_s)
        pacer.on_resume()
        state["started_answering"] = False

    async def _silence_watch() -> None:
        """Tick the pacer (mid-answer) OR the ladder (pre-answer) while silent."""
        while not state["closing"]:
            await asyncio.sleep(0.5)
            try:
                if state["responding"] or state["closing"] or state["brain_pending"]:
                    continue                          # mute all reflex cues while the brain reasons
                now = time.monotonic()
                if state["started_answering"]:
                    # Incompleteness gate (M5 decision E / R3): LiveKit's
                    # MultilingualModel does NOT expose a mid-pause "still-
                    # extending" signal, so we use the delay-above-commit-
                    # latency proxy. A COMPLETE answer commits via
                    # on_user_turn_completed which sets state["responding"]=True
                    # (typically within ~1-2s); that mutes this branch before
                    # the cue delay (3.0s) elapses. Only a detector-held-open
                    # INCOMPLETE pause — which the turn-detector holds up to
                    # engine_v2_endpointing_max_delay (10s) — survives here.
                    # The brain's HOLD directive (Task 6, fires at a committed
                    # turn boundary) is a separate, complementary path.
                    # v1 caveat: "never on a complete answer" is best-effort
                    # with text-only detection; perfect needs the v2 audio
                    # model. Validate on Task 10 talk-test.
                    if pacer.cue_due(now_s=now):
                        last_cue = state.get("last_cue_at")
                        if (isinstance(last_cue, (int, float))
                                and (now - last_cue) < settings.engine_v2_cue_cooldown_s):
                            continue              # §9: skip — triage hold already cued
                        pacer.mark_cued()
                        state["last_cue_at"] = now
                        log.info("engine.v2.holdspace",
                                 t_ms=int((now - started_at) * 1000))
                        state["responding"] = True
                        try:
                            await session.say(
                                _reflex("hold_space", settings.engine_v2_hold_space_message),
                                add_to_chat_ctx=False)
                        finally:
                            state["responding"] = False
                    continue
                action = ladder.action(now_s=now)
                if action is LadderAction.NONE:
                    continue
                state["responding"] = True
                try:
                    if action is LadderAction.PROMPT_1:
                        await session.say(
                            _reflex("gentle_nudge", settings.engine_v2_unresponsive_message_1),
                            add_to_chat_ctx=False)
                    elif action is LadderAction.PROMPT_2:
                        await session.say(
                            _reflex("still_there", settings.engine_v2_unresponsive_message_2),
                            add_to_chat_ctx=False)
                        ladder.on_question_posed(at_s=now)
                    elif action is LadderAction.CLOSE_UNRESPONSIVE:
                        state["closing"] = True
                        collector.record("engine.v2.candidate_unresponsive", {},
                                         t_ms=int((now - started_at) * 1000),
                                         wall_ms=_now_ms())
                        await session.say(
                            _reflex("still_there", settings.engine_v2_unresponsive_message_2),
                            add_to_chat_ctx=False)
                        state["close_initiated"] = True
                        # Record-before-close, same as the clean terminal path: persist a
                        # (partial) result reliably, then aclose() (deletes room/evicts).
                        await _terminate_session()
                finally:
                    state["responding"] = False
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("engine.v2.silence_watch.tick_failed", exc_info=True)
                state["responding"] = False

    # Forward-declared so the run()-level user-speaking handler can reach the agent's Option-C
    # pre-stage + barge-in cancel; assigned below at the original construction site.
    agent: _MouthAgent | None = None

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        now = time.monotonic()
        if ev.new_state == "speaking":
            state["started_answering"] = True
            pacer.on_resume()
            if agent is not None:
                agent.cancel_brain()         # barge-in: cancel any in-flight brain decision (CMI-4)
                agent.cancel_triage()        # …and the in-flight triage (design §7)
                agent.prestage_speculative()  # Option C: stage the non-voiced speculative pre-stage
        elif ev.new_state == "listening":
            if state["started_answering"]:
                pacer.on_pause_started(at_s=now)
            state["last_listening_at"] = now
        collector.record("audio.user.state",
                         {"old_state": ev.old_state, "new_state": ev.new_state},
                         t_ms=int((now - started_at) * 1000), wall_ms=_now_ms())

    @session.on("agent_state_changed")
    def _on_agent_state(ev: object) -> None:
        ns = str(getattr(ev, "new_state", ""))
        if ns in ("thinking", "speaking"):
            state["responding"] = True
        elif ns in ("listening", "idle"):
            state["responding"] = False
            # The agent just finished speaking. If that was a posed question, arm the
            # unresponsive ladder NOW (patient window starts after the candidate hears it).
            if ns == "listening" and state.get("pending_arm"):
                state["pending_arm"] = False
                log.info("engine.v2.ladder_armed",
                         t_ms=int((time.monotonic() - started_at) * 1000))
                _pose_question(time.monotonic())
            # Terminal CLOSE has fully drained (agent back to 'listening'): RECORD the
            # result on a healthy connection FIRST, THEN close. Recording before aclose()
            # (rather than only in the shutdown callback) keeps the durable DB write off
            # the shutdown_process_timeout (10s) critical path — see _terminate_session.
            # delete_room_on_close then evicts the candidate + deletes the room.
            if ns == "listening" and state.get("closing") and not state.get("close_initiated"):
                state["close_initiated"] = True
                asyncio.create_task(_terminate_session())

    async def _heartbeat_loop() -> None:
        """Pulse last_engine_heartbeat_at every engine_heartbeat_interval_seconds so the
        stuck-session reaper treats this (possibly long) interview as alive. The first
        beat fires immediately; a missed beat is logged, never fatal; the loop ends when
        finalize cancels it (the row is about to leave 'active')."""
        session_uuid = uuid.UUID(config.session_id)
        while True:
            try:
                async with get_bypass_session() as hb_db:
                    await record_engine_heartbeat(
                        hb_db, session_id=session_uuid, tenant_id=tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a missed beat must never crash the session
                log.warning("engine.v2.heartbeat_failed", exc_info=True)
            await asyncio.sleep(settings.engine_heartbeat_interval_seconds)

    # ---------------------------------------------------------------------------
    # _finalize_and_record: async shutdown hook (CMI-1 result path).
    # Runs once via ctx.add_shutdown_callback — async so DB writes can be awaited.
    # The M4 sync @session.on("close") handler stays for the audio-summary log;
    # the DB write lives here (sync close cannot await).
    # knockout_failures=[] for M5: the brain RECORDs a knockout via the audit
    # turn.decision event; mapping it into the typed KnockoutFailure list is a
    # small follow-up not required for M5 acceptance ("records, never rejects").
    # ---------------------------------------------------------------------------
    async def _finalize_and_record() -> None:
        if state.get("result_recorded"):
            return
        state["result_recorded"] = True
        state["closing"] = True
        # Stop the liveness pulse — the session is about to leave 'active'.
        hb_task = state.get("heartbeat_task")
        if hb_task is not None:
            hb_task.cancel()
        env = collector.envelope(closed_at=_now_iso())
        envelope_ref: str | None = None
        if settings.engine_event_log_sink == "local":
            try:
                envelope_ref = LocalFileSink(
                    directory=settings.engine_event_log_dir
                ).write(env)
            except Exception:  # noqa: BLE001 — never let envelope write block result recording
                log.warning("engine.v2.envelope_write_failed", exc_info=True)
        summary = compute_audio_summary(
            events=[e.model_dump(mode="json") for e in env.events],
            config_snapshot={
                "endpointing_mode": ai_config.engine_v2_endpointing_mode,
                "endpointing_min_delay": ai_config.engine_v2_endpointing_min_delay,
                "endpointing_max_delay": ai_config.engine_v2_endpointing_max_delay,
                "turn_detector_unlikely_threshold": (
                    ai_config.engine_v2_turn_detector_unlikely_threshold
                ),
            },
        )
        result = build_v2_session_result(
            config=config,
            coverage=coverage,
            transcript=list(agent._result_transcript) if agent is not None else [],
            envelope=env,
            audio_summary=summary,
            knockout_failures=[],
            duration_seconds=(time.monotonic() - started_at),
            completed_at=_now_iso(),
            audit_envelope_ref=envelope_ref,
        )
        try:
            async with get_bypass_session() as db:
                # record_session_result commits the completion durably itself
                # (then best-effort-enqueues report scoring); no commit here.
                await record_session_result(
                    db,
                    session_id=uuid.UUID(config.session_id),
                    tenant_id=tenant_id,
                    result=result,
                    correlation_id=correlation_id,
                )
            log.info(
                "engine.v2.result.persisted",
                session_id=config.session_id,
                questions_asked=result.questions_asked,
            )
        except Exception:  # noqa: BLE001 — log + swallow; teardown must complete
            log.error("engine.v2.result.persist_failed", exc_info=True)

    async def _terminate_session() -> None:
        """Clean terminal-close path: persist the result on a healthy connection
        FIRST (off the shutdown_process_timeout critical path), THEN close the
        session — delete_room_on_close evicts the candidate + deletes the room.
        _finalize_and_record is once-guarded, so the shutdown-callback fallback
        (for non-clean ends: disconnect/crash) is a no-op after this runs."""
        try:
            await _finalize_and_record()
        finally:
            await session.aclose()

    # Register the async shutdown callback (LiveKit 1.5.x — see ctx.add_shutdown_callback docs).
    # The framework awaits all shutdown callbacks before terminating the job process (default 10s
    # timeout, configurable via shutdown_process_timeout in WorkerOptions).
    ctx.add_shutdown_callback(_finalize_and_record)

    agent = _MouthAgent(controller=controller, mouth=mouth, brain=brain, collector=collector,
                        ladder=ladder, started_at=started_at, state=state,
                        pose_question=_pose_question, correlation_id=correlation_id,
                        triage=triage)

    nc_filter = build_noise_cancellation()
    await session.start(
        agent=agent, room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=nc_filter),
            delete_room_on_close=True,
        ),
    )
    # Liveness heartbeat: now that the engine session is running, pulse the DB so the
    # stuck-session reaper never mistakes a long-but-live interview for a dead engine.
    state["heartbeat_task"] = asyncio.create_task(_heartbeat_loop())
    # INTRO + first ASK are delivered by _MouthAgent.on_enter (no explicit say() block here).

    # Pre-render persona reflex cues in the background (HOLD/REASSURE decision); seeds = canned.
    async def _prime_reflex() -> None:
        state["reflex"] = await mouth.prerender_reflex_variants(
            hold_seed=settings.engine_v2_hold_space_message,
            nudge_seed=settings.engine_v2_unresponsive_message_1,
            still_seed=settings.engine_v2_unresponsive_message_2,
            ack_seed=list(settings.engine_v2_ack_messages))
    state["reflex_task"] = asyncio.create_task(_prime_reflex())

    state["silence_task"] = asyncio.create_task(_silence_watch())

    @session.on("close")
    def _on_close(_ev: object) -> None:
        state["closing"] = True
        if agent is not None:
            agent.cancel_brain()
        for key in ("silence_task", "reflex_task"):
            task = state.get(key)
            if isinstance(task, asyncio.Task):
                task.cancel()
        env = collector.envelope()
        summary = compute_audio_summary(
            events=[e.model_dump(mode="json") for e in env.events],
            config_snapshot={
                "endpointing_mode": ai_config.engine_v2_endpointing_mode,
                "endpointing_min_delay": ai_config.engine_v2_endpointing_min_delay,
                "endpointing_max_delay": ai_config.engine_v2_endpointing_max_delay,
                "turn_detector_unlikely_threshold":
                    ai_config.engine_v2_turn_detector_unlikely_threshold,
            },
        )
        # CMI-3: the talk-test reads these numbers (incl. the `perceived` block) from the logs.
        log.info("engine.v2.audio_tuning_summary", **summary)


@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint. Parses dispatch metadata, binds the log context,
    then runs the engine; any pre-run crash is funneled to the failure handler."""
    metadata = json.loads(ctx.job.metadata or "{}")
    session_id_str = metadata["session_id"]
    tenant_id_str = metadata["tenant_id"]
    correlation_id = metadata.get("correlation_id", session_id_str)
    session_uuid = uuid.UUID(session_id_str)
    tenant_uuid = uuid.UUID(tenant_id_str)

    structlog.contextvars.bind_contextvars(
        session_id=session_id_str,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

    try:
        await _run_entrypoint(ctx, session_uuid, tenant_uuid, correlation_id)
    except Exception as exc:
        await _handle_entrypoint_failure(
            exc=exc,
            ctx=ctx,
            session_id=session_uuid,
            tenant_uuid=tenant_uuid,
            correlation_id=correlation_id,
        )
        raise  # preserves LiveKit's "job crashed" log


async def _run_entrypoint(
    ctx: JobContext,
    session_uuid: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Fetch the SessionConfig and run the engine (unconditional — single engine)."""
    async with get_bypass_session() as db:
        session_config = await build_session_config(
            db, session_id=session_uuid, tenant_id=tenant_uuid,
        )
    log.info(
        "engine.config.fetched",
        question_count=len(session_config.stage.questions),
        stage_type=session_config.stage.stage_type,
        candidate_name=session_config.candidate.name,
        job_title=session_config.job_title,
    )
    await run(
        ctx,
        session_config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
    )


async def _handle_entrypoint_failure(
    *,
    exc: Exception,
    ctx: JobContext,
    session_id: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Single failure handler for every pre-`run` crash path.

    Order: classify -> transition session row to state='error' (durable truth) ->
    best-effort publish session_outcome='error' to the room. DB transition is first
    so the candidate's HTTP fallback poll wins even if the room publish fails. Each
    step is independently guarded and neither re-raises (the caller re-raises the
    original exception)."""
    error_code = classify_engine_exception(exc)
    log.error(
        "engine.entrypoint.failed",
        error_code=error_code,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    try:
        async with get_bypass_session() as db:
            await transition_to_error(
                db,
                session_id=session_id,
                tenant_id=tenant_uuid,
                error_code=error_code,
                correlation_id=correlation_id,
                reason="engine_entrypoint",
            )
            await db.commit()
    except Exception as inner:  # noqa: BLE001
        log.error(
            "engine.entrypoint.db_transition_failed",
            error=str(inner),
            error_type=type(inner).__name__,
        )
    await _best_effort_publish_outcome_attribute(ctx)


async def _best_effort_publish_outcome_attribute(ctx: JobContext) -> None:
    """Publish session_outcome='error' to the room if possible (connecting first,
    since the failure may predate ctx.connect()). Swallows every exception."""
    try:
        if not ctx.room.isconnected():
            await ctx.connect()
        await ctx.room.local_participant.set_attributes(
            {"session_outcome": "error"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "engine.entrypoint.outcome_publish_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
