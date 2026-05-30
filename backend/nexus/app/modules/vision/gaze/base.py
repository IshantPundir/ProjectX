# app/modules/vision/gaze/base.py
"""The gaze-estimation seam.

`GazeEstimator` is the ONLY thing the analysis pipeline depends on. The v1
implementation (gaze/mobilegaze.py) wraps MobileGaze (ONNX) with NON-COMMERCIAL
Gaze360 weights (spec §16.8); a clean-weights or MediaPipe estimator implements
the same Protocol and drops in with no downstream change.

Angle convention (pin it — downstream baseline math depends on it):
  pitch: radians, POSITIVE = looking DOWN.
  yaw:   radians, POSITIVE = looking to the CAMERA's right.
  score: detector/landmark confidence in [0, 1].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FaceGaze:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (pixels)
    pitch: float  # radians, + = down
    yaw: float    # radians, + = camera-right
    score: float  # [0, 1]


@runtime_checkable
class GazeEstimator(Protocol):
    def estimate(self, frame_bgr) -> list[FaceGaze]:
        """Return one FaceGaze per detected face in a BGR frame (may be empty)."""
        ...
