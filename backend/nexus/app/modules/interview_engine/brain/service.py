"""
Brain service — D3 task.

ControlPlane.decide(turn_input, *, asked_ids) → BrainDecision

Responsibilities:
  1. Build the full LLM messages list (prefix + suffix) via build_messages.
  2. Call the brain LLM (instructor, structured output → BrainTurnOutput).
     The LLM call is an INJECTABLE SEAM (llm_call arg) so tests can pass a
     fake; the default (_default_brain_llm) is the real instructor call.
  3. Update the CoverageProjection with the LLM's observations.
  4. Derive a Directive from the BrainTurnOutput:
       - Map BrainMove → DirectiveAct (1:1 by name).
       - Resolve `say`:
           ask       → resolver picks next question; fallback to close if None.
           probe     → coerce_probe_dimension (fire-once + cap); composed_say (scrubbed)
                       or the served dimension's seed_probe; fallback to ask when None.
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
    coerce_probe_dimension,
    scrub_composed_say,
)
from app.modules.interview_engine.brain.resolver import (
    ResolverQuestion,
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
)

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
    DirectiveAct.hold: DirectiveTone.warm,
    DirectiveAct.confirm: DirectiveTone.warm,
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
        llm_call: Callable[[list[dict]], Awaitable[BrainTurnOutput]] | None = None,
    ) -> None:
        self._session_context = session_context
        self._system_prompt = system_prompt
        self._projection = projection
        self._resolver_questions = resolver_questions
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
        )

        # When this turn is a probe, record which dimension was served (coerced to a
        # valid, UNFIRED slug under the cap) so the driver's fired-dimension ledger
        # advances and the same dimension is never re-fired.
        probe_dimension_used: str | None = None
        if directive.act == DirectiveAct.probe:
            from app.ai.config import ai_config
            probe_dimension_used = coerce_probe_dimension(
                output.probe_dimension,
                follow_ups=turn_input.active_question.follow_ups,
                fired=turn_input.active_question.fired_dimensions,
                cap=ai_config.engine_probe_cap_per_thread,
            )

        # Per-turn decision trace (F3 tuning observability). reasoning is the
        # brain's own scratchpad (not candidate PII); kept short.
        _log.info(
            "engine.brain.decision",
            llm_move=output.move.value,
            act=directive.act.value,
            is_terminal=directive.is_terminal,
            probe_dimension=output.probe_dimension,
            next_question_id=next_question_id,
            probe_composed=bool(directive.act == DirectiveAct.probe and output.composed_say),
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
            probe_dimension=probe_dimension_used,
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
    ) -> tuple[Directive, str | None]:
        """Map BrainTurnOutput → (Directive, next_question_id) applying all policy gates.

        Returns:
            A tuple of (Directive, next_question_id). `next_question_id` is the
            resolver-selected question id when act==ask; None for all other acts and
            for a close produced when the resolver found no remaining question.
        """

        # ── Candidate-initiated end — always honored ─────────────────────────
        # A candidate may end the screen at ANY time.
        if output.move == BrainMove.close and output.end_requested:
            return Directive(
                act=DirectiveAct.close,
                say=None,
                tone=DirectiveTone.warm,
                spoken_setup=None,
                is_terminal=True,
            ), None

        # ── Move → Act + Say ─────────────────────────────────────────────────
        move = output.move
        return self._resolve_move(
            move=move,
            output=output,
            turn_input=turn_input,
            asked_ids=asked_ids,
        )

    def _resolve_move(
        self,
        *,
        move: BrainMove,
        output: BrainTurnOutput,
        turn_input: BrainTurnInput,
        asked_ids: set[str],
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
                )

            case BrainMove.probe:
                return self._resolve_probe(
                    output=output,
                    turn_input=turn_input,
                    asked_ids=asked_ids,
                )

            case (
                BrainMove.clarify
                | BrainMove.redirect
                | BrainMove.reassure
                | BrainMove.hold
                | BrainMove.confirm
                | BrainMove.answer_meta
            ):
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
    ) -> tuple[Directive, str | None]:
        """Resolve an `ask` move: deterministic resolver → (bank-text Directive, next_question_id).

        Returns (close Directive, None) when the resolver finds no remaining question.
        Returns (ask Directive, nxt.question_id) otherwise.
        """
        nxt = resolve_next(
            questions=self._resolver_questions,
            asked_ids=asked_ids,
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
    ) -> tuple[Directive, str | None]:
        """Resolve a `probe` move. The dimension gate decides probe-vs-advance.

        coerce_probe_dimension returns the served (valid, unfired) slug under the cap,
        or None → fall back to `ask` (advance). The spoken text is the brain's composed
        probe (leak-scrubbed); when not composed, the served dimension's seed_probe.
        """
        from app.ai.config import ai_config

        served = coerce_probe_dimension(
            output.probe_dimension,
            follow_ups=turn_input.active_question.follow_ups,
            fired=turn_input.active_question.fired_dimensions,
            cap=ai_config.engine_probe_cap_per_thread,
        )
        if served is None:
            # Cap reached or all dimensions fired → advance.
            return self._resolve_ask(
                output=output, asked_ids=asked_ids,
            )

        composed = scrub_composed_say(output.composed_say, turn_input.active_question)
        if composed:
            say = composed
        else:
            # Seed fallback: the served dimension's pre-authored probe.
            by_slug = {d.dimension: d.seed_probe for d in turn_input.active_question.follow_ups}
            say = by_slug.get(served, "")

        return Directive(
            act=DirectiveAct.probe,
            say=say,
            tone=_ACT_TONE[DirectiveAct.probe],
            spoken_setup=None,
            is_terminal=False,
        ), None

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
    (version from ai_config.engine_brain_prompt_version). Builds session context
    and resolver questions.

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

    # Build resolver questions from the bank (purely positional selection).
    resolver_questions: list[ResolverQuestion] = []
    for q in config.stage.questions:
        primary = q.primary_signal or (q.signal_values[0] if q.signal_values else "")
        resolver_questions.append(
            ResolverQuestion(
                question_id=q.id,
                primary_signal=primary,
                position=q.position,
            )
        )

    return ControlPlane(
        session_context=session_context,
        system_prompt=system_prompt,
        projection=projection or CoverageProjection(),
        resolver_questions=resolver_questions,
        llm_call=None,  # use _default_brain_llm in production
    )
