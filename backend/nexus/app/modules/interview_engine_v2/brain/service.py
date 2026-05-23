"""ControlPlane (the brain) — one coherent decision per turn boundary (no livekit).

Renders the cache-stable prefix once, then per turn: build messages → instructor structured-output
call on engine_brain_model (the brain LLM site; like question_bank/refine.py) → apply coverage_delta
to the CoverageTracker → run deterministic policy gates → map the (possibly-downgraded) move to a
no-leak Directive (ASK/PROBE resolve VERBATIM bank text by reference; composed acts use the
policy-sanitized say) → emit the Directive + a full TurnDecisionRecord. Bounded-awaited by the
caller; a timeout/error yields a deterministic safe fallback so the turn never stalls. The LLM call
is isolated in the module-level `_call_brain` helper so tests mock it at the app/ai boundary.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog

from app.ai.config import ai_config
from app.modules.interview_engine_v2.audit import TurnDecisionRecord
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove
from app.modules.interview_engine_v2.brain.input_builder import (
    build_brain_messages,
    render_stable_prefix,
)
from app.modules.interview_engine_v2.brain.policy import evaluate_policy
from app.modules.interview_engine_v2.coverage import CoverageTracker
from app.modules.interview_engine_v2.directive import Directive, DirectiveAct, DirectiveTone
from app.modules.interview_runtime import SessionConfig

log = structlog.get_logger("interview_engine_v2.brain")

_MOVE_TO_ACT: dict[BrainMove, DirectiveAct] = {
    BrainMove.probe: DirectiveAct.PROBE,
    BrainMove.advance: DirectiveAct.ACK_ADVANCE,
    BrainMove.clarify: DirectiveAct.CLARIFY,
    BrainMove.redirect: DirectiveAct.REDIRECT,
    BrainMove.hold: DirectiveAct.HOLD,
    BrainMove.reassure: DirectiveAct.REASSURE,
    BrainMove.hint: DirectiveAct.HINT,
    BrainMove.answer_meta: DirectiveAct.ANSWER_META,
    BrainMove.confirm: DirectiveAct.CONFIRM,
    BrainMove.repeat: DirectiveAct.REPEAT,
    BrainMove.knockout_close: DirectiveAct.CLOSE,
    BrainMove.close: DirectiveAct.CLOSE,
}
_TERMINAL_MOVES = frozenset({BrainMove.knockout_close, BrainMove.close})


async def _call_brain(*, messages: list[dict[str, str]], correlation_id: str) -> BrainDecision:
    """The blessed brain LLM site (instructor structured output). Mocked in unit tests."""
    from app.ai.client import get_openai_client

    client = get_openai_client()
    create_kwargs: dict[str, object] = {
        "model": ai_config.engine_brain_model,
        "response_model": BrainDecision,
        "messages": messages,
        "max_retries": 1,
    }
    if ai_config.engine_brain_effort:          # 'low' for the brain; gated per the effort contract
        create_kwargs["reasoning_effort"] = ai_config.engine_brain_effort
    # prompt_cache_key: Step 0 spike confirmed True (SDK forwards it) — include it.
    create_kwargs["prompt_cache_key"] = ai_config.engine_brain_prompt_cache_key
    return await client.chat.completions.create(**create_kwargs)


class ControlPlane:
    """Brain control plane — one coherent LLM-based decision per turn boundary.

    Ties together CoverageTracker, BrainDecision, evaluate_policy, render_stable_prefix,
    build_brain_messages, and the instructor client. The mouth receives only a no-leak
    Directive + an auditable TurnDecisionRecord; rubric reasoning stays in the record.
    """

    def __init__(self, *, config: SessionConfig, coverage: CoverageTracker) -> None:
        self._config = config
        self._coverage = coverage
        self._questions = {q.id: q for q in config.stage.questions}
        self._active_question_id: str | None = None  # set by opener(), advanced on advance-move
        from app.ai.prompts import PromptLoader  # local import keeps module import light
        loader = PromptLoader(version=ai_config.engine_brain_prompt_version)
        self._stable_prefix = render_stable_prefix(
            system_prompt=loader.get("engine/brain.system"), config=config
        )

    @property
    def active_question_id(self) -> str | None:
        """The question currently on the floor (the agent passes nothing; the brain owns this)."""
        return self._active_question_id

    def _new_id(self) -> str:
        return f"d-{uuid.uuid4().hex[:8]}"

    def opener(self) -> tuple[Directive, Directive]:
        """Deterministic INTRO + ASK(first bank question) — D4. No brain call before any answer."""
        first = min(self._config.stage.questions, key=lambda q: q.position)
        self._active_question_id = first.id
        intro = Directive(
            id=self._new_id(), turn_ref="t-0", act=DirectiveAct.INTRO, say=None,
            compose_hint="warm, brief, disclose it's an AI + recorded, set them at ease",
            tone=DirectiveTone.WARM,
        )
        ask = Directive(id=self._new_id(), turn_ref="t-0", act=DirectiveAct.ASK, say=first.text)
        return intro, ask

    async def decide(
        self,
        *,
        turn_ref: str,
        candidate_utterance: str,
        transcript_window: list[tuple[str, str]],
        active_question_id: str | None = None,
        correlation_id: str = "",
        budget_ms: int | None = None,
    ) -> tuple[Directive, TurnDecisionRecord]:
        """One bounded brain decision. Returns (Directive, TurnDecisionRecord).

        `active_question_id` defaults to the brain's own tracked pointer; pass it explicitly only
        in tests. On a successful `advance` the pointer moves to the newly-asked question.
        """
        aqid = active_question_id or self._active_question_id
        messages = build_brain_messages(
            stable_prefix=self._stable_prefix,
            transcript_window=transcript_window,
            coverage_summary=self._coverage.summary_for_prompt(),
            active_question_id=aqid,
            candidate_utterance=candidate_utterance,
        )
        timeout = (
            budget_ms if budget_ms is not None else ai_config.engine_brain_total_budget_ms
        ) / 1000.0
        try:
            decision = await asyncio.wait_for(
                _call_brain(messages=messages, correlation_id=correlation_id), timeout=timeout
            )
        except TimeoutError:
            log.warning("engine.v2.brain.timeout", turn_ref=turn_ref, timeout_s=timeout)
            return self._fallback(turn_ref, candidate_utterance, reason="brain timeout")
        except Exception:  # noqa: BLE001 — the brain must never crash the session
            log.warning("engine.v2.brain.error", turn_ref=turn_ref, exc_info=True)
            return self._fallback(turn_ref, candidate_utterance, reason="brain error")

        applied = self._coverage.apply_delta(decision.coverage_delta)
        policy = evaluate_policy(decision)
        move = policy.effective_move
        if move is BrainMove.probe and aqid is not None:
            self._coverage.record_probe(aqid)

        directive = self._build_directive(
            turn_ref=turn_ref, move=move, decision=decision,
            sanitized_say=policy.sanitized_say, active_question_id=aqid,
        )
        # Advance moves the active-question pointer (when the brain named a valid next question).
        if (
            move is BrainMove.advance
            and directive.act is DirectiveAct.ACK_ADVANCE
            and decision.bank_question_id in self._questions
        ):
            self._active_question_id = decision.bank_question_id
        record = TurnDecisionRecord(
            turn_ref=turn_ref,
            candidate_quote=candidate_utterance,
            attributed_signals=decision.attributed_signals,
            grade=decision.grade,
            coverage_delta={s: st.value for s, st in applied.items()},
            move=decision.move.value,
            reasoning=decision.reasoning,
            policy_checks=[*policy.checks, *policy.violations],
            directive_id=directive.id,
        )
        return directive, record

    def _build_directive(
        self,
        *,
        turn_ref: str,
        move: BrainMove,
        decision: BrainDecision,
        sanitized_say: str | None,
        active_question_id: str | None,
    ) -> Directive:
        act = _MOVE_TO_ACT[move]
        is_terminal = move in _TERMINAL_MOVES
        tone = DirectiveTone(decision.tone)
        say: str | None
        if move is BrainMove.advance:
            nxt = self._questions.get(decision.bank_question_id or "")
            if nxt is None:                    # brain didn't name a valid next q -> close out
                return Directive(
                    id=self._new_id(), turn_ref=turn_ref, act=DirectiveAct.CLOSE,
                    say=None, compose_hint="thank warmly; recruiter will follow up",
                    tone=DirectiveTone.WARM, is_terminal=True,
                )
            say = nxt.text                     # VERBATIM (D2 — brain selects, never rewrites)
        elif move is BrainMove.probe:
            active = self._questions.get(active_question_id or "")
            idx = decision.bank_follow_up_index
            if active is not None and idx is not None and 0 <= idx < len(active.follow_ups):
                say = active.follow_ups[idx]   # VERBATIM follow-up
            else:                              # no valid follow-up -> degrade to advance/close path
                return self._build_directive(
                    turn_ref=turn_ref, move=BrainMove.advance, decision=decision,
                    sanitized_say=sanitized_say, active_question_id=active_question_id,
                )
        elif move is BrainMove.repeat:
            say = None                         # mouth replays its cached last question
        else:                                  # composed acts (clarify/redirect/hold/.../close)
            say = sanitized_say
        return Directive(
            id=self._new_id(), turn_ref=turn_ref, act=act, say=say,
            compose_hint=None, tone=tone, is_terminal=is_terminal,
        )

    def _fallback(
        self, turn_ref: str, candidate_utterance: str, *, reason: str,
    ) -> tuple[Directive, TurnDecisionRecord]:
        """Deterministic safe directive when the brain times out/errors — never stall the turn."""
        uncovered = self._coverage.uncovered_mandatory()
        nxt = next(
            (
                q for q in sorted(self._config.stage.questions, key=lambda q: q.position)
                if (q.primary_signal in uncovered) or (not q.primary_signal and uncovered)
            ),
            None,
        )
        if nxt is not None:
            directive = Directive(
                id=self._new_id(), turn_ref=turn_ref, act=DirectiveAct.ACK_ADVANCE,
                say=nxt.text, tone=DirectiveTone.NEUTRAL,
            )
            move = "fallback_advance"
        else:
            directive = Directive(
                id=self._new_id(), turn_ref=turn_ref, act=DirectiveAct.CLOSE,
                say=None, compose_hint="thank warmly; recruiter will follow up",
                tone=DirectiveTone.WARM, is_terminal=True,
            )
            move = "fallback_close"
        record = TurnDecisionRecord(
            turn_ref=turn_ref, candidate_quote=candidate_utterance, move=move,
            reasoning=f"deterministic fallback ({reason})", policy_checks=["fallback"],
            directive_id=directive.id,
        )
        return directive, record
