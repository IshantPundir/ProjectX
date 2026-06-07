"""Per-turn drive loop — bridge ∥ brain → mouth real line + NoteLog.

Spec reference: §3 (architecture overview), §6 (per-turn loop).

This module is the heart of the Gen-3 engine's turn-by-turn execution. It is
intentionally FREE of LiveKit imports — all collaborators are duck-typed via
`typing.Protocol` so the loop can be unit-tested without any realtime transport.
The real wiring (Ear commit → run_turn, barge-in → cancel the run_turn task)
lands in Phase F1 (agent.py / controller integration).

Turn anatomy (one `run_turn` call):
  1. Fire mouth.bridge ∥ brain.decide IN PARALLEL (asyncio.create_task).
  2. Await the bridge first — it resolves in ~100–300ms and masks the brain's
     ~2–3s inference latency. Play it via voice.say immediately.
     If bridge errors → play CANNED_BRIDGE_FALLBACK. Never dead air.
  3. Await brain.decide — returns a BrainDecision (Directive + observations).
  4. Append each observation to the NoteLog (append-only, monotonic seq).
  5. Render the Directive as speech via mouth.real_line, passing
     just_said=bridge_text so the mouth CONTINUES from the bridge
     (one ack/turn — no repeated opener).
  6. Play the real line via voice.say.

Cancellation (barge-in): cancel the run_turn task → the `finally` block cancels
both child tasks. Neither task is left dangling. CancelledError propagates to
the caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

# Lazy / TYPE_CHECKING imports — keep this module livekit-free at runtime.
if TYPE_CHECKING:
    pass  # no runtime livekit imports ever

from app.modules.interview_engine.contracts import (
    BrainDecision,
    BrainTurnInput,
    BridgeRequest,
    MouthTurnInput,
)
from app.modules.interview_runtime.evidence import TimeSpan


# ---------------------------------------------------------------------------
# Canned fallback — spec §F3 tunable
# ---------------------------------------------------------------------------

#: Spoken when mouth.bridge errors or times out (never dead air).
#: Indian-English, neutral, trailing-open. Phase F3 may tune this per-persona.
CANNED_BRIDGE_FALLBACK: str = "Mm, okay…"


# ---------------------------------------------------------------------------
# Collaborator Protocols (duck-typed — no livekit dependency)
# ---------------------------------------------------------------------------

class Brain(Protocol):
    """Async control plane: grades the answer, runs policy gates, emits a Directive.

    The real implementation lives in Phase D (brain service). The loop only
    calls `decide`; the brain never mutates external state directly.
    """

    async def decide(self, turn_input: BrainTurnInput) -> BrainDecision:
        """Evaluate the committed candidate turn and return a BrainDecision.

        Args:
            turn_input: Everything the brain needs for this turn (session context,
                active rubric, signal coverage map, sliding transcript window,
                candidate utterance + triage classification).

        Returns:
            A BrainDecision containing the resolved Directive, zero or more signal
            observations for the NoteLog, and the brain's audit reasoning.
        """
        ...


class Mouth(Protocol):
    """Spoken-word renderer: bridge filler + real-line naturalisation.

    The real implementation lives in Phase E (mouth service). The mouth NEVER
    sees the rubric or the brain's reasoning (no-leak invariant by construction —
    it only receives a Directive and what was just said).
    """

    async def bridge(self, req: BridgeRequest) -> str:
        """Emit an immediate, short spoken beat while the brain is running.

        The bridge is always a filler/continuation cue — never a question,
        never rubric-bearing content.

        Args:
            req: The triage tier's bridge request (cue + triage_intent).

        Returns:
            The verbatim text to speak (short, Indian-English filler).
        """
        ...

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        """Render the brain's Directive as natural spoken Indian English.

        The mouth CONTINUES from the bridge (just_said is set). It should not
        repeat the bridge ack — one ack per turn.

        Args:
            mouth_input: The Directive + just_said bridge text + recent openers.

        Returns:
            The full spoken line (bridge continuation → directive rendering).
        """
        ...


class Voice(Protocol):
    """Duck-typed session — emits synthesised speech to the candidate.

    LiveKit's AgentSession.say(text) satisfies this protocol. The loop only
    needs `say`; nothing else from AgentSession is called here.
    """

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        """Speak `text` to the candidate (TTS + WebRTC delivery).

        Args:
            text: The verbatim text to synthesise and deliver.
            allow_interruptions: When False, the candidate cannot talk over this
                utterance (used for the non-interruptible opening intro).
        """
        ...


# ---------------------------------------------------------------------------
# TurnContext — carries everything the loop needs for one turn
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnContext:
    """Immutable context for one committed candidate turn.

    Assembled by the engine controller (Phase F) before calling run_turn.
    Carries everything the loop needs to build the brain/bridge inputs, record
    notes, and thread the recent_openers list through to the mouth.

    Fields:
        turn_ref:           Engine turn reference (e.g. "t-3"). Used as the NoteLog key.
        utterance:          Full committed candidate utterance text (proof for notes).
        utterance_span:     TimeSpan of the full utterance (start/end ms).
        from_question_id:   Bank question on the floor when this was said.
        via_probe:          True if the utterance was elicited by a follow-up probe.
        brain_input:        Pre-assembled BrainTurnInput (controller builds this).
        bridge_request:     Pre-assembled BridgeRequest (triage tier emits this).
        recent_openers:     Opening words from recent agent turns — forwarded to the mouth
                            to avoid repetitive sentence-starters.
    """
    turn_ref: str
    utterance: str
    utterance_span: TimeSpan
    from_question_id: str
    via_probe: bool
    brain_input: BrainTurnInput
    bridge_request: BridgeRequest
    recent_openers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

async def run_turn(
    ctx: TurnContext,
    *,
    brain: Brain,
    mouth: Mouth,
    voice: Voice,
    notelog: object,  # NoteLog — typed as object to avoid a hard import cycle in tests
) -> BrainDecision:
    """Run one candidate turn: bridge ∥ brain → real line; append notes.

    Spec §6 implementation. Cancellation-safe — see module docstring.

    Args:
        ctx:      Immutable turn context (utterance, spans, question id, inputs).
        brain:    Async control plane (duck-typed Brain protocol).
        mouth:    Spoken-word renderer (duck-typed Mouth protocol).
        voice:    TTS delivery surface (duck-typed Voice protocol).
        notelog:  The session's append-only NoteLog. `append()` is called once
                  per observation in BrainDecision.observations.

    Returns:
        The BrainDecision returned by the brain, after notes have been appended
        and the real line has been spoken. The caller (engine controller) uses
        `decision.is_terminal` to trigger session cleanup when appropriate.

    Raises:
        asyncio.CancelledError: when the task is cancelled (barge-in). Both child
            tasks (bridge, brain) are cancelled in the finally block before the
            error propagates.
    """
    # §6.1 — Launch bridge and brain IN PARALLEL (neither blocks the other).
    bridge_task: asyncio.Task[str] = asyncio.create_task(
        mouth.bridge(ctx.bridge_request),
        name=f"bridge:{ctx.turn_ref}",
    )
    brain_task: asyncio.Task[BrainDecision] = asyncio.create_task(
        brain.decide(ctx.brain_input),
        name=f"brain:{ctx.turn_ref}",
    )

    try:
        # §6.2 — Play the bridge the instant it resolves (masks brain latency).
        # On any error (TTS upstream, network, timeout) → canned fallback.
        # Never dead air: even if bridge_task raises, we continue.
        try:
            bridge_text: str = await bridge_task
        except Exception:
            bridge_text = CANNED_BRIDGE_FALLBACK

        await voice.say(bridge_text)

        # §6.3 — Await the brain's decision (already running in parallel).
        decision: BrainDecision = await brain_task

        # §6.4 — Append signal observations to the append-only NoteLog.
        # Each observation becomes one immutable EvidenceNote with monotonic seq.
        for obs in decision.observations:
            notelog.append(  # type: ignore[union-attr]
                obs,
                turn_ref=ctx.turn_ref,
                utterance=ctx.utterance,
                utterance_span=ctx.utterance_span,
                from_question_id=ctx.from_question_id,
                via_probe=ctx.via_probe,
            )

        # §6.5 — Render the real line, CONTINUING from the bridge.
        # just_said=bridge_text ensures the mouth does not repeat the bridge ack.
        # One ack per turn — the real line picks up the thread.
        mouth_input = MouthTurnInput(
            directive=decision.directive,
            just_said=bridge_text,
            recent_openers=ctx.recent_openers,
        )
        real_text: str = await mouth.real_line(mouth_input)
        await voice.say(real_text)

        return decision

    finally:
        # §6.6 — Barge-in / error: cancel any in-flight child tasks.
        # Called on both normal completion (tasks already done → cancel is a no-op)
        # and on CancelledError / exception (tasks may still be running → must cancel).
        # This guarantees no task is left dangling after run_turn exits.
        for task in (bridge_task, brain_task):
            if not task.done():
                task.cancel()
