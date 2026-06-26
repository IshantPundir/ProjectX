"""Cut one candidate span from the recording -> a normalized, A/V-locked 16:9 clip.

The source is a VARIABLE-frame-rate LiveKit egress recording. Re-encoding each
clip to a CONSTANT frame rate (``-vsync cfr``) + resampling audio to its window
(``aresample=async=1``) keeps every clip's audio and video durations equal, so
the re-timing concat in render.py cannot accumulate per-segment A/V drift.

All clips are normalized to identical params (1280x720, 30fps CFR, H.264 yuv420p,
AAC 48k stereo) so render.concat_clips can join them via the concat FILTER.
"""
from __future__ import annotations

import asyncio
import os

TARGET_W, TARGET_H, FPS = 1280, 720, 30
PAD_MS = 150       # lead pad so a beat never starts a hair late
TAIL_PAD_MS = 400  # larger trail pad so the last word always finishes (+ a breath)


def build_cut_cmd(*, recording_path: str, out_path: str,
                  start_ms: int, end_ms: int, offset_ms: int,
                  pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS,
                  overlay_png: str | None = None) -> list[str]:
    """Pure: the ffmpeg argv for one normalized, A/V-duration-locked clip.

    video window = [source_start - offset - pad, source_end - offset + tail_pad].
    Seek (``-ss``) before ``-i`` for speed, but bound the output by DURATION
    (``-t``) not input ``-to`` so the encoded length is deterministic. ``-vsync
    cfr`` (ffmpeg < 5.1; the ``-fps_mode`` alias on 5.1+) forces a constant frame
    rate and ``aresample=async=1:first_pts=0`` keeps audio aligned to the window,
    so audio and video durations stay equal (no progressive desync at concat).
    """
    v_start = max(0, start_ms - offset_ms - pad_ms)
    v_end = max(v_start + 1, end_ms - offset_ms + tail_pad_ms)
    ss = v_start / 1000.0
    dur = (v_end - v_start) / 1000.0
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={FPS}"
    )
    cmd = ["ffmpeg", "-y", "-ss", f"{ss:.3f}", "-i", recording_path]
    if overlay_png:
        # Composite the transparent banner PNG over the normalized video.
        cmd += ["-i", overlay_png,
                "-filter_complex", f"[0:v]{vf}[base];[base][1:v]overlay=0:0[v]",
                "-map", "[v]", "-map", "0:a"]
    else:
        cmd += ["-vf", vf]
    cmd += [
        "-t", f"{dur:.3f}",
        "-vsync", "cfr",
        "-af", "aresample=async=1:first_pts=0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out_path,
    ]
    return cmd


async def cut_clip(*, recording_path: str, out_path: str,
                   start_ms: int, end_ms: int, offset_ms: int,
                   pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS,
                   overlay_png: str | None = None) -> str:
    """Write a normalized, A/V-locked clip for [start_ms, end_ms] (source clock)."""
    cmd = build_cut_cmd(
        recording_path=recording_path, out_path=out_path,
        start_ms=start_ms, end_ms=end_ms, offset_ms=offset_ms,
        pad_ms=pad_ms, tail_pad_ms=tail_pad_ms, overlay_png=overlay_png,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg cut failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
