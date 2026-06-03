"""Offline TTS for reel narration — the session's voice (Arjun) one-shot to WAV.

Reuses ``app.ai.realtime.build_tts_plugin`` (Sarvam ``bulbul:v3`` by default) so the
reel's framing voice matches the interview. The plugin is a LiveKit streaming
plugin; ``.synthesize()`` works offline when wrapped in
``livekit.agents.utils.http_context.open()`` (it provides the aiohttp session the
plugin would otherwise get from a job context).

All heavy imports are lazy so this module imports cleanly in the lean image; it is
only CALLED in the vision image (which carries the TTS plugin + key).
"""
from __future__ import annotations


async def synthesize_to_wav(text: str, out_path: str) -> tuple[str, int] | None:
    """Synthesize ``text`` in Arjun's voice to a mono WAV. Returns (path, duration_ms).

    Returns None for empty text or empty audio (caller falls back to a silent card).
    """
    if not text or not text.strip():
        return None

    import wave

    from livekit.agents.utils import http_context

    from app.ai.realtime import build_tts_plugin

    tts = build_tts_plugin()
    frames: list[bytes] = []
    sample_rate = 22_050
    async with http_context.open():
        async for ev in tts.synthesize(text):
            frame = getattr(ev, "frame", None)
            if frame is not None:
                frames.append(bytes(frame.data))
                sample_rate = frame.sample_rate

    pcm = b"".join(frames)
    if not pcm:
        return None
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    duration_ms = int(len(pcm) / 2 / sample_rate * 1000)
    return out_path, duration_ms
