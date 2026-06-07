"""
Mouth real-line service — E2 task.

ConversationPlane.real_line(mouth_input: MouthTurnInput) -> str

Responsibilities:
  1. For act in {ask, probe, repeat} where directive.say is set, return
     directive.say VERBATIM (zero LLM call, zero latency added, meaning exact).
     repeat is verbatim for the same reason: the brain has already committed to
     replaying on_the_floor bit-for-bit; reshaping it would shift the candidate's
     attention or create confusion about which question is being repeated.
  2. For all other acts (clarify, redirect, reassure, answer_meta, close):
     a. Load the per-act instruction block from prompts/v{N}/engine/mouth/<act>.
     b. Build the three-part message list (persona | act block | dynamic suffix).
     c. Call the injected (or default real) LLM and return the stripped response.

validate_no_leak(messages, *, rubric_secrets):
     Test/assertion helper. Returns True iff none of the rubric_secrets appear
     in any message content. Proves the mouth message list cannot carry rubric
     material (the structural guarantee — the Directive has no rubric fields —
     makes this True by construction; this function makes that claim testable).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache

import structlog

from app.modules.interview_engine.contracts import (
    DirectiveAct,
    MouthTurnInput,
)
from app.modules.interview_engine.mouth.persona import build_mouth_messages, build_persona

_log = structlog.get_logger()

# Acts for which directive.say is spoken verbatim — no LLM call.
_VERBATIM_ACTS = {DirectiveAct.ask, DirectiveAct.probe, DirectiveAct.repeat}

# Last-resort spoken line if the mouth LLM fails AND the directive carries no
# `say` (e.g. a close with say=None). Keeps the interview alive (no dead air,
# no crash) — F3-tunable.
_SAFE_FALLBACK_LINE = "Okay — let's continue."


# ---------------------------------------------------------------------------
# Act-block loader (per-version, per-act cache)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _load_act_block(act: str, version: str) -> str:
    """Load the per-act instruction block, one dict-entry per (act, version) pair.

    Cached so the filesystem read happens once per process per (act, version).
    """
    from app.ai.prompts import PromptLoader
    return PromptLoader(version).get(f"engine/mouth/{act}")


# ---------------------------------------------------------------------------
# validate_no_leak
# ---------------------------------------------------------------------------

def validate_no_leak(messages: list[dict], *, rubric_secrets: list[str]) -> bool:
    """Return True iff none of the rubric_secrets appear in any message content.

    Structural backstop: the mouth never receives a rubric (Directive has no
    rubric fields), so this should always return True. Use in tests to prove
    the invariant for a given message list.

    Only secrets whose length >= 10 characters are checked (short strings risk
    false positives from coincidental substring matches).
    """
    qualifying = [s for s in rubric_secrets if len(s) >= 10]
    for msg in messages:
        content = msg.get("content", "") or ""
        for secret in qualifying:
            if secret in content:
                return False
    return True


# ---------------------------------------------------------------------------
# ConversationPlane
# ---------------------------------------------------------------------------

class ConversationPlane:
    """Renders the brain's Directive as a natural spoken Indian English line.

    The mouth is the ONLY tier the candidate ever hears. It is structurally
    isolated from rubric data: the Directive type carries no rubric fields,
    so no rubric can reach the message builder even by accident.

    Parameters
    ----------
    persona_name:
        The interviewer's display name (e.g. "Arjun").
    job_title:
        The role being screened (e.g. "Integration Engineer").
    version:
        Prompt version to load (defaults to ai_config.engine_mouth_prompt_version).
    llm_call:
        INJECTABLE SEAM — async callable that takes a list[dict] of messages
        and returns a str. None → _default_mouth_llm (real API call). Pass a
        fake in tests to avoid any network call.
    """

    def __init__(
        self,
        *,
        persona_name: str,
        job_title: str,
        version: str | None = None,
        llm_call: Callable[[list[dict]], Awaitable[str]] | None = None,
    ) -> None:
        if version is None:
            from app.ai.config import ai_config
            version = ai_config.engine_mouth_prompt_version

        self._version = version
        self._persona_name = persona_name
        self._job_title = job_title
        self._persona = build_persona(
            persona_name=persona_name,
            job_title=job_title,
            version=version,
        )
        self._llm_call: Callable[[list[dict]], Awaitable[str]] = (
            llm_call if llm_call is not None else self._default_mouth_llm
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def intro(self, *, candidate_name: str, role_summary: str, company_about: str) -> str:
        """Compose the warm opening (greeting + one-line job brief) spoken BEFORE the
        first question. Ends on a confident statement that flows into the first
        question — never a "shall we?" (see intro.txt). Leak-proof (no rubric). On
        any LLM error/timeout returns a safe canned greeting (never dead air)."""
        intro_block = _load_act_block("intro", self._version)
        context = (
            f"CANDIDATE FIRST NAME: {candidate_name}\n"
            f"ROLE: {self._job_title}\n"
            f"ROLE SUMMARY: {role_summary}\n"
            f"COMPANY: {company_about}"
        )
        messages = [
            {"role": "system", "content": self._persona},
            {"role": "system", "content": intro_block},
            {"role": "user", "content": context},
        ]
        try:
            raw = await self._llm_call(messages)
        except Exception:  # noqa: BLE001 — an intro blip must never break the session
            _log.warning("mouth.intro.fallback", exc_info=True)
            return f"Hi {candidate_name} — thanks for joining today. So, let's get into it."
        return (raw.strip() if raw else "") or (
            f"Hi {candidate_name} — thanks for joining today. So, let's get into it."
        )

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        """Render the Directive as a spoken string.

        Verbatim shortcut (ask / probe / repeat with say set):
            Return directive.say directly. The brain/resolver has already
            committed to this exact text — re-running it through the LLM would
            risk meaning drift. Zero latency added.

        LLM path (clarify / redirect / reassure / answer_meta / close):
            Load the per-act instruction block, build the three-part message
            list, call the LLM, and return the stripped response.
        """
        directive = mouth_input.directive
        act = directive.act

        # ── Verbatim shortcut ────────────────────────────────────────────────
        if act in _VERBATIM_ACTS and directive.say:
            _log.debug("mouth.verbatim_shortcut", act=act.value)
            return directive.say

        # ── LLM composition path ─────────────────────────────────────────────
        act_block = _load_act_block(act.value, self._version)
        messages = build_mouth_messages(
            persona=self._persona,
            act_block=act_block,
            mouth_input=mouth_input,
        )

        _log.debug("mouth.llm_call", act=act.value, n_messages=len(messages))
        # Graceful degradation: a mouth-LLM blip must NEVER kill the interview.
        # Fall back to the directive's own text (composed_say for clarify/redirect/
        # reassure/answer_meta) or a safe neutral line for close.
        try:
            raw = await self._llm_call(messages)
        except Exception:  # noqa: BLE001 — never propagate a mouth failure into the drive loop
            _log.warning("mouth.llm_call.fallback", act=act.value, exc_info=True)
            return directive.say or _SAFE_FALLBACK_LINE
        line = raw.strip() if raw else ""
        return line or directive.say or _SAFE_FALLBACK_LINE

    # -----------------------------------------------------------------------
    # Default real LLM call (injectable seam — only reached in production)
    # -----------------------------------------------------------------------

    async def _default_mouth_llm(self, messages: list[dict]) -> str:
        """Real raw-client call — mirrors realtime.build_mouth_llm_plugin effort-gating."""
        # Lazy imports keep this module free of livekit and app startup cost at
        # import time; the FastAPI process never loads realtime SDKs.
        from app.ai.client import get_raw_openai_client
        from app.ai.config import ai_config

        # The raw AsyncOpenAI client already sets max_retries=1 at construction
        # (_build_raw_openai_client); it is NOT a valid per-call create() kwarg.
        client = get_raw_openai_client()
        kwargs: dict = {
            "model": ai_config.engine_mouth_model,
            "messages": messages,
        }
        if ai_config.engine_mouth_effort:
            kwargs["reasoning_effort"] = ai_config.engine_mouth_effort
        if ai_config.engine_mouth_prompt_cache_key:
            kwargs["prompt_cache_key"] = ai_config.engine_mouth_prompt_cache_key

        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
