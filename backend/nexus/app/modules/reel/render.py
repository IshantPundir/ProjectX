"""Assemble the reel: render each EDL beat to a normalized segment, then concat.

Cards (title/match/point/outro) become a still image under Arjun narration; clips
(experience/clip) are cut from the recording. Every segment is normalized to
identical codec/params (1280x720, 30fps CFR, H.264 yuv420p, AAC 48k stereo) AND
A/V-duration-locked, so the re-timing concat FILTER joins them with re-encode --
which re-bases every segment's timestamps and so cannot accumulate the
per-segment A/V differences a stream-copy concat would.

Imports of cards/tts/clips are top-level but import-light (Pillow / livekit
/ ffmpeg are all lazy or shelled out), so this stays importable in the lean image;
``render_reel`` is only CALLED in the vision image.
"""
from __future__ import annotations

import asyncio
import os

from app.modules.reel import cards, clips, tts

# Render-side minimum card hold times (s) — a card lasts max(floor, narration+tail).
_CARD_FLOOR_S = {"point": 3.0, "outro": 4.0}
_NARRATION_TAIL_S = 0.5


def build_card_segment_cmd(*, image_path: str, out_path: str, duration_ms: int,
                           audio_path: str | None = None,
                           w: int = cards.CARD_W, h: int = cards.CARD_H,
                           fps: int = 30) -> list[str]:
    """Pure: ffmpeg argv turning a still card (+ optional narration) into a segment.

    Output params match ``clips.cut_clip`` so the concat filter graph is uniform.
    A/V are duration-locked: ``-t {dur}`` bounds the video, and when narration is
    present the audio is padded with silence (``apad``) so audio==video even when
    the TTS is shorter than the card hold (otherwise the short audio would shift
    every following segment). Without narration, a silent stereo source already
    matches ``-t``. ``-vsync cfr`` matches the clips' constant frame rate.
    """
    dur = max(0.1, duration_ms / 1000.0)
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", image_path]
    af: list[str] = []
    if audio_path:
        cmd += ["-i", audio_path]
        af = ["-af", "apad"]   # pad short narration with silence to the full -t
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += [
        "-map", "0:v", "-map", "1:a", "-t", f"{dur:.3f}",
        "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"),
        "-vsync", "cfr",
        *af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", out_path,
    ]
    return cmd


def build_concat_cmd(clip_paths: list[str], out_path: str) -> list[str]:
    """Pure: ffmpeg argv that joins segments via the concat FILTER (re-encode).

    Unlike the concat demuxer + ``-c copy``, the concat filter re-bases each
    segment's timestamps into one continuous stream, so per-segment A/V
    differences cannot accumulate into progressive desync. All segments share
    identical params (see clips/card builders), so the filter graph is valid.
    """
    if not clip_paths:
        raise ValueError("build_concat_cmd: no clips")
    cmd: list[str] = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", p]
    n = len(clip_paths)
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filtergraph = f"{streams}concat=n={n}:v=1:a=1[v][a]"
    cmd += [
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", out_path,
    ]
    return cmd


async def card_segment(*, image_path: str, out_path: str, duration_ms: int,
                       audio_path: str | None = None) -> str:
    cmd = build_card_segment_cmd(image_path=image_path, out_path=out_path,
                                 duration_ms=duration_ms, audio_path=audio_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg card segment failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path


_CARD_KINDS = ("point", "outro")


async def probe_duration_ms(path: str) -> int:
    """Exact media duration (ms) via ffprobe — for accurate chapter offsets."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    try:
        return int(float(out.decode().strip()) * 1000)
    except (ValueError, AttributeError):
        return 0


def first_point_index(beats: list) -> int | None:
    """Index of the first ``point`` beat (gets the identity subtitle), or None."""
    for i, b in enumerate(beats):
        if b.kind == "point":
            return i
    return None


def _chapter_label(beat) -> str:
    if beat.on_screen_text:
        return beat.on_screen_text.lstrip("★ ").strip()[:60]
    return {"clip": "Candidate", "experience": "Candidate"}.get(beat.kind, beat.kind.title())


def _clip_to_video(beat, offset_ms: int) -> tuple[int, int]:
    """Map a clip beat's words -> (video_start, video_end) on the recording clock.

    Each word carries its own ``turn_start_ms`` (session-relative) + turn-relative
    ms; the gen-3 video map is ``video_ms = turn_start_ms + rel_ms + offset_ms``,
    so a multi-turn clip is one contiguous cut on the recording's clock. ``words``
    is still the source of truth for clip TIMING (first/last word -> the cut
    window); only the burned-caption text was removed.
    """
    first, last = beat.words[0], beat.words[-1]
    video_start = int(first["turn_start_ms"]) + int(first["rel_start_ms"]) + offset_ms
    video_end = int(last["turn_start_ms"]) + int(last["rel_end_ms"]) + offset_ms
    return video_start, video_end


async def render_reel(*, beats: list, recording_path: str, offset_ms: int,
                      tmp_dir: str, out_path: str, tts_enabled: bool = True,
                      identity_tag: str | None = None
                      ) -> tuple[str, list[dict]]:
    """Render a validated EDL into one MP4 + chapter metadata.

    Card+narration beats are interleaved with candidate clips. ``offset_ms`` maps
    engine session ms to the video clock (``video_ms = session_ms + offset_ms``;
    see timing.py). Returns ``(out_path, chapters)`` where chapters =
    ``[{kind, label, start_ms}]`` at the exact (ffprobe-measured) offset of each
    rendered segment.
    """
    rendered: list[tuple[str, object]] = []   # (segment_path, beat)
    subtitle_idx = first_point_index(beats)
    for i, b in enumerate(beats):
        seg = os.path.join(tmp_dir, f"seg_{i:02d}.mp4")
        if b.kind in _CARD_KINDS:
            png = os.path.join(tmp_dir, f"card_{i:02d}.png")
            cards.render_card(kind=b.kind, out_path=png,
                              on_screen_text=b.on_screen_text or "",
                              subtitle=identity_tag if i == subtitle_idx else None)
            audio_path, audio_dur = None, 0
            if tts_enabled and b.narration_text:
                res = await tts.synthesize_to_wav(
                    b.narration_text, os.path.join(tmp_dir, f"narr_{i:02d}.wav"))
                if res:
                    audio_path, audio_dur = res
            floor_ms = int(_CARD_FLOOR_S.get(b.kind, 3.0) * 1000)
            duration_ms = (max(floor_ms, audio_dur + int(_NARRATION_TAIL_S * 1000))
                           if audio_dur else max(floor_ms, b.duration_ms))
            await card_segment(image_path=png, out_path=seg,
                               duration_ms=duration_ms, audio_path=audio_path)
            rendered.append((seg, b))
        else:  # clip / experience
            video_start, video_end = _clip_to_video(b, offset_ms)
            await clips.cut_clip(
                recording_path=recording_path, out_path=seg,
                start_ms=video_start, end_ms=video_end, offset_ms=0)
            rendered.append((seg, b))

    if not rendered:
        raise RuntimeError("render_reel: no renderable segments")

    chapters: list[dict] = []
    cursor = 0
    for seg, beat in rendered:
        chapters.append({"kind": beat.kind, "label": _chapter_label(beat),
                         "start_ms": cursor})
        cursor += await probe_duration_ms(seg)

    await concat_clips([s for s, _ in rendered], out_path)
    return out_path, chapters


async def concat_clips(clip_paths: list[str], out_path: str) -> str:
    if not clip_paths:
        raise ValueError("concat_clips: no clips")
    cmd = build_concat_cmd(clip_paths, out_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
