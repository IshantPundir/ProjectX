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
    """Synthesize one variant. On success, populates variant.audio_frames
    in place. On failure, leaves audio_frames=None and returns the
    exception. Never raises — caller aggregates into BuildReport."""
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
    except Exception as exc:  # noqa: BLE001
        return variant, exc


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
