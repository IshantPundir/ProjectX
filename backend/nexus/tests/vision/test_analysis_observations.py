# tests/vision/test_analysis_observations.py
from app.modules.vision.analysis import observations_from_estimates
from app.modules.vision.gaze.base import FaceGaze


def test_picks_largest_face_as_primary():
    frames = [
        (0, [FaceGaze((0, 0, 10, 10), 0.1, 0.2, 0.8),
             FaceGaze((0, 0, 40, 40), 0.3, -0.1, 0.9)]),   # larger bbox wins
        (200, []),                                          # no face → unscorable
    ]
    obs = observations_from_estimates(frames)
    assert len(obs) == 2
    assert obs[0].faces == 2
    assert obs[0].pitch == 0.3 and obs[0].yaw == -0.1  # from the larger face
    assert obs[1].faces == 0 and obs[1].yaw is None and obs[1].quality == 0.0
