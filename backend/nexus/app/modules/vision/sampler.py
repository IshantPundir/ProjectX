# app/modules/vision/sampler.py
"""Frame sampling for vision proctoring — ffmpeg-based, bounded.

Pure functions (effective_fps, scaled_dimensions, build_*_cmd, parse_probe_json)
are import-light and unit-tested in the lean image. `sample_frames` shells out to
ffprobe + ffmpeg (both in Dockerfile.vision) and yields (t_ms, frame_bgr); numpy
imports lazily inside it, mirroring analysis.py discipline.

Design (2026-06-01 perf): replaces the old full-decode OpenCV loop. ffmpeg
decodes keyframe-aware and emits ONLY the sampled, pre-downscaled frames at a
constant effective fps, so index->timestamp is exact and worst-case frame count
is bounded by the budget.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import structlog

log = structlog.get_logger("vision.sampler")


def effective_fps(duration_seconds: float, target_fps: float, max_frames: int) -> float:
    """Sampling fps after applying the frame budget.

    = min(target_fps, max_frames / duration). Long recordings degrade to a wider
    uniform stride instead of being truncated. Unknown/zero duration → target.
    """
    if duration_seconds <= 0 or target_fps <= 0:
        return target_fps
    return min(target_fps, max_frames / duration_seconds)


def scaled_dimensions(src_w: int, src_h: int, max_width: int) -> tuple[int, int]:
    """Output (w, h) preserving aspect, capped to max_width, both forced even.

    Even dims keep ffmpeg's scaler and common pixel formats happy and let us
    compute the exact rawvideo frame byte size up front. No upscaling.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"invalid source dimensions: {src_w}x{src_h}")
    out_w = min(src_w, max_width)
    out_h = round(src_h * out_w / src_w)
    out_w -= out_w % 2
    out_h -= out_h % 2
    return max(2, out_w), max(2, out_h)


def build_ffprobe_cmd(path: str) -> list[str]:
    """ffprobe argv → JSON with the first video stream's width/height + duration."""
    return [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]


def parse_probe_json(raw: str) -> tuple[int, int, float]:
    """(width, height, duration_seconds) from ffprobe JSON. Missing duration → 0.0."""
    doc = json.loads(raw)
    stream = doc["streams"][0]
    w = int(stream["width"])
    h = int(stream["height"])
    dur_raw = doc.get("format", {}).get("duration")
    dur = float(dur_raw) if dur_raw not in (None, "", "N/A") else 0.0
    return w, h, dur


def build_ffmpeg_cmd(path: str, eff_fps: float, out_w: int, out_h: int) -> list[str]:
    """ffmpeg argv → constant-fps, downscaled raw BGR24 frames on stdout."""
    vf = f"fps={eff_fps:.6f},scale={out_w}:{out_h}"
    return [
        "ffmpeg", "-v", "error",
        "-i", path,
        "-vf", vf,
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "pipe:1",
    ]


def sample_frames(
    video_path: str, *, target_fps: float, max_frames: int, max_width: int
) -> Iterator[tuple[int, object]]:
    """Yield (t_ms, frame_bgr ndarray) sampled at the budget-bounded effective fps.

    ffprobe for dims+duration → compute eff_fps + output dims → ffmpeg pipe →
    read fixed-size frames. Raises ValueError on a non-positive effective fps
    (e.g. max_frames misconfigured to 0). Raises RuntimeError if ffmpeg exits
    non-zero while streaming to completion. Yielded frames are writable copies,
    safe for in-place cv2 ops in the gaze estimator.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    probe = subprocess.run(
        build_ffprobe_cmd(video_path), capture_output=True, text=True, check=True
    )
    src_w, src_h, duration = parse_probe_json(probe.stdout)
    eff = effective_fps(duration, target_fps, max_frames)
    if eff <= 0:
        raise ValueError(
            f"non-positive effective fps ({eff}); check target_fps/max_frames"
        )
    out_w, out_h = scaled_dimensions(src_w, src_h, max_width)
    frame_bytes = out_w * out_h * 3

    log.info(
        "vision.sampler.start", duration_s=round(duration, 1), eff_fps=round(eff, 4),
        out_w=out_w, out_h=out_h, budget=max_frames,
    )

    # stderr → a regular temp file, NOT a pipe. A pipe we only drain after wait()
    # can fill its OS buffer on a verbose-failing ffmpeg and deadlock (we block in
    # wait() while ffmpeg blocks writing stderr). A file never blocks the writer.
    stderr_file = tempfile.TemporaryFile()  # noqa: SIM115
    proc = subprocess.Popen(
        build_ffmpeg_cmd(video_path, eff, out_w, out_h),
        stdout=subprocess.PIPE, stderr=stderr_file,
    )
    completed = False
    i = 0
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                completed = True
                break
            # .copy(): frombuffer is a read-only view over an immutable bytes
            # object; the gaze estimator mutates/preprocesses frames in place.
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3).copy()
            yield int(round(i / eff * 1000)), frame
            i += 1
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if not completed:
            # Consumer abandoned the generator early — stop ffmpeg cleanly rather
            # than let it die on SIGPIPE and surface as a spurious failure.
            proc.terminate()
        ret = proc.wait()
        if completed and ret != 0:
            stderr_file.seek(0)
            err = stderr_file.read().decode("utf-8", "replace")[:500]
            stderr_file.close()
            log.error("vision.sampler.ffmpeg_failed", returncode=ret, stderr=err)
            raise RuntimeError(f"ffmpeg exited {ret}: {err}")
        stderr_file.close()
