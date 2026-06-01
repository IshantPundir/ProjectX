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
from app.modules.vision.sampler import sample_frames

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


# Severity ordering for proctoring flags (higher = more serious). Decides which
# flags earn a timeline thumbnail when there are more than top_n.
_FLAG_SEVERITY: dict[str, int] = {
    "multiple_faces": 3,
    "off_screen_sustained": 2,
    "reading_sweep": 1,
    "down_glance": 0,
}


def select_flag_targets(flagged_intervals: list[dict], *, top_n: int) -> list[dict]:
    """Return the top-N most serious flags (by severity, then confidence, then
    earliest start), each as the original interval dict. Pure — no I/O."""
    ranked = sorted(
        flagged_intervals,
        key=lambda f: (
            _FLAG_SEVERITY.get(f.get("kind", ""), 0),
            float(f.get("confidence") or 0.0),
            -int(f.get("start_ms") or 0),
        ),
        reverse=True,
    )
    return ranked[: max(0, top_n)]


def _target_frame_index(t_ms: int, src_fps: float, frame_count: int) -> int:
    """Frame index nearest a timestamp, clamped to [0, frame_count-1] (no upper
    clamp when frame_count is 0/unknown). Pure — no cv2."""
    idx = int(round((t_ms / 1000.0) * src_fps))
    if frame_count:
        idx = min(idx, frame_count - 1)
    return max(idx, 0)


def grab_thumbnails(
    video_path: str, targets_ms: list[int], *, width: int, webp_quality: int
) -> dict[int, bytes]:
    """For each target timestamp, seek to the nearest frame, resize to ``width``
    (preserving aspect), encode WebP. Returns {target_ms: webp_bytes}; targets
    whose frame cannot be read are omitted. Reuses the recording the proctoring
    pass already downloaded — one seek per target, no full re-decode."""
    import cv2  # noqa: PLC0415  — lazy: heavy native dep, vision image only

    cap = cv2.VideoCapture(video_path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out: dict[int, bytes] = {}
    try:
        for t_ms in targets_ms:
            idx = _target_frame_index(t_ms, src_fps, frame_count)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            if w > width and w > 0:
                new_h = max(1, int(round(h * (width / w))))
                frame = cv2.resize(frame, (width, new_h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".webp", frame, [cv2.IMWRITE_WEBP_QUALITY, webp_quality])
            if ok:
                out[t_ms] = bytes(buf.tobytes())
    finally:
        cap.release()
    return out


def run_analysis(estimator: GazeEstimator, *, local_video_path: str) -> tuple[AnalysisResult, int]:
    """Sample frames from a LOCAL file, estimate, analyze. Returns (result, frames).

    The actor (Task 10) does the async R2 download to a temp path, then calls
    this with that path — keeping all async I/O in the actor and this function
    pure-sync + CPU-bound.
    """
    cfg = vision_config
    frames: list[tuple[int, list[FaceGaze]]] = []
    for t_ms, frame in sample_frames(
        local_video_path,
        target_fps=cfg.sample_fps,
        max_frames=cfg.max_frames,
        max_width=cfg.max_frame_width,
    ):
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
