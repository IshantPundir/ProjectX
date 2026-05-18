"""SpeakerService — OpenAI Responses API streaming → AsyncIterable[str].

The `stream()` method returns a handle whose `.stream()` yields token deltas as
they arrive. The orchestrator passes that AsyncIterable directly to
`session.say(stream, allow_interruptions=True)`. After streaming completes,
`.final_text()` returns the assembled utterance.

Per-call prompt resolution (Task 11): the system prompt is composed at
``stream()`` time from ``engine/speaker/_preamble`` plus the per-action
body keyed by ``speaker_input.instruction_kind``. The resolved body's
``sha256:<hex>`` hash is exposed on the handle so the orchestrator can
record exactly which prompt was used in the per-call ``speaker.call``
audit event.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, AsyncIterator

from app.ai.prompts import PromptLoader, prompt_loader
from app.ai.tracing import set_llm_span_attributes
from app.modules.interview_engine.models.speaker import SpeakerInput


class SpeakerStreamHandle:
    """Encapsulates the streaming Speaker call's lifecycle + telemetry."""

    def __init__(self, *, model: str) -> None:
        self._model = model
        self._final_text: str = ""
        self._chunks: list[str] = []
        self._usage: dict[str, int] | None = None
        self._latency_ms_first_token: int | None = None
        self._latency_ms_total: int | None = None
        self._stream_iterator: AsyncIterator[str] | None = None
        self._completed = False
        # Set per-call by SpeakerService.stream() before the producer
        # is attached. Exposes the sha256 hash of the composed
        # (preamble + per-action body) so the orchestrator can record
        # the exact prompt used in the speaker.call audit event.
        self._prompt_hash: str = ""
        # Phase 9.3 diagnostic state — populated by the producer for
        # every call, but only consumed by the orchestrator's
        # speaker.output.empty audit path. Captures every Responses-API
        # event type we observed (so the absence of any
        # ``response.output_text.delta`` is visible), plus any refusal
        # deltas (the most common cause of empty output is a content
        # filter rejection, which the API signals via
        # ``response.refusal.delta`` + ``response.refusal.done``).
        self._event_types_seen: list[str] = []
        self._refusal_chunks: list[str] = []
        self._response_id: str | None = None
        self._finish_reason: str | None = None

    @property
    def latency_ms_first_token(self) -> int:
        return self._latency_ms_first_token or 0

    @property
    def latency_ms_total(self) -> int:
        return self._latency_ms_total or 0

    @property
    def usage(self) -> dict[str, int] | None:
        return self._usage

    @property
    def prompt_hash(self) -> str:
        """sha256:<hex> of the composed (preamble + per-action body)."""
        return self._prompt_hash

    @property
    def event_types_seen(self) -> list[str]:
        """Every Responses-API event type observed during streaming.

        Useful only when ``final_text()`` returns empty: a normal turn
        contains ``response.output_text.delta`` events; an empty turn
        typically does not, and may instead show ``response.refusal.delta``
        (content filter) or only ``response.completed`` (model gave up).
        """
        return list(self._event_types_seen)

    @property
    def refusal_text(self) -> str | None:
        """Joined ``response.refusal.delta`` content if any, else None.

        Non-None means OpenAI's content filter or alignment guardrail
        rejected the request. The string itself is the model's refusal
        message (typically a short safety boilerplate).
        """
        if not self._refusal_chunks:
            return None
        return "".join(self._refusal_chunks)

    @property
    def response_id(self) -> str | None:
        """OpenAI's request id (when surfaced) — for upstream trace lookup."""
        return self._response_id

    @property
    def finish_reason(self) -> str | None:
        """``stop`` / ``content_filter`` / ``length`` / ``tool_calls`` etc.

        ``content_filter`` paired with empty output is the textbook
        safety-rejection shape; ``stop`` with empty output usually means
        the model decided there was nothing to say given the prompt.
        """
        return self._finish_reason

    def stream(self) -> AsyncIterator[str]:
        if self._stream_iterator is None:
            raise RuntimeError("SpeakerStreamHandle.stream() called before producer attached")
        return self._stream_iterator

    async def final_text(self) -> str:
        # Drains the stream if not yet drained.
        if not self._completed:
            async for _ in self.stream():
                pass
        return self._final_text


