# app/modules/vision/analysis.py
"""Offline analysis orchestration: sample frames from a local recording →
GazeEstimator → FrameObservations → analyze_observations. The actor owns the
async R2 download; this module is sync + CPU-bound. The heavy frame-decode dep
(cv2) imports lazily; `observations_from_estimates` is pure + unit-tested.
"""
from __future__ import annotations

import structlog

from app.modules.vision.config import vision_config
from app.modules.vision.detectors import AnalysisResult, FrameObservation, analyze_observations
from app.modules.vision.gaze.base import FaceGaze, GazeEstimator

log = structlog.get_logger("vision.analysis")


def _bbox_area(g: FaceGaze) -> float:
    x1, y1, x2, y2 = g.bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def observations_from_estimates(
    frames: list[tuple[int, list[FaceGaze]]],
) -> list[FrameObservation]:
    """Map [(t_ms, [FaceGaze, ...]), ...] → [FrameObservation, ...].

    Primary face = largest bbox. No face → unscorable observation.
    """
    obs: list[FrameObservation] = []
    for t_ms, faces in frames:
        if not faces:
            obs.append(FrameObservation(t_ms=t_ms, faces=0, yaw=None, pitch=None, quality=0.0))
            continue
        primary = max(faces, key=_bbox_area)
        obs.append(FrameObservation(
            t_ms=t_ms, faces=len(faces),
            yaw=primary.yaw, pitch=primary.pitch, quality=primary.score,
        ))
    return obs


def _sample_frames(video_path: str, fps: float):
    """Yield (t_ms, frame_bgr) sampling `video_path` at ~fps. Lazy cv2 import."""
    import cv2  # noqa: PLC0415

    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / fps)))
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                t_ms = int((idx / src_fps) * 1000)
                yield t_ms, frame
            idx += 1
    finally:
        cap.release()


def run_analysis(estimator: GazeEstimator, *, local_video_path: str) -> tuple[AnalysisResult, int]:
    """Sample frames from a LOCAL file, estimate, analyze. Returns (result, frames).

    The actor (Task 10) does the async R2 download to a temp path, then calls
    this with that path — keeping all async I/O in the actor and this function
    pure-sync + CPU-bound.
    """
    cfg = vision_config
    frames: list[tuple[int, list[FaceGaze]]] = []
    for t_ms, frame in _sample_frames(local_video_path, cfg.sample_fps):
        frames.append((t_ms, estimator.estimate(frame)))

    obs = observations_from_estimates(frames)
    result = analyze_observations(
        obs,
        zone_yaw_deg=cfg.zone_yaw_deg, zone_pitch_deg=cfg.zone_pitch_deg,
        far_off_deg=cfg.far_off_deg,
        off_screen_min_ms=cfg.off_screen_min_ms,
        down_glance_min_ms=cfg.down_glance_min_ms, down_glance_max_ms=cfg.down_glance_max_ms,
        reading_window_ms=cfg.reading_window_ms, reading_min_reversals=cfg.reading_min_reversals,
        multi_face_min_ms=cfg.multi_face_min_ms,
        band_high_off_screen_pct=cfg.band_high_off_screen_pct,
        band_medium_off_screen_pct=cfg.band_medium_off_screen_pct,
        band_high_down_glances=cfg.band_high_down_glances,
        max_unscorable_pct=cfg.max_unscorable_pct,
    )
    return result, len(obs)
