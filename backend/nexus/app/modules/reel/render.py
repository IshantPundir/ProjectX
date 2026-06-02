"""Join normalized clips into one MP4 via ffmpeg's concat demuxer.

All inputs MUST share codec/params (clips.cut_clip guarantees this), so concat
is a stream copy -- fast and glitch-free. Grows later to interleave card+TTS beats.
"""
from __future__ import annotations

import asyncio
import os


def build_concat_file(clip_paths: list[str]) -> str:
    """Pure: the concat-demuxer list file body (one `file '<abspath>'` per line)."""
    return "".join(f"file '{os.path.abspath(p)}'\n" for p in clip_paths)


async def concat_clips(clip_paths: list[str], out_path: str) -> str:
    if not clip_paths:
        raise ValueError("concat_clips: no clips")
    list_path = out_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        f.write(build_concat_file(clip_paths))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", "-movflags", "+faststart", out_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): "
                           f"{stderr.decode('utf-8', 'replace')[-800:]}")
    return out_path
