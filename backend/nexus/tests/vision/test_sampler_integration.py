# tests/vision/test_sampler_integration.py
import os
import shutil
import subprocess
import tempfile

import pytest

from app.modules.vision.sampler import sample_frames

pytestmark = [
    pytest.mark.vision_integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available (lean image)"),
]


def _make_clip(path: str, *, seconds: int, w: int, h: int, src_fps: int) -> None:
    # Synthetic test pattern, no audio.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={src_fps}:duration={seconds}",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


def test_sampler_bounds_frames_and_downscales():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "clip.mp4")
        _make_clip(clip, seconds=10, w=1280, h=720, src_fps=30)
        frames = list(sample_frames(clip, target_fps=2.0, max_frames=2000, max_width=960))

    # 10s @ 2fps ~= 20 frames (budget not binding).
    assert 18 <= len(frames) <= 22
    t0, f0 = frames[0]
    assert f0.shape == (540, 960, 3)          # 1280x720 -> 960x540
    times = [t for t, _ in frames]
    assert times == sorted(times)             # monotonic
    assert times[0] == 0


def test_sampler_budget_degrades_long_clip():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "long.mp4")
        _make_clip(clip, seconds=30, w=640, h=480, src_fps=15)
        # Budget 30 frames over 30s forces ~1 fps (below target 2 fps).
        frames = list(sample_frames(clip, target_fps=2.0, max_frames=30, max_width=960))

    assert len(frames) <= 32                  # bounded by the budget, not 60
