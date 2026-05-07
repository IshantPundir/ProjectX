"""SpeakerService — OpenAI Responses API streaming → AsyncIterable[str].

The `stream()` method returns a handle whose `.stream()` yields token deltas as
they arrive. The orchestrator passes that AsyncIterable directly to
`session.say(stream, allow_interruptions=True)`. After streaming completes,
`.final_text()` returns the assembled utterance.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator

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

    @property
    def latency_ms_first_token(self) -> int:
        return self._latency_ms_first_token or 0

    @property
    def latency_ms_total(self) -> int:
        return self._latency_ms_total or 0

    @property
    def usage(self) -> dict[str, int] | None:
        return self._usage

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
        system_prompt: str,
        system_prompt_hash: str,
    ) -> None:
        self._client = openai_client
        self._model = model
        self._system_prompt = system_prompt
        self._system_prompt_hash = system_prompt_hash

    async def stream(
        self,
        *,
        turn_id: str,
        speaker_input: SpeakerInput,
        correlation_id: str,
        tenant_id: str,
    ) -> SpeakerStreamHandle:
        set_llm_span_attributes(
            prompt_name="engine/speaker.system",
            prompt_version="v1",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            turn_id=turn_id,
            model=self._model,
            instruction_kind=speaker_input.instruction_kind.value,
        )

        handle = SpeakerStreamHandle(model=self._model)
        started = time.monotonic()

        cm = self._client.responses.stream(
            model=self._model,
            instructions=self._system_prompt,
            input=speaker_input.model_dump_json(),
        )

        async def _producer() -> AsyncIterator[str]:
            async with cm as stream:
                async for event in stream:
                    etype = getattr(event, "type", "")
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
                    elif etype == "response.completed":
                        response = getattr(event, "response", None)
                        usage = getattr(response, "usage", None) if response else None
                        if usage is not None:
                            handle._usage = {
                                "prompt_tokens": getattr(usage, "input_tokens", 0),
                                "completion_tokens": getattr(usage, "output_tokens", 0),
                            }
            handle._final_text = "".join(handle._chunks)
            handle._latency_ms_total = int((time.monotonic() - started) * 1000)
            handle._completed = True

        handle._stream_iterator = _producer()
        return handle
