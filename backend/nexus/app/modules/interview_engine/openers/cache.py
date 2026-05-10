"""OpenerCacheBuilder — pre-synthesizes opener audio at engine startup.

See docs/superpowers/specs/2026-05-10-opener-prefetch-architecture-design.md §4.2
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from app.modules.interview_engine.openers.library import OpenerLibrary, OpenerVariant

if TYPE_CHECKING:
    from livekit.agents.tts import TTS

log = structlog.get_logger("interview-engine.openers")

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 0.2


@dataclass
class BuildReport:
    """Summary of a build_opener_cache run.

    Used by the engine entrypoint to log degraded-mode warnings when
    some variants failed to synthesize.
    """
    success_count: int = 0
    failed_variants: list[tuple[str, str]] = field(default_factory=list)
    """Pairs of (variant.text, error_message) for variants that failed."""
    total_synthesis_time_ms: int = 0


async def _synthesize_variant(
    variant: OpenerVariant, tts: TTS,
) -> tuple[OpenerVariant, Exception | None]:
    """Synthesize one variant with bounded exponential-backoff retry on
    transient errors. Returns (variant, None) on success or (variant,
    last_error) after all retries exhausted.

    Retried errors: ``asyncio.TimeoutError`` and ``OSError`` (DNS
    failures bubble up via httpcore as OSError subclasses; TCP resets
    likewise). Non-retried: every other exception (4xx auth/validation
    errors, content-filter rejections, schema errors — these will not
    change on retry and we'd just hide the real problem).

    Bounded budget: 3 attempts with exponential backoff (200ms, 400ms,
    800ms = 1.4s max wait), tested in
    ``test_synthesize_variant_exhausts_retries_then_returns_last_error``.

    See spec ``docs/superpowers/specs/2026-05-10-intro-prefetch-and-cache-integrity-design.md``
    §4.2 for the rationale (Bug C from session a998073a-3007-... boot).
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            frames: list = []
            # tts.synthesize(text) returns an async context manager wrapping
            # the audio stream. Each yielded event has a `frame` attribute.
            async with tts.synthesize(variant.text) as stream:
                async for ev in stream:
                    frame = getattr(ev, "frame", None)
                    if frame is not None:
                        frames.append(frame)
            if not frames:
                return variant, RuntimeError("empty audio stream")
            variant.audio_frames = frames
            return variant, None
        except (asyncio.TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                backoff_s = _RETRY_BASE_DELAY_S * (2 ** attempt)
                log.warning(
                    "openers.cache.synth.retry",
                    variant_text=variant.text[:40],
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    backoff_ms=int(backoff_s * 1000),
                )
                await asyncio.sleep(backoff_s)
                continue
            return variant, exc
        except Exception as exc:  # noqa: BLE001
            return variant, exc
    return variant, last_exc


async def build_opener_cache(
    *, library: OpenerLibrary, tts: TTS,
) -> BuildReport:
    """Pre-synthesize every variant in ``library`` via ``tts``.

    Variants are mutated in place — on success their ``audio_frames``
    field is populated. Failed variants are left with audio_frames=None
    (orchestrator falls back to text-based TTS for those).

    Synthesis runs in parallel via asyncio.gather. Per-variant exceptions
    are caught and recorded in the BuildReport so a partial failure
    doesn't poison the whole cache.
    """
    import time
    started = time.monotonic()

    all_variants: list[OpenerVariant] = []
    seen: set[str] = set()
    for variants in library._vocabulary.values():
        for v in variants:
            # Deduplicate: identical text used in multiple (kind, sub_ctx)
            # pairs only needs one synthesis. Variants are mutable
            # dataclass instances so populating one populates them all
            # only when they're literally the same object — they're not.
            # Synthesize per-variant for simplicity; a future v2
            # optimization could share frames across same-text variants.
            all_variants.append(v)
            seen.add(v.text)

    log.info(
        "openers.cache.build.started",
        variant_count=len(all_variants),
        unique_texts=len(seen),
    )

    results = await asyncio.gather(
        *[_synthesize_variant(v, tts) for v in all_variants],
        return_exceptions=False,
    )

    report = BuildReport()
    for variant, exc in results:
        if exc is None:
            report.success_count += 1
        else:
            report.failed_variants.append((variant.text, str(exc)[:200]))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    report.total_synthesis_time_ms = elapsed_ms
    log.info(
        "openers.cache.build.completed",
        success_count=report.success_count,
        failed_count=len(report.failed_variants),
        elapsed_ms=elapsed_ms,
    )
    return report
