from app.modules.vision.gaze.base import FaceGaze, GazeEstimator


class _FakeEstimator:
    def estimate(self, frame_bgr) -> list[FaceGaze]:
        return [FaceGaze(bbox=(0.0, 0.0, 10.0, 10.0), pitch=0.1, yaw=-0.2, score=0.9)]


def test_facegaze_fields():
    g = FaceGaze(bbox=(1, 2, 3, 4), pitch=0.0, yaw=0.0, score=1.0)
    assert g.bbox == (1, 2, 3, 4)
    assert g.pitch == 0.0 and g.yaw == 0.0 and g.score == 1.0


def test_fake_satisfies_protocol():
    est: GazeEstimator = _FakeEstimator()  # structural typing — must type-check + run
    out = est.estimate(object())
    assert len(out) == 1 and isinstance(out[0], FaceGaze)
