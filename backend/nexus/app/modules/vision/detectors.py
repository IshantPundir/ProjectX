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


@dataclass(frozen=True)
class Interval:
    start_ms: int
    end_ms: int
    kind: str
    confidence: float = 0.6
    max_faces: int = 1

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def _zone(o: FrameObservation, base, thresholds) -> str | None:
    if not _scorable(o):
        return None
    return classify_zone(o.yaw, o.pitch, base[0], base[1], **thresholds)


def _runs(obs, base, thresholds, predicate):
    """Yield (start_ms, end_ms, members) for maximal runs where predicate(zone) holds.

    A run ends at the first frame failing the predicate; end_ms is that frame's
    t_ms (so a single trailing centered frame closes the interval cleanly).
    Unscorable frames neither extend nor break a run — they are skipped.
    """
    run_start = None
    last_t = None
    members = 0
    for o in obs:
        z = _zone(o, base, thresholds)
        if z is None:
            continue
        if predicate(z):
            if run_start is None:
                run_start = o.t_ms
                members = 0
            members += 1
            last_t = o.t_ms
        else:
            if run_start is not None:
                yield (run_start, o.t_ms, members)
                run_start = None
        prev_close = o.t_ms  # noqa: F841
    if run_start is not None and last_t is not None:
        yield (run_start, last_t, members)


def detect_off_screen_intervals(obs, base, *, min_ms, thresholds) -> list[Interval]:
    off = lambda z: z != "center"  # noqa: E731
    out = []
    for start, end, _ in _runs(obs, base, thresholds, off):
        if end - start >= min_ms:
            out.append(Interval(start, end, "off_screen_sustained", confidence=0.65))
    return out


def detect_down_glances(obs, base, *, min_ms, max_ms, thresholds) -> list[Interval]:
    is_down = lambda z: z == "down"  # noqa: E731
    out = []
    for start, end, _ in _runs(obs, base, thresholds, is_down):
        dur = end - start
        if min_ms <= dur <= max_ms:
            out.append(Interval(start, end, "down_glance", confidence=0.6))
    return out


def detect_reading_sweeps(obs, base, *, window_ms, min_reversals, thresholds) -> list[Interval]:
    """Flag windows with >= min_reversals left<->right horizontal direction changes.

    Reading a second screen/notes shows rhythmic horizontal scanning; idle
    glancing does not. We slide non-overlapping windows of window_ms and count
    sign changes of the horizontal deviation among scorable frames.
    """
    scor = [o for o in obs if _scorable(o)]
    out: list[Interval] = []
    if not scor:
        return out
    i = 0
    n = len(scor)
    while i < n:
        w_start = scor[i].t_ms
        j = i
        signs: list[int] = []
        while j < n and scor[j].t_ms - w_start < window_ms:
            dyaw = math.degrees(scor[j].yaw - base[0])
            if abs(dyaw) > thresholds["zone_yaw_deg"]:
                signs.append(1 if dyaw > 0 else -1)
            j += 1
        reversals = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
        if reversals >= min_reversals:
            out.append(Interval(w_start, scor[j - 1].t_ms, "reading_sweep", confidence=0.55))
        i = j if j > i else i + 1
    return out


def detect_multi_face_intervals(obs, *, min_ms) -> list[Interval]:
    out: list[Interval] = []
    run_start = None
    last_t = None
    peak = 0
    for o in obs:
        if o.faces >= 2:
            if run_start is None:
                run_start = o.t_ms
                peak = 0
            peak = max(peak, o.faces)
            last_t = o.t_ms
        else:
            if run_start is not None and last_t is not None and last_t - run_start >= min_ms:
                out.append(Interval(run_start, last_t, "multiple_faces", confidence=0.7, max_faces=peak))
            run_start = None
    if run_start is not None and last_t is not None and last_t - run_start >= min_ms:
        out.append(Interval(run_start, last_t, "multiple_faces", confidence=0.7, max_faces=peak))
    return out
