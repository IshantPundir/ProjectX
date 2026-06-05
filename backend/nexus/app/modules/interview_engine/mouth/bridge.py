"""
Mouth bridge — E3 task.

BridgeComposer.bridge(req: BridgeRequest) -> str

The instant the Ear commits the candidate's turn, the Mouth speaks an immediate
context-aware gist-mirror beat — IN PARALLEL with the brain (which hasn't
decided yet). The bridge sees ONLY the candidate's words (BridgeRequest) — never
a rubric, never the brain's output. It fires before the brain decides → it MUST
commit to NOTHING about quality or the next move (else it risks contradicting the
brain once it lands).

Responsibilities:
  1. Build the message list (persona preamble | bridge block | candidate suffix).
     The suffix carries ONLY the candidate's utterance (fenced as DATA) and the
     recent openers — nothing else.
  2. Call the injected LLM seam wrapped in asyncio.wait_for (if timeout_s set).
  3. On ANY error or timeout → log a warning and return CANNED_BRIDGE_FALLBACK.
  4. On success → return the stripped beat. Whitespace-only → also fall back.
  5. NEVER raises from bridge(). Dead air is never acceptable.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from app.modules.interview_engine.contracts import BridgeRequest
from app.modules.interview_engine.loop import CANNED_BRIDGE_FALLBACK
from app.modules.interview_engine.mouth.persona import build_persona

_log = structlog.get_logger()

# Sentinel fence tags — candidate utterance is DATA, never instructions.
_ANSWER_BEGIN = "<<<CANDIDATE_ANSWER_BEGIN>>>"
_ANSWER_END = "<<<CANDIDATE_ANSWER_END>>>"

# Default bridge timeout (seconds). Short: the bridge must resolve fast to mask the brain.
# Phase F3 may tune this via ai_config; exposed as a constructor param.
_DEFAULT_BRIDGE_TIMEOUT_S: float = 1.5


class BridgeComposer:
    """Emits an immediate gist-mirror beat while the brain is running.

    Structurally isolated from the rubric: BridgeRequest has no rubric or
    directive field, so nothing can inject one even accidentally.

    Parameters
    ----------
    persona_name:
        The interviewer's display name (e.g. "Arjun"). Used in the persona preamble.
    job_title:
        The role being screened (e.g. "Integration Engineer"). Used in the persona.
    version:
        Prompt version directory (e.g. "v4"). Defaults to
        ``ai_config.engine_mouth_prompt_version`` when None.
    llm_call:
        INJECTABLE SEAM — async callable that takes a list[dict] of messages and
        returns a str. None → _default_bridge_llm (real API call). Pass a fake in
        tests to avoid any network call.
    timeout_s:
        Seconds to wait for the LLM before falling back to the canned beat.
        None → ``_DEFAULT_BRIDGE_TIMEOUT_S``. Phase F3 may expose this via
        ai_config; for now a small constant is fine.
    """

    def __init__(
        self,
        *,
        persona_name: str,
        job_title: str,
        version: str | None = None,
        llm_call: Callable[[list[dict]], Awaitable[str]] | None = None,
        timeout_s: float | None = None,
    ) -> None:
        if version is None:
            from app.ai.config import ai_config
            version = ai_config.engine_mouth_prompt_version

        self._version = version
        self._timeout_s: float = timeout_s if timeout_s is not None else _DEFAULT_BRIDGE_TIMEOUT_S

        # Build and cache the persona once — it's byte-identical across all bridge
        # calls in this session (same persona_name + job_title + version).
        self._persona: str = build_persona(
            persona_name=persona_name,
            job_title=job_title,
            version=version,
        )

        # Lazy-load the bridge block once and cache it (same version across the session).
        self._bridge_block: str = self._load_bridge_block(version)

        self._llm_call: Callable[[list[dict]], Awaitable[str]] = (
            llm_call if llm_call is not None else self._default_bridge_llm
        )

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def build_messages(self, req: BridgeRequest) -> list[dict]:
        """Assemble the three-part message list for the bridge LLM call.

        Structure
        ---------
        [
            {"role": "system", "content": <persona preamble>},
            {"role": "system", "content": <bridge instruction block>},
            {"role": "user",   "content": <candidate utterance (DATA-fenced) + RECENT OPENERS>},
        ]

        The user suffix carries ONLY:
          - The candidate's utterance, fenced as DATA (identity lock — cannot be instructions).
          - RECENT OPENERS: the recent opening connectives to avoid.

        NO rubric, NO directive, NO brain output EVER appears here. BridgeRequest
        has no such fields, so this is a structural guarantee.
        """
        openers_text = ", ".join(req.recent_openers) if req.recent_openers else ""

        user_suffix = (
            f"{_ANSWER_BEGIN}\n"
            f"{req.candidate_utterance}\n"
            f"{_ANSWER_END}\n"
            f"RECENT OPENERS: {openers_text}"
        )

        return [
            {"role": "system", "content": self._persona},
            {"role": "system", "content": self._bridge_block},
            {"role": "user", "content": user_suffix},
        ]

    async def bridge(self, req: BridgeRequest) -> str:
        """Emit an immediate gist-mirror beat, or a canned fallback on any failure.

        NEVER raises. Dead air is never acceptable — if the LLM call fails (network
        error, timeout, bad response) the caller always gets a spoken string back.

        Parameters
        ----------
        req:
            The BridgeRequest from the Ear (candidate_utterance + recent_openers only).

        Returns
        -------
        str
            A short spoken beat (stripped). Falls back to CANNED_BRIDGE_FALLBACK on
            any error, timeout, or empty response.
        """
        messages = self.build_messages(req)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(messages),
                timeout=self._timeout_s,
            )
            stripped = raw.strip() if raw else ""
            if not stripped:
                _log.warning(
                    "engine.mouth.bridge.fallback",
                    reason="empty_output",
                    timeout_s=self._timeout_s,
                )
                return CANNED_BRIDGE_FALLBACK
            return stripped
        except Exception as exc:
            reason = "timeout" if isinstance(exc, asyncio.TimeoutError) else "error"
            _log.warning(
                "engine.mouth.bridge.fallback",
                reason=reason,
                exc_type=type(exc).__name__,
                timeout_s=self._timeout_s,
            )
            return CANNED_BRIDGE_FALLBACK

    # ---------------------------------------------------------------------------
    # Default real LLM call (injectable seam — only reached in production)
    # ---------------------------------------------------------------------------

    async def _default_bridge_llm(self, messages: list[dict]) -> str:
        """Real raw-client call — mirrors mouth/service.py::ConversationPlane._default_mouth_llm."""
        # Lazy imports keep this module free of livekit and app startup cost at
        # import time. The FastAPI process never loads realtime SDKs.
        from app.ai.client import get_raw_openai_client
        from app.ai.config import ai_config

        # max_retries is set at client construction, not a valid create() kwarg.
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

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _load_bridge_block(version: str) -> str:
        """Load the bridge prompt block once per version."""
        from app.ai.prompts import PromptLoader
        return PromptLoader(version).get("engine/mouth/bridge")
