# tests/vision/test_analysis_sampler_wiring.py
from app.modules.vision import analysis as an
from app.modules.vision.gaze.base import FaceGaze


class _FakeEstimator:
    def __init__(self):
        self.frames_seen = 0

    def estimate(self, frame_bgr):
        self.frames_seen += 1
        return [FaceGaze(bbox=(0.0, 0.0, 10.0, 10.0), pitch=0.0, yaw=0.0, score=1.0)]


def test_run_analysis_uses_sampler_with_config_budget(monkeypatch):
    captured = {}

    def _fake_sample_frames(video_path, *, target_fps, max_frames, max_width):
        captured["args"] = (video_path, target_fps, max_frames, max_width)
        for i in range(3):
            yield i * 500, object()  # 3 frames at the chosen stride

    monkeypatch.setattr(an, "sample_frames", _fake_sample_frames)
    est = _FakeEstimator()
    result, frames = an.run_analysis(est, local_video_path="/tmp/rec.mp4")

    assert est.frames_seen == 3
    assert frames == 3
    vpath, fps, max_frames, max_width = captured["args"]
    assert vpath == "/tmp/rec.mp4"
    assert fps == 2.0           # vision_config.sample_fps default
    assert max_frames == 2000   # vision_config.max_frames default
    assert max_width == 960     # vision_config.max_frame_width default
