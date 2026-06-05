"""
Brain service — D3 task.

ControlPlane.decide(turn_input, *, asked_ids, time_remaining_s) → BrainDecision

Responsibilities:
  1. Build the full LLM messages list (prefix + suffix) via build_messages.
  2. Call the brain LLM (instructor, structured output → BrainTurnOutput).
     The LLM call is an INJECTABLE SEAM (llm_call arg) so tests can pass a
     fake; the default (_default_brain_llm) is the real instructor call.
  3. Update the CoverageProjection with the LLM's observations.
  4. Derive a Directive from the BrainTurnOutput:
       - Apply gate_knockout (block premature close, steer knockout flow).
       - Map BrainMove → DirectiveAct (1:1 by name).
       - Resolve `say`:
           ask       → resolver picks next question; fallback to close if None.
           probe     → coerce probe_index, verbatim follow_up; fallback to ask.
           clarify / redirect / reassure / answer_meta → scrub_composed_say.
           repeat    → on_the_floor verbatim.
           close     → None (mouth composes from act prompt), is_terminal=True.
  5. Return BrainDecision(directive, observations, reasoning, is_terminal).

Module-level constructor:
  build_control_plane(config, *, projection=None) → ControlPlane
    Assembles everything from a SessionConfig (used by loop F1).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

from app.modules.interview_engine.brain.input_builder import (
    CoverageProjection,
    build_messages,
    build_session_context,
)
from app.modules.interview_engine.brain.policy import (
    KnockoutTracker,
    coerce_probe_index,
    gate_knockout,
    scrub_composed_say,
)
from app.modules.interview_engine.brain.resolver import (
    BudgetConfig,
    ResolverQuestion,
    budget_config_from_ai_config,
    resolve_next,
)
from app.modules.interview_engine.contracts import (
    BrainDecision,
    BrainMove,
    BrainSessionContext,
    BrainTurnInput,
    BrainTurnOutput,
    Directive,
    DirectiveAct,
    DirectiveTone,
    SignalSpec,
)
from app.modules.interview_runtime.evidence import CoverageState

if TYPE_CHECKING:
    from app.modules.interview_runtime.schemas import SessionConfig

_log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tone defaults per act (simple policy — tune if needed)
# ---------------------------------------------------------------------------

_ACT_TONE: dict[DirectiveAct, DirectiveTone] = {
    DirectiveAct.ask: DirectiveTone.warm,
    DirectiveAct.probe: DirectiveTone.warm,
    DirectiveAct.clarify: DirectiveTone.warm,
    DirectiveAct.redirect: DirectiveTone.calm,
    DirectiveAct.reassure: DirectiveTone.warm,
    DirectiveAct.answer_meta: DirectiveTone.neutral,
    DirectiveAct.repeat: DirectiveTone.warm,
    DirectiveAct.close: DirectiveTone.warm,
}


# ---------------------------------------------------------------------------
# ControlPlane
# ---------------------------------------------------------------------------

class ControlPlane:
    """Async control plane — one structured LLM call per committed candidate turn.

    Parameters
    ----------
    session_context:
        Stable, byte-identical session prefix (built once at session start).
    system_prompt:
        The brain system prompt (read from prompts/v4/engine/brain.system.txt).
    projection:
        Mutable CoverageProjection that accumulates signal observations.
    resolver_questions:
        Compact bank view (ResolverQuestion list) — used to resolve next question.
    all_specs:
        Full SignalSpec list — used by the knockout gate + projection helpers.
    budget_cfg:
        Time-budget config for resolve_next.
    knockout_tracker:
        Per-session KnockoutTracker; defaults to a fresh one if None.
    llm_call:
        INJECTABLE SEAM for tests.  None → use _default_brain_llm (real API call).
    """

    def __init__(
        self,
        *,
        session_context: BrainSessionContext,
        system_prompt: str,
        projection: CoverageProjection,
        resolver_questions: list[ResolverQuestion],
        all_specs: list[SignalSpec],
        budget_cfg: BudgetConfig,
        knockout_tracker: KnockoutTracker | None = None,
        llm_call: Callable[[list[dict]], Awaitable[BrainTurnOutput]] | None = None,
    ) -> None:
        self._session_context = session_context
        self._system_prompt = system_prompt
        self._projection = projection
        self._resolver_questions = resolver_questions
        self._all_specs = all_specs
        self._budget_cfg = budget_cfg
        self._knockout_tracker = knockout_tracker or KnockoutTracker()
        self._llm_call: Callable[[list[dict]], Awaitable[BrainTurnOutput]] = (
            llm_call if llm_call is not None else self._default_brain_llm
        )

        # Build a lookup: question_id → bank question text (for `ask` resolution)
        self._bank_text_by_id: dict[str, str] = {
            q.question_id: q.text
            for q in session_context.bank_index
        }

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def decide(
        self,
        turn_input: BrainTurnInput,
        *,
        asked_ids: set[str],
        time_remaining_s: float,
    ) -> BrainDecision:
        """One full brain turn: LLM call → projection update → Directive derivation."""

        # Step 1: build messages (stable prefix + dynamic suffix)
        messages = build_messages(self._system_prompt, self._session_context, turn_input)

        # Step 2: LLM call (real or injected fake)
        output: BrainTurnOutput = await self._llm_call(messages)

        # Step 3: update the coverage projection
        quote_by_signal = {
            obs.signal: turn_input.candidate_utterance
            for obs in output.observations
        }
        self._projection.update(
            output.observations,
            established_quote_by_signal=quote_by_signal,
        )

        # Step 4: derive the Directive + the resolver-selected next question id (if ask)
        directive, next_question_id = self._derive_directive(
            output=output,
            turn_input=turn_input,
            asked_ids=asked_ids,
            time_remaining_s=time_remaining_s,
        )

        # Per-turn decision trace (F3 tuning observability). reasoning is the
        # brain's own scratchpad (not candidate PII); kept short.
        _log.info(
            "engine.brain.decision",
            llm_move=output.move.value,
            act=directive.act.value,
            is_terminal=directive.is_terminal,
            probe_index=output.probe_index,
            next_question_id=next_question_id,
            n_observations=len(output.observations),
            reasoning=(output.reasoning or "")[:160],
        )

        # Step 5: return BrainDecision
        return BrainDecision(
            directive=directive,
            observations=output.observations,
            reasoning=output.reasoning,
            is_terminal=directive.is_terminal,
            next_question_id=next_question_id,
        )

    # -----------------------------------------------------------------------
    # Directive derivation (private)
    # -----------------------------------------------------------------------

    def _derive_directive(
        self,
        *,
        output: BrainTurnOutput,
        turn_input: BrainTurnInput,
        asked_ids: set[str],
        time_remaining_s: float,
    ) -> tuple[Directive, str | None]:
        """Map BrainTurnOutput → (Directive, next_question_id) applying all policy gates.

        Returns:
            A tuple of (Directive, next_question_id). `next_question_id` is the
            resolver-selected question id when act==ask; None for all other acts and
            for a close produced when the resolver found no remaining question.
        """

        # Covered signals (for resolver)
        covered_signals: set[str] = {
            sr.signal
            for sr in self._projection.signal_reads()
            if sr.coverage == CoverageState.sufficient
        }

        # ── Gate: verified-knockout ──────────────────────────────────────────
        gate = gate_knockout(
            proposed_move=output.move,
            knockout_pending=turn_input.knockout_pending,
            tracker=self._knockout_tracker,
        )

        if not gate.allow_move:
            # Premature close blocked — steer toward the knockout verification flow.
            # Produce a warm probe/clarify directive to drive gate.forced_step.
            return self._steer_knockout(gate, turn_input), None

        # ── Move → Act + Say ─────────────────────────────────────────────────
        move = output.move
        return self._resolve_move(
            move=move,
            output=output,
            turn_input=turn_input,
            asked_ids=asked_ids,
            covered_signals=covered_signals,
            time_remaining_s=time_remaining_s,
        )

    def _resolve_move(
        self,
        *,
        move: BrainMove,
        output: BrainTurnOutput,
        turn_input: BrainTurnInput,
        asked_ids: set[str],
        covered_signals: set[str],
        time_remaining_s: float,
    ) -> tuple[Directive, str | None]:
        """Resolve a BrainMove (gate-allowed) → (Directive, next_question_id).

        `next_question_id` is the resolver-selected question id for ask moves; None otherwise.
        """

        act = DirectiveAct(move.value)  # 1:1 by name

        match move:
            case BrainMove.ask:
                return self._resolve_ask(
                    output=output,
                    asked_ids=asked_ids,
                    covered_signals=covered_signals,
                    time_remaining_s=time_remaining_s,
                )

            case BrainMove.probe:
                return self._resolve_probe(
                    output=output,
                    turn_input=turn_input,
                    asked_ids=asked_ids,
                    covered_signals=covered_signals,
                    time_remaining_s=time_remaining_s,
                )

            case BrainMove.clarify | BrainMove.redirect | BrainMove.reassure | BrainMove.answer_meta:
                say = scrub_composed_say(output.composed_say, turn_input.active_question)
                return Directive(
                    act=act,
                    say=say,
                    tone=_ACT_TONE[act],
                    spoken_setup=None,
                    is_terminal=False,
                ), None

            case BrainMove.repeat:
                return Directive(
                    act=DirectiveAct.repeat,
                    say=turn_input.on_the_floor,
                    tone=_ACT_TONE[DirectiveAct.repeat],
                    spoken_setup=None,
                    is_terminal=False,
                ), None

            case BrainMove.close:
                return Directive(
                    act=DirectiveAct.close,
                    say=None,
                    tone=DirectiveTone.warm,
                    spoken_setup=None,
                    is_terminal=True,
                ), None

            case _:
                # Defensive fallback — unknown move → treat as repeat
                _log.warning("brain.service.unknown_move", move=move)
                return Directive(
                    act=DirectiveAct.repeat,
                    say=turn_input.on_the_floor,
                    tone=DirectiveTone.warm,
                    spoken_setup=None,
                    is_terminal=False,
                ), None

    def _resolve_ask(
        self,
        *,
        output: BrainTurnOutput,
        asked_ids: set[str],
        covered_signals: set[str],
        time_remaining_s: float,
    ) -> tuple[Directive, str | None]:
        """Resolve an `ask` move: deterministic resolver → (bank-text Directive, next_question_id).

        Returns (close Directive, None) when the resolver finds no remaining question.
        Returns (ask Directive, nxt.question_id) otherwise.
        """
        nxt = resolve_next(
            questions=self._resolver_questions,
            asked_ids=asked_ids,
            covered_signals=covered_signals,
            time_remaining_s=time_remaining_s,
            cfg=self._budget_cfg,
            preferred_next_signal=output.preferred_next_signal,
        )
        if nxt is None:
            # No question left → this is actually a close; next_question_id is None.
            return Directive(
                act=DirectiveAct.close,
                say=None,
                tone=DirectiveTone.warm,
                spoken_setup=None,
                is_terminal=True,
            ), None

        say = self._bank_text_by_id.get(nxt.question_id)
        if say is None:
            _log.warning(
                "brain.service.ask.bank_text_missing",
                question_id=nxt.question_id,
            )
            say = ""

        return Directive(
            act=DirectiveAct.ask,
            say=say,
            tone=_ACT_TONE[DirectiveAct.ask],
            spoken_setup=None,
            is_terminal=False,
        ), nxt.question_id

    def _resolve_probe(
        self,
        *,
        output: BrainTurnOutput,
        turn_input: BrainTurnInput,
        asked_ids: set[str],
        covered_signals: set[str],
        time_remaining_s: float,
    ) -> tuple[Directive, str | None]:
        """Resolve a `probe` move: coerce index → (verbatim follow_up Directive, None).

        Falls back to _resolve_ask (returning its tuple) when all probes are exhausted.
        """
        idx = coerce_probe_index(
            output.probe_index,
            follow_ups=turn_input.active_question.follow_ups,
            probes_used=turn_input.active_question.probes_used,
        )
        if idx is None:
            # No probe left → fall back to ask (which returns a tuple)
            return self._resolve_ask(
                output=output,
                asked_ids=asked_ids,
                covered_signals=covered_signals,
                time_remaining_s=time_remaining_s,
            )

        say = turn_input.active_question.follow_ups[idx]
        return Directive(
            act=DirectiveAct.probe,
            say=say,
            tone=_ACT_TONE[DirectiveAct.probe],
            spoken_setup=None,
            is_terminal=False,
        ), None

    def _steer_knockout(
        self,
        gate,  # KnockoutGate
        turn_input: BrainTurnInput,
    ) -> Directive:
        """Produce a non-terminal directive to steer toward the knockout flow.

        When gate_knockout blocks a premature close, we produce a warm probe/
        clarify-style directive that drives the current forced_step for the
        pending signal. The mouth renders the composed line; the caller only
        needs is_terminal=False and act != close.
        """
        from app.modules.interview_engine.brain.policy import KnockoutStep

        step = gate.forced_step
        signal = gate.signal

        # Build a safe steering line (not a rubric leak — purely meta/process).
        if step == KnockoutStep.probe:
            say = (
                f"Before we wrap up, I'd like to understand a bit more — "
                f"have you had direct experience with {signal}?"
            )
        elif step == KnockoutStep.check_alternatives:
            say = (
                "And are there alternative approaches or tools you've used "
                "to achieve the same outcome?"
            )
        elif step == KnockoutStep.reflect_confirm:
            say = (
                "Just to confirm — based on what you've shared, "
                "could you summarise your experience in that area for me?"
            )
        else:
            # confirmed or unknown — shouldn't be reached (gate would have allowed move)
            say = "Let me circle back to one more point before we close."

        return Directive(
            act=DirectiveAct.probe,
            say=say,
            tone=DirectiveTone.warm,
            spoken_setup=None,
            is_terminal=False,
        )

    # -----------------------------------------------------------------------
    # Default real LLM call (injectable seam — only called in production)
    # -----------------------------------------------------------------------

    async def _default_brain_llm(self, messages: list[dict]) -> BrainTurnOutput:
        """Real instructor call — mirrors question_bank/refine.py::_call_llm_refine."""
        # Lazy imports keep this module free of livekit at module-level,
        # and keep the FastAPI process free of engine-only SDKs.
        from app.ai.client import get_openai_client
        from app.ai.config import ai_config

        client = get_openai_client()
        kwargs: dict = {
            "model": ai_config.engine_brain_model,
            "response_model": BrainTurnOutput,
            "messages": messages,
            "max_retries": 1,
        }
        if ai_config.engine_brain_effort:
            kwargs["reasoning_effort"] = ai_config.engine_brain_effort
        if ai_config.engine_brain_prompt_cache_key:
            kwargs["prompt_cache_key"] = ai_config.engine_brain_prompt_cache_key

        result: BrainTurnOutput = await client.chat.completions.create(**kwargs)
        return result


# ---------------------------------------------------------------------------
# Module-level constructor helper — used by the loop (F1)
# ---------------------------------------------------------------------------

def build_control_plane(
    config: "SessionConfig",
    *,
    projection: CoverageProjection | None = None,
) -> ControlPlane:
    """Assemble a ControlPlane from a SessionConfig.

    Reads the brain system prompt from prompts/v4/engine/brain.system.txt
    (version from ai_config.engine_brain_prompt_version). Builds session context,
    resolver questions, all_specs, budget_cfg, and a fresh KnockoutTracker.

    Used by the engine loop (F1) at session start. Tests should construct
    ControlPlane directly with small fixtures.
    """
    from app.ai.config import ai_config
    from app.ai.prompts import PromptLoader

    # Load the brain system prompt
    version = ai_config.engine_brain_prompt_version
    system_prompt = PromptLoader(version).get("engine/brain.system")

    # Build the stable session context
    session_context = build_session_context(config)

    # Build resolver questions from the bank
    # Weight defaults from signal_metadata; fallback to 1 when not found.
    signal_weight: dict[str, int] = {}
    for m in config.signal_metadata:
        signal_weight[m.value] = m.weight

    resolver_questions: list[ResolverQuestion] = []
    for q in config.stage.questions:
        primary = q.primary_signal or (q.signal_values[0] if q.signal_values else "")
        resolver_questions.append(
            ResolverQuestion(
                question_id=q.id,
                primary_signal=primary,
                tier="core",
                is_mandatory=q.is_mandatory,
                position=q.position,
                weight=signal_weight.get(primary, 1),
                estimated_minutes=q.estimated_minutes,
            )
        )

    # All signal specs (from session_context.signals — already built)
    all_specs: list[SignalSpec] = session_context.signals

    # Budget config from AIConfig
    budget_cfg = budget_config_from_ai_config()

    return ControlPlane(
        session_context=session_context,
        system_prompt=system_prompt,
        projection=projection or CoverageProjection(),
        resolver_questions=resolver_questions,
        all_specs=all_specs,
        budget_cfg=budget_cfg,
        knockout_tracker=KnockoutTracker(),
        llm_call=None,  # use _default_brain_llm in production
    )
