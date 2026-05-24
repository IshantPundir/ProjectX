"""ConversationPlane (the mouth) — per-turn prompt orchestration (no livekit).

Holds the versioned PromptLoader + the rendered (byte-stable) persona preamble, loads the
per-act block for a directive, assembles the bounded message list (via input_builder), and
tracks the last question delivered so REPEAT can replay it. Also pre-renders persona-voiced
reflex cues ONCE at session start (the HOLD/REASSURE decision): an off-critical-path
instructor call, with the canned Settings strings as the seed + fallback so the behavioral
layer never breaks. The actual LLM voicing per turn happens in agent.py's llm_node, which
sends `build_turn_messages(...)` through the mouth LLM plugin.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from app.ai.config import ai_config
from app.ai.prompts import PromptLoader
from app.modules.interview_engine_v2.directive import Directive, DirectiveAct
from app.modules.interview_engine_v2.mouth.input_builder import (
    build_mouth_messages,
    effective_say,
    is_question_bearing,
)
from app.modules.interview_engine_v2.mouth.persona import render_persona_preamble

log = structlog.get_logger("interview_engine_v2.mouth")

# DirectiveAct -> prompt name under prompts/v{version}/engine/mouth/.
_ACT_PROMPT: dict[DirectiveAct, str] = {
    DirectiveAct.INTRO: "engine/mouth/intro",
    DirectiveAct.ASK: "engine/mouth/ask",
    DirectiveAct.PROBE: "engine/mouth/probe",
    DirectiveAct.CLARIFY: "engine/mouth/clarify",
    DirectiveAct.ACK_ADVANCE: "engine/mouth/ack_advance",
    DirectiveAct.REPEAT: "engine/mouth/repeat",
    DirectiveAct.REDIRECT: "engine/mouth/redirect",
    DirectiveAct.HOLD: "engine/mouth/hold",
    DirectiveAct.REASSURE: "engine/mouth/reassure",
    DirectiveAct.HINT: "engine/mouth/hint",
    DirectiveAct.ANSWER_META: "engine/mouth/answer_meta",
    DirectiveAct.CONFIRM: "engine/mouth/confirm",
    DirectiveAct.CLOSE: "engine/mouth/close",
}


class ReflexCueVariants(BaseModel):
    """Persona-voiced variants of the silence-timer reflex cues + the M5 ack-mask.

    `acknowledgment` (M5/D3): content-free "mm, okay —" beats played the instant the candidate
    finishes, to MASK the brain's parallel reasoning (never a silent wait). Like the other cues
    they are pre-rendered once at session start; the canned `settings.engine_v2_ack_messages` list
    is the seed + fallback.
    """

    hold_space: list[str] = Field(min_length=1)
    gentle_nudge: list[str] = Field(min_length=1)
    still_there: list[str] = Field(min_length=1)
    # Defaulted so a pre-render that omits it (or an older caller) stays valid; the agent's _ack
    # falls back to settings.engine_v2_ack_messages when the pool is empty/absent anyway.
    acknowledgment: list[str] = Field(default_factory=lambda: ["Mm, okay."], min_length=1)


class ConversationPlane:
    """The mouth: turns a Directive into a bounded, cache-stable mouth-LLM prompt."""

    def __init__(self, *, loader: PromptLoader, persona_name: str, job_title: str) -> None:
        self._loader = loader
        self._persona_name = persona_name
        self._job_title = job_title
        self._persona_preamble = render_persona_preamble(
            loader=loader, persona_name=persona_name, job_title=job_title,
        )
        self._last_question: str | None = None

    @property
    def persona_preamble(self) -> str:
        """The byte-stable cache prefix (rendered once)."""
        return self._persona_preamble

    @property
    def last_question(self) -> str | None:
        """The most recently delivered question-bearing line (REPEAT cache / triage context)."""
        return self._last_question

    def build_turn_messages(
        self, directive: Directive, *, candidate_utterance: str | None,
        just_said_filler: str | None = None,
    ) -> list[dict[str, str]]:
        """Assemble the [persona | act | dynamic] messages and update the REPEAT cache.

        `just_said_filler` (Pass-2 linking, design §5): the line triage just spoke; the mouth
        continues from it without repeating it, while delivering the directive's substance
        faithfully (verbatim bank text stays intact)."""
        act_block = self._loader.get(_ACT_PROMPT[directive.act])
        messages = build_mouth_messages(
            directive=directive,
            persona_preamble=self._persona_preamble,
            act_block=act_block,
            candidate_utterance=candidate_utterance,
            last_question=self._last_question,
            just_said_filler=just_said_filler,
        )
        if is_question_bearing(directive.act):
            say = effective_say(directive, last_question=self._last_question)
            if say:
                self._last_question = say
        return messages

    async def prerender_reflex_variants(
        self, *, hold_seed: str, nudge_seed: str, still_seed: str,
        ack_seed: list[str] | None = None,
    ) -> ReflexCueVariants:
        """Pre-render persona-voiced reflex cues once at session start; fall back to seeds.

        `ack_seed` is the canned content-free ack-mask list (settings.engine_v2_ack_messages) —
        the seed/fallback for the M5 ack that masks the brain's parallel reasoning (D3).
        """
        try:
            return await self._call_reflex_llm()
        except Exception:  # noqa: BLE001 — never let pre-render break the behavioral layer
            log.warning("mouth.reflex_prerender_failed_using_seeds", exc_info=True)
            return ReflexCueVariants(
                hold_space=[hold_seed], gentle_nudge=[nudge_seed], still_there=[still_seed],
                acknowledgment=list(ack_seed) if ack_seed else ["Mm, okay."],
            )

    async def _call_reflex_llm(self) -> ReflexCueVariants:
        """One instructor structured call on engine_mouth_model (off the critical path).

        The persona template is the sole guide; the seed strings passed to
        prerender_reflex_variants are NOT forwarded here — they are used only as the
        fallback when this call fails.
        """
        from app.ai.client import get_openai_client

        client = get_openai_client()
        prompt = self._loader.get("engine/mouth/reflex_cues").format(
            persona_name=self._persona_name, job_title=self._job_title,
        )
        create_kwargs: dict[str, object] = {
            "model": ai_config.engine_mouth_model,
            "response_model": ReflexCueVariants,
            "messages": [{"role": "system", "content": prompt}],
        }
        if ai_config.engine_mouth_effort:
            create_kwargs["reasoning_effort"] = ai_config.engine_mouth_effort
        return await client.chat.completions.create(**create_kwargs)