class SpeakerService:
    def __init__(
        self,
        *,
        openai_client: Any,
        model: str,
        loader: PromptLoader | None = None,
    ) -> None:
        self._client = openai_client
        self._model = model
        # Version-selected PromptLoader. Defaults to the module-level v1
        # singleton for backward compat; agent.py passes a versioned loader.
        self._loader: PromptLoader = loader if loader is not None else prompt_loader
        # Render preamble once from PersonaSpec. Result is deterministic
        # so the composed (preamble + per-action) system prompt is
        # byte-identical across calls of the same kind — that's what
        # triggers OpenAI prompt caching.
        from app.modules.interview_engine.speaker.persona import (
            DEFAULT_PERSONA,
            render_preamble,
        )
        preamble_template: str = self._loader.get("engine/speaker/_preamble")
        self._rendered_preamble: str = render_preamble(preamble_template, DEFAULT_PERSONA)

    def _resolve_prompt(self, instruction_kind: Any) -> tuple[str, str]:
        """Compose rendered preamble + per-action body for ``instruction_kind``.

        Returns ``(composed_body, sha256_hex_hash)``. The preamble is
        pre-rendered at __init__ time; only the per-action body is loaded
        per-call (from PromptLoader's cache). Hash is over the exact
        bytes sent to the model.
        """
        per_action: str = self._loader.get(
            f"engine/speaker/{instruction_kind.value}"
        )
        body = self._rendered_preamble + "\n\n" + per_action
        digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        return body, digest

    async def stream(
        self,
        *,
        turn_id: str,
        speaker_input: SpeakerInput,
        correlation_id: str,
        tenant_id: str,
    ) -> SpeakerStreamHandle:
        system_prompt, prompt_hash = self._resolve_prompt(
            speaker_input.instruction_kind,
        )
        set_llm_span_attributes(
            prompt_name=f"engine/speaker/{speaker_input.instruction_kind.value}",
            prompt_version=self._loader.version,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            turn_id=turn_id,
            model=self._model,
            instruction_kind=speaker_input.instruction_kind.value,
        )

        handle = SpeakerStreamHandle(model=self._model)
        handle._prompt_hash = prompt_hash
        started = time.monotonic()

        from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA

        cm = self._client.responses.stream(
            model=self._model,
            instructions=system_prompt,
            input=speaker_input.model_dump_json(),
            reasoning={"effort": "none"},
            temperature=DEFAULT_PERSONA.speaker_llm_temperature,
        )

        async def _producer() -> AsyncIterator[str]:
            async with cm as stream:
                async for event in stream:
                    etype = getattr(event, "type", "")
                    # Diagnostic capture (Phase 9.3): record every event
                    # type so an empty-output turn surfaces the actual
                    # API shape (refusal? completed-without-deltas?
                    # only error events?) in the audit envelope.
                    if etype:
                        handle._event_types_seen.append(etype)
                    if etype == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        if not delta:
                            continue
                        if handle._latency_ms_first_token is None:
                            handle._latency_ms_first_token = int(
                                (time.monotonic() - started) * 1000
                            )
                        handle._chunks.append(delta)
                        yield delta
                    elif etype == "response.refusal.delta":
                        # Content-filter / safety refusal. The model is
                        # explicitly declining to respond — this is the
                        # most common cause of empty output we have seen
                        # on adversarial candidate inputs.
                        delta = getattr(event, "delta", "")
                        if delta:
                            handle._refusal_chunks.append(delta)
                    elif etype == "response.completed":
                        response = getattr(event, "response", None)
                        if response is not None:
                            rid = getattr(response, "id", None)
                            if isinstance(rid, str):
                                handle._response_id = rid
                            # Best-effort: finish_reason can live on the
                            # response object directly OR per-output-item
                            # depending on SDK version. Try both.
                            fr = getattr(response, "finish_reason", None)
                            if not isinstance(fr, str):
                                outputs = getattr(response, "output", None) or []
                                for item in outputs:
                                    fr = getattr(item, "finish_reason", None)
                                    if isinstance(fr, str):
                                        break
                            if isinstance(fr, str):
                                handle._finish_reason = fr
                        usage = getattr(response, "usage", None) if response else None
                        if usage is not None:
                            # Responses API: usage.input_tokens_details.cached_tokens.
                            # isinstance(int) guards against MagicMock auto-generated
                            # children in unit tests (int(MagicMock()) returns 1).
                            details = getattr(usage, "input_tokens_details", None)
                            cached_raw = (
                                getattr(details, "cached_tokens", 0)
                                if details is not None else 0
                            )
                            cached = cached_raw if isinstance(cached_raw, int) else 0
                            handle._usage = {
                                "prompt_tokens": getattr(usage, "input_tokens", 0),
                                "completion_tokens": getattr(usage, "output_tokens", 0),
                                "cached_tokens": cached,
                            }
            handle._final_text = "".join(handle._chunks)
            handle._latency_ms_total = int((time.monotonic() - started) * 1000)
            handle._completed = True

        handle._stream_iterator = _producer()
        return handle
