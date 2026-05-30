# app/modules/vision/detectors.py
"""Pure self-baseline gaze detectors (stdlib only — no cv2/torch/numpy).

Input: a time-ordered list[FrameObservation] (one per sampled frame). Output:
zones, flagged intervals, heatmap, and a transparent 3-tier band. We never map
gaze to absolute screen pixels (spec §16.3) — we measure DEVIATION from each
session's own baseline gaze direction.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class FrameObservation:
    t_ms: int
    faces: int
    yaw: float | None    # radians, primary face; None when unscorable
    pitch: float | None  # radians, + = down
    quality: float       # [0,1] primary-face score; 0 when no scorable face


def _scorable(o: FrameObservation) -> bool:
    return o.yaw is not None and o.pitch is not None and o.quality > 0.0


def estimate_baseline(obs: list[FrameObservation]) -> tuple[float, float]:
    """Baseline gaze ≈ the densest (yaw, pitch) cluster over scorable frames.

    Implemented as the modal bin of a coarse 2° histogram, then the mean of the
    members of that bin (so the value isn't quantised to the bin centre).
    Falls back to (0, 0) when there are no scorable frames.
    """
    pts = [(o.yaw, o.pitch) for o in obs if _scorable(o)]
    if not pts:
        return (0.0, 0.0)
    bin_rad = math.radians(2.0)

    def key(p: tuple[float, float]) -> tuple[int, int]:
        return (round(p[0] / bin_rad), round(p[1] / bin_rad))

    counts = Counter(key(p) for p in pts)
    top = counts.most_common(1)[0][0]
    members = [p for p in pts if key(p) == top]
    by = sum(p[0] for p in members) / len(members)
    bp = sum(p[1] for p in members) / len(members)
    return (by, bp)


def classify_zone(
    yaw: float,
    pitch: float,
    base_yaw: float,
    base_pitch: float,
    *,
    zone_yaw_deg: float,
    zone_pitch_deg: float,
    far_off_deg: float,
) -> str:
    """Coarse zone of a gaze relative to the session baseline."""
    dyaw = math.degrees(yaw - base_yaw)
    dpitch = math.degrees(pitch - base_pitch)
    if abs(dyaw) >= far_off_deg or abs(dpitch) >= far_off_deg:
        return "far_off"
    horiz = abs(dyaw) > zone_yaw_deg
    vert = abs(dpitch) > zone_pitch_deg
    if not horiz and not vert:
        return "center"
    # Pick the dominant axis when both exceed threshold.
    if horiz and (not vert or abs(dyaw) - zone_yaw_deg >= abs(dpitch) - zone_pitch_deg):
        return "right" if dyaw > 0 else "left"
    return "down" if dpitch > 0 else "up"
