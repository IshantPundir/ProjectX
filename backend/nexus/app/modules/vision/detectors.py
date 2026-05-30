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
        reversals = sum(1 for a, b in zip(signs, signs[1:], strict=False) if a != b)
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
                out.append(
                    Interval(run_start, last_t, "multiple_faces", confidence=0.7, max_faces=peak)
                )
            run_start = None
    if run_start is not None and last_t is not None and last_t - run_start >= min_ms:
        out.append(
            Interval(run_start, last_t, "multiple_faces", confidence=0.7, max_faces=peak)
        )
    return out


@dataclass(frozen=True)
class AnalysisResult:
    risk_band: str
    detector_summary: dict
    gaze_heatmap: dict
    flagged_intervals: list[dict]
    gaze_signal_quality: str
    unscorable_pct: float


def _build_heatmap(obs, base, thresholds, *, grid=5) -> dict:
    """5x5 yaw×pitch occupancy (relative to baseline) + off-screen-% timeline.

    Cell extent = ±far_off_deg across the grid; out-of-range clamps to edge.
    Timeline buckets the session into 30 slots of off-center fraction.
    """
    span = thresholds["far_off_deg"]
    cells = [[0 for _ in range(grid)] for _ in range(grid)]
    scorable = 0
    for o in obs:
        if not _scorable(o):
            continue
        scorable += 1
        dx = math.degrees(o.yaw - base[0])
        dy = math.degrees(o.pitch - base[1])
        cx = min(grid - 1, max(0, int((dx + span) / (2 * span) * grid)))
        cy = min(grid - 1, max(0, int((dy + span) / (2 * span) * grid)))
        cells[cy][cx] += 1

    slots = 30
    if obs:
        t0, t1 = obs[0].t_ms, max(o.t_ms for o in obs)
    else:
        t0 = t1 = 0
    span_ms = max(1, t1 - t0)
    buckets = [[0, 0] for _ in range(slots)]  # [off_count, total]
    for o in obs:
        if not _scorable(o):
            continue
        idx = min(slots - 1, int((o.t_ms - t0) / span_ms * slots))
        z = classify_zone(o.yaw, o.pitch, base[0], base[1], **thresholds)
        buckets[idx][1] += 1
        if z != "center":
            buckets[idx][0] += 1
    timeline = [round(b[0] / b[1], 3) if b[1] else 0.0 for b in buckets]
    return {"grid": cells, "scorable_frames": scorable, "off_screen_timeline": timeline}


def _signal_quality(unscorable_pct: float) -> str:
    if unscorable_pct > 0.6:
        return "unscorable"
    if unscorable_pct > 0.25:
        return "low-light"
    return "good"


def analyze_observations(
    obs: list[FrameObservation],
    *,
    zone_yaw_deg: float,
    zone_pitch_deg: float,
    far_off_deg: float,
    off_screen_min_ms: int,
    down_glance_min_ms: int,
    down_glance_max_ms: int,
    reading_window_ms: int,
    reading_min_reversals: int,
    multi_face_min_ms: int,
    band_high_off_screen_pct: float,
    band_medium_off_screen_pct: float,
    band_high_down_glances: int,
    max_unscorable_pct: float,
) -> AnalysisResult:
    thresholds = dict(
        zone_yaw_deg=zone_yaw_deg, zone_pitch_deg=zone_pitch_deg, far_off_deg=far_off_deg
    )
    total = len(obs)
    scorable = [o for o in obs if _scorable(o)]
    unscorable_pct = round(1 - (len(scorable) / total), 3) if total else 1.0

    base = estimate_baseline(obs)

    off = detect_off_screen_intervals(obs, base, min_ms=off_screen_min_ms, thresholds=thresholds)
    downs = detect_down_glances(
        obs, base, min_ms=down_glance_min_ms, max_ms=down_glance_max_ms, thresholds=thresholds
    )
    reads = detect_reading_sweeps(
        obs, base, window_ms=reading_window_ms, min_reversals=reading_min_reversals,
        thresholds=thresholds
    )
    faces = detect_multi_face_intervals(obs, min_ms=multi_face_min_ms)

    # Off-screen % over scorable frames.
    off_frames = sum(
        1 for o in scorable
        if classify_zone(o.yaw, o.pitch, base[0], base[1], **thresholds) != "center"
    )
    off_pct = round(off_frames / len(scorable), 3) if scorable else 0.0
    max_faces = max((o.faces for o in obs), default=0)

    intervals = sorted(off + downs + reads + faces, key=lambda i: i.start_ms)
    flagged = [
        {"start_ms": i.start_ms, "end_ms": i.end_ms, "kind": i.kind, "confidence": i.confidence}
        for i in intervals
    ]

    summary = {
        "off_screen_pct": off_pct,
        "down_glance_count": len(downs),
        "reading_sweep_intervals": len(reads),
        "max_faces": max_faces,
        "multi_face_intervals": [
            {"start_ms": i.start_ms, "end_ms": i.end_ms, "max_faces": i.max_faces}
            for i in faces
        ],
    }

    # --- Transparent 3-tier band (spec §16.5) ---
    # Multi-face contributes to "high" only when SUSTAINED (a debounced
    # `multi_face_intervals` entry), never on the raw single-frame peak
    # `max_faces` — a one-frame spurious second-face detection must not flip a
    # session to high. `max_faces` is kept in the summary as informational.
    if unscorable_pct > max_unscorable_pct:
        band = "insufficient_data"
    elif (off_pct >= band_high_off_screen_pct or len(faces) >= 1
          or len(downs) >= band_high_down_glances):
        band = "high"
    elif off_pct >= band_medium_off_screen_pct or len(reads) >= 1 or len(downs) >= 3:
        band = "medium"
    else:
        band = "low"

    return AnalysisResult(
        risk_band=band,
        detector_summary=summary,
        gaze_heatmap=_build_heatmap(obs, base, thresholds),
        flagged_intervals=flagged,
        gaze_signal_quality=_signal_quality(unscorable_pct),
        unscorable_pct=unscorable_pct,
    )
