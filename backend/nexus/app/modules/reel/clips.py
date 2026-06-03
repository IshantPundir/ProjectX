"""Cut one candidate span from the recording -> a normalized, captioned 16:9 clip.

Normalization to identical params (1280x720, 30fps, H.264+AAC, SAR 1:1) lets
render.concat_clips use the fast concat demuxer with no re-encode at join.
"""
from __future__ import annotations

import asyncio
import os

from app.modules.reel.captions import build_ass

TARGET_W, TARGET_H, FPS = 1280, 720, 30
PAD_MS = 150       # lead pad so a beat never starts a hair late
TAIL_PAD_MS = 400  # larger trail pad so the last word always finishes (+ a breath)


def build_cut_cmd(*, recording_path: str, ass_path: str | None, out_path: str,
                  start_ms: int, end_ms: int, offset_ms: int,
                  pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS) -> list[str]:
    """Pure: the ffmpeg argv for one normalized clip.

    video window = [source_start - offset - pad, source_end - offset + tail_pad].
    The trail pad is larger than the lead pad so the final word is never clipped.
    Seek before -i for speed. If ``ass_path`` is given, the subtitles filter burns
    the clip-relative .ass; pass None to render the clip without captions.
    """
    v_start = max(0, start_ms - offset_ms - pad_ms)
    v_end = max(v_start + 1, end_ms - offset_ms + tail_pad_ms)
    ss = v_start / 1000.0
    to = v_end / 1000.0
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={FPS}"
    )
    if ass_path:
        vf += f",subtitles='{ass_path}'"
    return [
        "ffmpeg", "-y",
        "-ss", f"{ss:.3f}", "-to", f"{to:.3f}", "-i", recording_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out_path,
    ]


async def cut_clip(*, recording_path: str, out_path: str,
                   words: list[dict] | None = None,
                   start_ms: int, end_ms: int, offset_ms: int,
                   pad_ms: int = PAD_MS, tail_pad_ms: int = TAIL_PAD_MS) -> str:
    """Write a normalized clip for [start_ms, end_ms] (source clock).

    When ``words`` is provided, burn clip-relative captions; otherwise render
    the clip without captions (captions deferred until offset/anchor is proven).
    """
    ass_path: str | None = None
    if words:
        ass_path = out_path + ".ass"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(words, clip_start_ms=start_ms))
    cmd = build_cut_cmd(
        recording_path=recording_path, ass_path=ass_path, out_path=out_path,
        start_ms=start_ms, end_ms=end_ms, offset_ms=offset_ms,
        pad_ms=pad_ms, tail_pad_ms=tail_pad_ms,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg cut failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
