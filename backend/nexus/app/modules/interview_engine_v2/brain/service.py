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
from dataclasses import dataclass
from typing import Literal

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


@dataclass(frozen=True)
class FloorRef:
    """The exact question-bearing line currently awaiting an answer (single source of truth for
    'what is being asked'). `canonical_text` is the brain's intent (the mouth re-renders it);
    `kind` in {main, probe, clarify}; `thread_question_id` is the bank question being graded."""
    canonical_text: str
    kind: Literal["main", "probe", "clarify"]
    thread_question_id: str | None


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


def build_speculative_directive(plane: ControlPlane, *, anticipated_turn_ref: str) -> Directive:
    """A deterministic, NON-voiced Option-C pre-stage (D3): staged while the candidate is still
    answering so the controller's stage->supersede->discard machinery runs live (CMI-4). It is
    ALWAYS superseded by the confirm decision at the boundary; its content is a benign best-effort
    guess (advance to the next uncovered question, else a hold). It calls no LLM and mutates no
    state (coverage stays the single source of truth — the speculative move is never voiced).
    """
    uncovered = plane._coverage.uncovered_mandatory()
    nxt = next(
        (q for q in sorted(plane._config.stage.questions, key=lambda q: q.position)
         if q.id != plane._active_question_id
         and ((q.primary_signal in uncovered) or (not q.primary_signal and uncovered))),
        None,
    )
    if nxt is not None:
        return Directive(id=plane._new_id(), turn_ref=anticipated_turn_ref,
                         act=DirectiveAct.ACK_ADVANCE, say=nxt.text, speculative=True)
    return Directive(id=plane._new_id(), turn_ref=anticipated_turn_ref, act=DirectiveAct.HOLD,
                     say=None, compose_hint="warm, brief — let them keep going", speculative=True,
                     tone=DirectiveTone.WARM)


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
    decision, completion = await client.chat.completions.create_with_completion(**create_kwargs)
    usage = getattr(completion, "usage", None)
    if usage is not None:
        details = getattr(usage, "prompt_tokens_details", None)
        log.info(
            "engine.v2.brain.usage",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            correlation_id=correlation_id,
        )
    return decision


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
        self._asked_ids: set[str] = set()            # questions physically ASKED (repeat-guard)
        self._pending_advance_id: str | None = None  # resolved next-q for THIS decide() (set in
        #                                              _build_directive, consumed by decide)
        self._floor: FloorRef | None = None          # last question-bearing line voiced (floor ref)
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
        self._asked_ids = {first.id}                 # the opener physically asks the first question
        self._floor = FloorRef(canonical_text=first.text, kind="main",
                               thread_question_id=first.id)
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
            active_question=self._questions.get(aqid),
            candidate_utterance=candidate_utterance,
            asked_question_ids=sorted(self._asked_ids),   # repeat-guard (brain side)
            active_probe_count=(self._coverage.probe_count(aqid) if aqid else 0),
            floor=self._floor,  # PRE-update floor: what's on the floor when the candidate spoke
            # A mandatory signal already failed/disclaimed is a pending knockout the brain must
            # complete (confirm→knockout_close), never advance past (b33f4ed5).
            failed_mandatory=self._coverage.failed_mandatory(),
        )
        timeout = (
            budget_ms if budget_ms is not None else ai_config.engine_brain_total_budget_ms
        ) / 1000.0
        try:
            decision = await asyncio.wait_for(
                _call_brain(messages=messages, correlation_id=correlation_id), timeout=timeout
            )
        except TimeoutError:
            log.warning("engine.v2.brain.timeout", turn_ref=turn_ref, timeout_s=timeout,
                        correlation_id=correlation_id)
            return self._fallback(turn_ref, candidate_utterance, reason="brain timeout")
        except Exception:  # noqa: BLE001 — the brain must never crash the session
            log.warning("engine.v2.brain.error", turn_ref=turn_ref, exc_info=True,
                        correlation_id=correlation_id)
            return self._fallback(turn_ref, candidate_utterance, reason="brain error")

        applied = self._coverage.apply_delta(decision.coverage_map())
        policy = evaluate_policy(decision)
        move = policy.effective_move
        cap_note: str | None = None
        if move is BrainMove.probe and aqid is not None and self._coverage.at_probe_cap(aqid):
            move = BrainMove.advance                 # diminishing returns — stop grinding (9f581c21)  # noqa: E501
            cap_note = "probe_cap_reached"
        if move is BrainMove.probe and aqid is not None:
            self._coverage.record_probe(aqid)

        self._pending_advance_id = None              # set by _build_directive's advance path
        directive = self._build_directive(
            turn_ref=turn_ref, move=move, decision=decision,
            sanitized_say=policy.sanitized_say, active_question_id=aqid,
            sanitized_setup=policy.sanitized_setup,
        )
        # ACK_ADVANCE is only ever produced by the advance path of _build_directive, which resolves
        # the repeat-guarded next question into _pending_advance_id (also covers the probe->advance
        # degrade). Move the pointer to — and record as ASKED — exactly the question we voiced.
        if directive.act is DirectiveAct.ACK_ADVANCE and self._pending_advance_id is not None:
            self._active_question_id = self._pending_advance_id
            self._asked_ids.add(self._pending_advance_id)
        self._update_floor(directive)  # update floor AFTER pointer is advanced (thread_question_id
        #                                reflects the new active question for ACK_ADVANCE)
        record = TurnDecisionRecord(
            turn_ref=turn_ref,
            candidate_quote=candidate_utterance,
            attributed_signals=decision.attributed_signals,
            grade=decision.grade,
            coverage_delta={s: st.value for s, st in applied.items()},
            move=decision.move.value,
            reasoning=decision.reasoning,
            policy_checks=[*policy.checks, *policy.violations, *([cap_note] if cap_note else [])],
            directive_id=directive.id,
        )
        return directive, record

    # Keep in sync with the mouth's _QUESTION_BEARING set (mouth/input_builder.py) — same concept.
    _FLOOR_KIND: dict[DirectiveAct, str] = {
        DirectiveAct.ASK: "main", DirectiveAct.ACK_ADVANCE: "main",
        DirectiveAct.PROBE: "probe", DirectiveAct.CLARIFY: "clarify",
        DirectiveAct.REDIRECT: "clarify",
    }

    def _update_floor(self, directive: Directive) -> None:
        """Record the question-bearing line just produced. Non-question acts leave the floor."""
        kind = self._FLOOR_KIND.get(directive.act)
        if kind is None or not directive.say:
            return
        self._floor = FloorRef(canonical_text=directive.say, kind=kind,
                               thread_question_id=self._active_question_id)

    def _build_directive(
        self,
        *,
        turn_ref: str,
        move: BrainMove,
        decision: BrainDecision,
        sanitized_say: str | None,
        active_question_id: str | None,
        sanitized_setup: str | None = None,
    ) -> Directive:
        act = _MOVE_TO_ACT[move]
        is_terminal = move in _TERMINAL_MOVES
        tone = DirectiveTone(decision.tone)
        say: str | None
        spoken_setup: str | None = None
        if move is BrainMove.advance:
            target_id = self._resolve_advance_target(decision.bank_question_id,
                                                     active_question_id=active_question_id)
            if target_id is None:              # invalid pick, or nothing unasked left -> close out
                return Directive(
                    id=self._new_id(), turn_ref=turn_ref, act=DirectiveAct.CLOSE,
                    say=None, compose_hint="thank warmly; recruiter will follow up",
                    tone=DirectiveTone.WARM, is_terminal=True,
                )
            self._pending_advance_id = target_id
            say = self._questions[target_id].text  # VERBATIM (D2 — brain selects, never rewrites)
            # Carry the setup only when the resolver honored the brain's own pick; if it was
            # overridden (mandatory-first), the setup describes the wrong question -> drop it.
            spoken_setup = sanitized_setup if target_id == decision.bank_question_id else None
        elif move is BrainMove.probe:
            active = self._questions.get(active_question_id or "")
            idx = decision.bank_follow_up_index
            if active is not None and idx is not None and 0 <= idx < len(active.follow_ups):
                used = self._coverage.used_follow_ups(active.id)
                if idx in used:              # already asked this follow-up -> pick an unused one
                    idx = next((i for i in range(len(active.follow_ups)) if i not in used), None)
                if idx is not None:
                    self._coverage.record_follow_up(active.id, idx)
                    say = active.follow_ups[idx]   # VERBATIM, unused follow-up
                else:                              # all follow-ups used -> advance
                    return self._build_directive(
                        turn_ref=turn_ref, move=BrainMove.advance, decision=decision,
                        sanitized_say=sanitized_say, active_question_id=active_question_id,
                        sanitized_setup=sanitized_setup)
            else:                                  # no valid follow-up index -> advance (unchanged)
                return self._build_directive(
                    turn_ref=turn_ref, move=BrainMove.advance, decision=decision,
                    sanitized_say=sanitized_say, active_question_id=active_question_id,
                    sanitized_setup=sanitized_setup,
                )
        elif move is BrainMove.repeat:
            say = None                         # mouth replays its cached last question
        elif is_terminal:                      # close / knockout_close
            # The mouth owns the warm close wording (close.txt): one line, identical whether the
            # screen ended early or ran full, so it never reveals a knockout/verdict (88d62df0: the
            # brain wrote "I don't have enough signal to continue", which leaks a judgment).
            # Drop the brain's composed_say; the close.txt act prompt composes the warm close.
            say = None
        else:                                  # composed acts (clarify/redirect/hold/...)
            say = sanitized_say
        return Directive(
            id=self._new_id(), turn_ref=turn_ref, act=act, say=say,
            compose_hint=None, tone=tone, is_terminal=is_terminal,
            spoken_setup=spoken_setup,
        )

    def _resolve_advance_target(
        self, brain_pick: str | None, *, active_question_id: str | None = None
    ) -> str | None:
        """The next question to ASK on an advance.

        While any unasked MANDATORY question with a still-uncovered signal remains, advance to the
        next such question by position (`_next_unasked`), IGNORING the brain's free pick — the brain
        must never skip an unasked mandatory question (046f21e3: it leapfrogged the mandatory
        Workato-experience question by free-picking a later Workato id). Only once no unasked
        uncovered-mandatory remains do we honor the brain's adaptive pick (optional territory); a
        garbled / already-asked / None pick still falls through to `_next_unasked`. An exhausted
        bank returns None -> the caller closes.

        `active_question_id` (the question currently on the floor) is excluded from the candidate
        pool so an advance never re-targets it — defence-in-depth that holds even on the
        probe->advance degrade path, independent of `_asked_ids` bookkeeping.
        """
        if self._has_unasked_uncovered_mandatory(active_question_id=active_question_id):
            return self._next_unasked(active_question_id=active_question_id)
        q = self._questions.get(brain_pick or "")
        if q is not None and q.id not in self._asked_ids:
            return q.id                              # optional territory -> honor the brain
        return self._next_unasked(active_question_id=active_question_id)

    def _has_unasked_uncovered_mandatory(self, *, active_question_id: str | None = None) -> bool:
        """True if some bank question is BOTH not-yet-asked AND carries a still-uncovered mandatory
        signal — i.e. a mandatory question we have not asked yet. The active question (on the floor,
        being answered now) is treated as asked so it is never counted as a skippable question."""
        uncovered = set(self._coverage.uncovered_mandatory())
        asked_or_active = self._asked_ids | ({active_question_id} if active_question_id else set())
        return any(q.id not in asked_or_active and q.primary_signal in uncovered
                   for q in self._config.stage.questions)

    def _next_unasked(self, *, active_question_id: str | None = None) -> str | None:
        """The next not-yet-asked question by position, preferring one whose primary signal is still
        an uncovered mandatory; None when every question has been asked. The active question is
        excluded — advancing to it would be a re-ask."""
        uncovered = set(self._coverage.uncovered_mandatory())
        asked_or_active = self._asked_ids | ({active_question_id} if active_question_id else set())
        unasked = [q for q in sorted(self._config.stage.questions, key=lambda q: q.position)
                   if q.id not in asked_or_active]
        if not unasked:
            return None
        return next((q.id for q in unasked
                     if (q.primary_signal in uncovered) or (not q.primary_signal and uncovered)),
                    unasked[0].id)

    def _fallback(
        self, turn_ref: str, candidate_utterance: str, *, reason: str,
    ) -> tuple[Directive, TurnDecisionRecord]:
        """Deterministic safe directive when the brain times out/errors — never stall the turn.

        Walks STRICTLY FORWARD past the current active question by `position` (preferring still-
        uncovered mandatory ones) and moves the pointer to the chosen question, so repeated
        fallbacks march through the bank instead of re-asking the same question forever — the
        infinite-Q1 loop guard (defense-in-depth for total brain failure).
        """
        uncovered = self._coverage.uncovered_mandatory()
        ordered = sorted(self._config.stage.questions, key=lambda q: q.position)
        cur_pos = (
            self._questions[self._active_question_id].position
            if self._active_question_id in self._questions
            else None
        )
        # questions strictly after the current one (by position); if no pointer yet, all of them
        ahead = [q for q in ordered if cur_pos is None or q.position > cur_pos]

        def _eligible(q: object) -> bool:
            # a question with no primary_signal is eligible if any mandatory remains uncovered
            return (q.primary_signal in uncovered) or (not q.primary_signal and bool(uncovered))

        # prefer the next still-uncovered question; else just the next question in order
        nxt = next((q for q in ahead if _eligible(q)), None) or next(iter(ahead), None)
        if nxt is not None:
            # advance the pointer so the next fallback moves on (no infinite-Q1 loop)
            self._active_question_id = nxt.id
            self._asked_ids.add(nxt.id)        # keep the repeat-guard truthful on brain recovery
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
        self._update_floor(directive)  # ACK_ADVANCE moves the floor; CLOSE has say=None so no-ops
        record = TurnDecisionRecord(
            turn_ref=turn_ref, candidate_quote=candidate_utterance, move=move,
            reasoning=f"deterministic fallback ({reason})", policy_checks=["fallback"],
            directive_id=directive.id,
        )
        return directive, record
