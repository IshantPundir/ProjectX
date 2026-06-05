"""Gen-3 Ear — orchestration layer under LiveKit manual turn control (B4).

The Ear sits between LiveKit's raw events (VAD state changes, audio frames,
transcript chunks) and the fusion ladder (``ear/ladder.py``). Its job is to:

  1. Maintain live pause state (``SpeechActivity``) and buffered audio
     (``TurnAudioBuffer``) across the candidate's turn.
  2. On each evaluation tick (driven by the B4 poll loop in ``agent.py``),
     fuse the three signals — VAD silence, Smart Turn probability, and text-
     EOU probability — into a single ``EarDecision`` via ``ladder.decide()``.
  3. Act on the decision:
       commit   → ``session.commit_user_turn()`` then clear the buffer.
       hold_cue → ``session.say(cue_text)`` ONCE per pause (idempotent).
       wait     → no-op.
  4. Log every evaluation for observability (gen-2's cue was invisible).

Design rules
------------
* ``Ear`` (this module) is **livekit-free** — LiveKit types are only referenced
  under ``TYPE_CHECKING`` (duck-typed in ``act``). This keeps the class fully
  unit-testable with a mock session.
* The LiveKit glue (``_EarAgent``, ``setup_ear``, ``build_ear``) lives in
  ``agent.py`` — the one file that IS allowed to import livekit.
* No global state. The ``Ear`` instance is per-session.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from app.modules.interview_engine.ear.ladder import (
    EarDecision,
    EarLadderConfig,
    decide,
)
from app.modules.interview_engine.ear.vad_gate import (
    SpeechActivity,
    text_eou_probability,
)

if TYPE_CHECKING:
    # LiveKit types never imported at runtime from this module.
    # The ``session`` parameter in ``act`` is duck-typed.
    from livekit.agents import AgentSession as _AgentSession  # noqa: F401

    from app.modules.interview_engine.ear.smart_turn import TurnAudioBuffer

import numpy as np

# ---------------------------------------------------------------------------
# Module-level default patience cue
# ---------------------------------------------------------------------------

#: Gentle patience cue played at most once per candidate pause.
#: Indian-English natural phrasing — tuned empirically in Phase F3.
DEFAULT_HOLD_CUE: str = "Mm, take your time, ya."


# ---------------------------------------------------------------------------
# Ear — the orchestration core (livekit-free)
# ---------------------------------------------------------------------------


class Ear:
    """Per-session turn-taking orchestrator.

    Wires ``SpeechActivity`` (pause clock) + ``TurnAudioBuffer`` (audio ring-
    buffer + Smart Turn inference) + optional text-EOU model into the fusion
    ladder, then translates the ``EarDecision`` into a session action.

    Parameters
    ----------
    cfg:
        Fusion-ladder thresholds. Use ``ladder_config_from_ai_config()`` in
        production; pass a fixed ``EarLadderConfig`` in tests.
    buffer:
        Audio ring-buffer for the current turn.  Inject a ``TurnAudioBuffer``
        with a fake detector for unit tests.
    activity:
        Pure pause detector driven by LiveKit ``user_state_changed`` events.
    eou_model:
        Optional duck-typed EOU model with ``predict_end_of_turn``.
        Pass ``None`` for Smart-Turn-only mode (supported path).
    logger:
        structlog logger.  Defaults to the module logger.
    """

    def __init__(
        self,
        *,
        cfg: EarLadderConfig,
        buffer: "TurnAudioBuffer",
        activity: SpeechActivity,
        eou_model: object | None = None,
        logger: Any = None,
    ) -> None:
        self._cfg = cfg
        self._buffer = buffer
        self._activity = activity
        self._eou_model = eou_model
        self._log = logger or structlog.get_logger("interview_engine.ear")

        # Tracks whether a hold-cue has already been played in the current
        # pause so we never double-fire the patience cue within one pause.
        self._cue_played: bool = False

    # ------------------------------------------------------------------
    # Event callbacks — wired by the agent.py glue
    # ------------------------------------------------------------------

    def on_user_state(self, new_state: str, now_ms: int) -> None:
        """Handle a LiveKit ``user_state_changed`` event.

        Maps LiveKit states to the ``SpeechActivity`` API and manages the
        audio buffer + cue-guard lifecycle:

        ``"speaking"``
            Candidate started speaking.  Reset the buffer so ``predict()``
            only ever sees the current turn's audio, and clear the cue guard
            so a new patience cue can fire on the next pause.

        ``"listening"``
            Candidate stopped (VAD detected a pause).  Record the stop time
            so ``silence_ms`` starts counting from this moment.

        ``"away"``
            Candidate went away (LiveKit user-away detection).  Record in the
            log as a hook for the Phase F3 unresponsive-candidate ladder.
            No buffer or activity change — the silence clock keeps running.
        """
        if new_state == "speaking":
            self._activity.on_speaking_started(now_ms)
            self._buffer.reset()
            self._cue_played = False
            self._log.debug(
                "engine.ear.speaking_started",
                now_ms=now_ms,
            )
        elif new_state == "listening":
            self._activity.on_speaking_stopped(now_ms)
            self._log.debug(
                "engine.ear.speaking_stopped",
                now_ms=now_ms,
            )
        elif new_state == "away":
            # F3-VALIDATE: the unresponsive-candidate ladder is Phase F3.
            # For now we log the event and leave buffer/activity unchanged
            # (silence clock keeps running, giving the away-recovery path
            # access to the accumulated silence duration).
            self._log.info(
                "engine.ear.user_away",
                now_ms=now_ms,
            )
        else:
            self._log.warning(
                "engine.ear.unknown_user_state",
                new_state=new_state,
                now_ms=now_ms,
            )

    def append_audio(self, frame: np.ndarray) -> None:
        """Append a float32 mono audio frame to the turn buffer.

        Called by the ``_EarAgent.stt_node`` tee in ``agent.py`` on every
        incoming LiveKit audio frame during candidate speech.

        Parameters
        ----------
        frame:
            1-D float32 mono audio at the buffer's ``sample_rate``.
            Conversion from LiveKit's int16 is the caller's responsibility
            (``agent.py``'s ``stt_node`` handles it).
        """
        self._buffer.append(frame)

    # ------------------------------------------------------------------
    # Evaluate — fuse signals, log, return decision
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        *,
        now_ms: int,
        chat_ctx: object | None = None,
    ) -> tuple[EarDecision, dict]:
        """Fuse VAD + Smart Turn + text EOU into a single ``EarDecision``.

        This is the core tick called by the poll loop in ``agent.py`` while
        the candidate is paused. It:

        1. Reads ``vad_silence_ms`` from ``SpeechActivity``.
        2. Calls ``TurnAudioBuffer.predict()`` for the Smart Turn probability.
        3. Optionally calls ``text_eou_probability`` on the EOU model.
        4. Delegates to ``ladder.decide()`` for the fused decision.
        5. Logs the decision + all three signals for observability.
        6. Returns ``(decision, telemetry_dict)`` — the dict is for the caller
           to emit as metrics or include in the session audit trail.

        Parameters
        ----------
        now_ms:
            Current monotonic session clock in milliseconds.
        chat_ctx:
            Optional pre-built ``livekit.agents.llm.ChatContext`` for the
            text-EOU model. Ignored when ``eou_model`` is ``None``.

        Returns
        -------
        ``(EarDecision, telemetry_dict)``
        """
        vad_silence_ms = self._activity.silence_ms(now_ms)

        smart_turn_result = self._buffer.predict()
        smart_turn_prob: float = smart_turn_result["probability"]

        # text_eou_probability returns None on any failure (timeout, model
        # unavailable, etc.) — the ladder handles the None case explicitly.
        text_eou_prob: float | None = None
        if self._eou_model is not None and chat_ctx is not None:
            text_eou_prob = await text_eou_probability(
                self._eou_model, chat_ctx  # type: ignore[arg-type]
            )

        decision = decide(
            vad_silence_ms=vad_silence_ms,
            smart_turn_prob=smart_turn_prob,
            text_eou_prob=text_eou_prob,
            cfg=self._cfg,
        )

        # Observability: structured log for every evaluation tick.
        # gen-2's cue was invisible — this fixes it.
        self._log.info(
            "engine.ear.eou",
            decision=str(decision),
            smart_turn_prob=round(smart_turn_prob, 4),
            text_eou_prob=(
                round(text_eou_prob, 4) if text_eou_prob is not None else None
            ),
            vad_silence_ms=vad_silence_ms,
        )

        telemetry = {
            "decision": str(decision),
            "smart_turn_prob": smart_turn_prob,
            "text_eou_prob": text_eou_prob,
            "vad_silence_ms": vad_silence_ms,
        }
        return decision, telemetry

    # ------------------------------------------------------------------
    # Act — translate decision into session action
    # ------------------------------------------------------------------

    async def act(
        self,
        session: Any,
        decision: EarDecision,
        *,
        cue_text: str | None = None,
    ) -> None:
        """Translate an ``EarDecision`` into a session-level action.

        Parameters
        ----------
        session:
            Duck-typed LiveKit ``AgentSession`` (or mock).  Only three
            methods are called: ``commit_user_turn()``, ``say(text)`` (async),
            and ``interrupt()`` (called by the glue, not here).
        decision:
            The decision returned by ``evaluate()``.
        cue_text:
            Optional override for the patience cue.  Defaults to
            ``DEFAULT_HOLD_CUE`` ("Mm, take your time, ya.").
        """
        if decision == EarDecision.commit:
            self._log.info("engine.ear.act.commit")
            session.commit_user_turn()
            self._buffer.reset()

        elif decision == EarDecision.hold_cue:
            if self._cue_played:
                # Already played the patience cue in this pause — stay silent.
                self._log.debug("engine.ear.act.hold_cue.suppressed")
                return
            cue = cue_text or DEFAULT_HOLD_CUE
            self._log.info("engine.ear.act.hold_cue", cue=cue)
            await session.say(cue)
            self._cue_played = True

        elif decision == EarDecision.wait:
            # No-op — keep listening.
            self._log.debug("engine.ear.act.wait")
