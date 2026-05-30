# Vision Proctoring — Server Plane v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the post-session vision-proctoring pipeline — offline MobileGaze (ONNX) gaze + multi-face analysis on the R2 recording, producing a `session_proctoring_analysis` row, surfaced as a "Proctoring & Integrity" panel on the recruiter report page (evidence for human review, never auto-rejects).

**Architecture:** A new `app/modules/vision/` module. A Dramatiq actor on a dedicated `vision` queue (run by a new `nexus-vision-worker` ONNX image) downloads the recording, samples frames with ffmpeg, runs a MobileGaze ONNX gaze estimator behind a swappable `GazeEstimator` interface, derives self-baseline gaze zones + reading/down-glance/off-screen detectors + multi-face intervals via pure functions, computes a transparent 3-tier band, and persists features only (no frames) to a new tenant-scoped table. The recruiter report page reads it via a sibling endpoint and renders a right-sidebar panel with jump-to-timestamp into the existing recording player.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async (asyncpg), Alembic, Dramatiq+Redis, **MobileGaze** (`yakhyo/gaze-estimation`, MIT) via `onnxruntime` + `uniface` RetinaFace + opencv + ffmpeg (vision worker only — no torch), boto3 (R2), Next.js 16 + TanStack Query + `components/px/` primitives.

**Spec:** `docs/superpowers/specs/2026-05-29-vision-proctoring-design.md` §16 (authoritative) + §15b (model decision).

**Key constraints (do not violate):**
- The gaze model (MobileGaze `resnet34_gaze.onnx`, Gaze360-trained) is **non-commercial / dev-POC only**. It sits behind `GazeEstimator` so clean weights swap in later. Do NOT close the "replace NC weights before GA" item.
- **Heavy imports (onnxruntime / cv2 / uniface / numpy) are LAZY** — done inside function bodies, never at module top level — so the lean `nexus` API image can import `vision/actors.py` to call `.send()` without those deps installed (mirrors `app/ai/realtime.py`).
- New tenant-scoped table MUST carry the canonical `tenant_isolation` + `service_bypass` RLS pair and be registered in `_TENANT_SCOPED_TABLES` (`app/main.py`).
- `detectors.py` must import only stdlib (no cv2/torch/numpy) so its unit tests run in the standard test image.
- Backend commands run in Docker: `docker compose run --rm nexus <cmd>` (e.g. `pytest`). Frontend in `frontend/app`: `npm run test`.

---

## File Structure

**Backend — new `backend/nexus/app/modules/vision/`:**
- `__init__.py` — public API (`__all__`): `analyze_session_proctoring`, `get_session_proctoring_analysis`, `ProctoringAnalysisRead`, `SessionProctoringAnalysis`.
- `gaze/base.py` — `FaceGaze` dataclass + `GazeEstimator` Protocol (the swap seam).
- `gaze/mobilegaze.py` — `MobileGazeEstimator` (MobileGaze ONNX; lazy `onnxruntime`/`cv2`/`uniface` imports).
- `detectors.py` — pure functions (stdlib only): baseline, zone classify, off-screen / reading-sweep / down-glance / multi-face detectors, heatmap, band, `analyze_observations`.
- `analysis.py` — `run_analysis(...)`: download → ffmpeg-sample → estimate → `analyze_observations`. Lazy heavy imports.
- `models.py` — `SessionProctoringAnalysis` ORM.
- `schemas.py` — `ProctoringAnalysisRead` Pydantic response model.
- `service.py` — `get_session_proctoring_analysis(db, session_id, tenant_id)`.
- `actors.py` — `analyze_session_proctoring(session_id, tenant_id)` Dramatiq actor (light top-level imports).
- `config.py` — `VisionConfig` reading the new `Settings` fields.

**Backend — modified:**
- `app/config.py` — new `Settings` fields (gaze weights/arch/fps/thresholds/ttl).
- `app/storage/base.py` + `app/storage/s3.py` — add `download_to_path`.
- `app/main.py` — register `session_proctoring_analysis` in `_TENANT_SCOPED_TABLES`.
- `app/worker.py` — import the vision actor module.
- `app/modules/session/recording.py` — enqueue trigger after reconcile→ready.
- `app/modules/reporting/router.py` — `GET /api/reports/session/{id}/proctoring`.
- `migrations/versions/0051_session_proctoring_analysis.py` — new table + RLS + rollback.
- `pyproject.toml` (vision extras) + `Dockerfile.vision` + `docker-compose.yml` (`nexus-vision-worker`).

**Frontend — `frontend/app`:**
- `lib/api/reports.ts` — `ProctoringAnalysis` types + `reportsApi.getProctoring`.
- `lib/hooks/use-session-proctoring.ts` — new hook.
- `components/dashboard/reports/ProctoringIntegrityPanel.tsx` — sidebar panel + expand dialog + heatmap.
- `components/dashboard/reports/ReportView.tsx` — sidebar wiring + `onSeek` lift.
- `components/dashboard/reports/SessionPlayback.tsx` — imperative seek handle.

**Docs:**
- `docs/security/2026-05-30-vision-proctoring-dpia.md` — DPIA + bias-review note.
- `docs/security/threat-model.md` — append the vision-analysis data path.

---

## Task 1: Vision settings + `VisionConfig`

**Files:**
- Modify: `app/config.py` (add fields to `Settings`)
- Create: `app/modules/vision/__init__.py` (empty for now), `app/modules/vision/config.py`
- Test: `tests/vision/test_vision_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_vision_config.py
from app.config import Settings
from app.modules.vision.config import VisionConfig


def test_vision_config_reads_settings(monkeypatch):
    monkeypatch.setenv("VISION_GAZE_WEIGHTS_PATH", "/weights/L2CSNet_gaze360.pkl")
    monkeypatch.setenv("VISION_GAZE_ARCH", "ResNet50")
    monkeypatch.setenv("VISION_SAMPLE_FPS", "5.0")
    cfg = VisionConfig(Settings())
    assert cfg.gaze_weights_path == "/weights/L2CSNet_gaze360.pkl"
    assert cfg.gaze_arch == "ResNet50"
    assert cfg.sample_fps == 5.0


def test_vision_config_defaults():
    cfg = VisionConfig(Settings())
    # Off-screen sustained default ≥ 2s; band thresholds are present.
    assert cfg.off_screen_min_ms == 2000
    assert cfg.sample_fps == 5.0
    assert 0.0 < cfg.band_high_off_screen_pct <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_vision_config.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.config`

- [ ] **Step 3: Add the Settings fields**

In `app/config.py`, inside the `Settings` class (alongside the other `recording_*` fields), add:

```python
    # --- Vision proctoring (server plane, Phase 3D vision) ---
    # Gaze model behind the swappable GazeEstimator seam. The Gaze360 weights
    # are NON-COMMERCIAL (dev/POC only) — see spec §16.8. Swap path = env change.
    vision_gaze_weights_path: str = ""
    vision_gaze_arch: str = "ResNet50"
    vision_sample_fps: float = 5.0
    # Self-baseline zone thresholds (degrees of deviation from the per-session
    # baseline gaze direction).
    vision_zone_yaw_deg: float = 15.0
    vision_zone_pitch_deg: float = 12.0
    vision_far_off_deg: float = 35.0
    # Sustained off-screen flag: minimum continuous off-center duration (ms).
    vision_off_screen_min_ms: int = 2000
    # Down-glance: a brief pitch-down excursion between 300ms and 4000ms.
    vision_down_glance_min_ms: int = 300
    vision_down_glance_max_ms: int = 4000
    # Reading-sweep: ≥ this many L/R reversals within the window (ms).
    vision_reading_window_ms: int = 4000
    vision_reading_min_reversals: int = 4
    # Multi-face: sustained ≥2 faces for ≥ this many ms.
    vision_multi_face_min_ms: int = 1500
    # Band thresholds (fractions of session / counts).
    vision_band_high_off_screen_pct: float = 0.25
    vision_band_medium_off_screen_pct: float = 0.10
    vision_band_high_down_glances: int = 12
    # Frame is unscorable above this fraction → band = insufficient_data.
    vision_max_unscorable_pct: float = 0.6
```

- [ ] **Step 4: Write `app/modules/vision/config.py`**

```python
# app/modules/vision/config.py
"""Env-driven vision-proctoring config — single source for model + thresholds.

Mirrors app/ai/config.py discipline: never hardcode the gaze weights path or a
detector threshold elsewhere. Swapping the gaze model (e.g. to clean weights or
a MediaPipe estimator — spec §16.2/§16.8) is an env change.
"""
from __future__ import annotations

from app.config import Settings, settings


class VisionConfig:
    def __init__(self, _settings: Settings | None = None) -> None:
        self._s = _settings if _settings is not None else Settings()

    @property
    def gaze_weights_path(self) -> str:
        return self._s.vision_gaze_weights_path

    @property
    def gaze_arch(self) -> str:
        return self._s.vision_gaze_arch

    @property
    def sample_fps(self) -> float:
        return self._s.vision_sample_fps

    @property
    def zone_yaw_deg(self) -> float:
        return self._s.vision_zone_yaw_deg

    @property
    def zone_pitch_deg(self) -> float:
        return self._s.vision_zone_pitch_deg

    @property
    def far_off_deg(self) -> float:
        return self._s.vision_far_off_deg

    @property
    def off_screen_min_ms(self) -> int:
        return self._s.vision_off_screen_min_ms

    @property
    def down_glance_min_ms(self) -> int:
        return self._s.vision_down_glance_min_ms

    @property
    def down_glance_max_ms(self) -> int:
        return self._s.vision_down_glance_max_ms

    @property
    def reading_window_ms(self) -> int:
        return self._s.vision_reading_window_ms

    @property
    def reading_min_reversals(self) -> int:
        return self._s.vision_reading_min_reversals

    @property
    def multi_face_min_ms(self) -> int:
        return self._s.vision_multi_face_min_ms

    @property
    def band_high_off_screen_pct(self) -> float:
        return self._s.vision_band_high_off_screen_pct

    @property
    def band_medium_off_screen_pct(self) -> float:
        return self._s.vision_band_medium_off_screen_pct

    @property
    def band_high_down_glances(self) -> int:
        return self._s.vision_band_high_down_glances

    @property
    def max_unscorable_pct(self) -> float:
        return self._s.vision_max_unscorable_pct


vision_config = VisionConfig(settings)
```

Leave `app/modules/vision/__init__.py` empty for now (populated in Task 12).

- [ ] **Step 5: Run test + commit**

Run: `docker compose run --rm nexus pytest tests/vision/test_vision_config.py -v` → Expected: PASS
```bash
git add app/config.py app/modules/vision/__init__.py app/modules/vision/config.py tests/vision/test_vision_config.py
git commit -m "feat(vision): add vision-proctoring settings + VisionConfig"
```

---

## Task 2: `GazeEstimator` interface (the swap seam)

**Files:**
- Create: `app/modules/vision/gaze/__init__.py` (empty), `app/modules/vision/gaze/base.py`
- Test: `tests/vision/test_gaze_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_gaze_base.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_base.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.gaze.base`

- [ ] **Step 3: Write `gaze/base.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_base.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/gaze/__init__.py app/modules/vision/gaze/base.py tests/vision/test_gaze_base.py
git commit -m "feat(vision): GazeEstimator protocol + FaceGaze (the swap seam)"
```

---

## Task 3: Detectors — observation type, baseline, zone classification

**Files:**
- Create: `app/modules/vision/detectors.py`
- Test: `tests/vision/test_detectors_baseline.py`

`detectors.py` must import **stdlib only** (`math`, `statistics`, `dataclasses`, `collections`) — no cv2/torch/numpy — so these tests run in the standard image.

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_detectors_baseline.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    estimate_baseline,
    classify_zone,
)


def _obs(t_ms, yaw_deg, pitch_deg, faces=1, quality=0.9):
    return FrameObservation(
        t_ms=t_ms,
        faces=faces,
        yaw=math.radians(yaw_deg) if yaw_deg is not None else None,
        pitch=math.radians(pitch_deg) if pitch_deg is not None else None,
        quality=quality,
    )


def test_baseline_is_the_dense_cluster():
    # 8 frames clustered near (yaw=2°, pitch=3°), 2 outliers far away.
    obs = [_obs(i * 200, 2, 3) for i in range(8)]
    obs += [_obs(2000, 40, 30), _obs(2200, -45, 25)]
    by, bp = estimate_baseline(obs)
    assert abs(math.degrees(by) - 2) < 6
    assert abs(math.degrees(bp) - 3) < 6


def test_baseline_ignores_unscorable_frames():
    obs = [_obs(i * 200, 1, 1) for i in range(5)]
    obs += [FrameObservation(t_ms=9999, faces=0, yaw=None, pitch=None, quality=0.0)]
    by, bp = estimate_baseline(obs)
    assert abs(math.degrees(by) - 1) < 5


def test_classify_zone_center_and_deviations():
    base = (0.0, 0.0)
    th = dict(zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0)
    assert classify_zone(math.radians(2), math.radians(2), *base, **th) == "center"
    assert classify_zone(math.radians(25), math.radians(0), *base, **th) == "right"
    assert classify_zone(math.radians(-25), math.radians(0), *base, **th) == "left"
    assert classify_zone(math.radians(0), math.radians(25), *base, **th) == "down"
    assert classify_zone(math.radians(0), math.radians(-25), *base, **th) == "up"
    assert classify_zone(math.radians(50), math.radians(40), *base, **th) == "far_off"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.detectors`

- [ ] **Step 3: Write the baseline + zone portion of `detectors.py`**

```python
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
from dataclasses import dataclass, field


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_baseline.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/detectors.py tests/vision/test_detectors_baseline.py
git commit -m "feat(vision): detector baseline + zone classification (pure)"
```

---

## Task 4: Detectors — off-screen, down-glance, reading-sweep, multi-face intervals

**Files:**
- Modify: `app/modules/vision/detectors.py`
- Test: `tests/vision/test_detectors_intervals.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_detectors_intervals.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    Interval,
    detect_off_screen_intervals,
    detect_down_glances,
    detect_reading_sweeps,
    detect_multi_face_intervals,
)

TH = dict(zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0)


def _o(t, yaw_deg, pitch_deg, faces=1, q=0.9):
    return FrameObservation(
        t_ms=t, faces=faces,
        yaw=None if yaw_deg is None else math.radians(yaw_deg),
        pitch=None if pitch_deg is None else math.radians(pitch_deg),
        quality=q,
    )


def test_off_screen_sustained_flagged_above_min_ms():
    # 0–1000ms centered, 1000–4000ms looking right (off), back centered.
    obs = [_o(t, 2, 2) for t in range(0, 1001, 200)]
    obs += [_o(t, 30, 0) for t in range(1200, 4001, 200)]
    obs += [_o(t, 2, 2) for t in range(4200, 5001, 200)]
    out = detect_off_screen_intervals(obs, (0.0, 0.0), min_ms=2000, thresholds=TH)
    assert len(out) == 1
    assert out[0].start_ms >= 1000 and out[0].end_ms <= 4200
    assert out[0].kind == "off_screen_sustained"


def test_off_screen_brief_not_flagged():
    obs = [_o(t, 2, 2) for t in range(0, 1001, 200)]
    obs += [_o(1200, 30, 0), _o(1400, 30, 0)]  # only ~400ms off
    obs += [_o(t, 2, 2) for t in range(1600, 2601, 200)]
    out = detect_off_screen_intervals(obs, (0.0, 0.0), min_ms=2000, thresholds=TH)
    assert out == []


def test_down_glances_counts_brief_pitch_down():
    obs = []
    t = 0
    for _ in range(3):  # three down-glances of ~600ms each
        obs += [_o(t, 0, 2), _o(t + 200, 0, 2)]
        obs += [_o(t + 400, 0, 30), _o(t + 600, 0, 30), _o(t + 800, 0, 30)]
        t += 1200
    out = detect_down_glances(obs, (0.0, 0.0), min_ms=300, max_ms=4000, thresholds=TH)
    assert len(out) == 3
    assert all(i.kind == "down_glance" for i in out)


def test_reading_sweep_detects_rhythmic_horizontal_reversals():
    # Alternate left/right every 200ms for 4s → many reversals.
    obs = []
    for i in range(20):
        yaw = 25 if i % 2 == 0 else -25
        obs.append(_o(i * 200, yaw, 0))
    out = detect_reading_sweeps(obs, (0.0, 0.0), window_ms=4000, min_reversals=4, thresholds=TH)
    assert len(out) >= 1
    assert out[0].kind == "reading_sweep"


def test_multi_face_intervals_flag_sustained_two_faces():
    obs = [_o(t, 2, 2, faces=1) for t in range(0, 1001, 200)]
    obs += [_o(t, 2, 2, faces=2) for t in range(1200, 3201, 200)]  # ~2s of 2 faces
    obs += [_o(t, 2, 2, faces=1) for t in range(3400, 4001, 200)]
    out = detect_multi_face_intervals(obs, min_ms=1500)
    assert len(out) == 1 and out[0].kind == "multiple_faces"
    assert out[0].max_faces == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_intervals.py -v`
Expected: FAIL — `ImportError: cannot import name 'Interval'`

- [ ] **Step 3: Add the interval detectors to `detectors.py`**

Append to `app/modules/vision/detectors.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_intervals.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/detectors.py tests/vision/test_detectors_intervals.py
git commit -m "feat(vision): off-screen/down-glance/reading-sweep/multi-face detectors"
```

---

## Task 5: Detectors — heatmap, band, and the `analyze_observations` aggregator

**Files:**
- Modify: `app/modules/vision/detectors.py`
- Test: `tests/vision/test_detectors_aggregate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_detectors_aggregate.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    AnalysisResult,
    analyze_observations,
)

CFG = dict(
    zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0,
    off_screen_min_ms=2000, down_glance_min_ms=300, down_glance_max_ms=4000,
    reading_window_ms=4000, reading_min_reversals=4, multi_face_min_ms=1500,
    band_high_off_screen_pct=0.25, band_medium_off_screen_pct=0.10,
    band_high_down_glances=12, max_unscorable_pct=0.6,
)


def _o(t, yaw_deg, pitch_deg, faces=1, q=0.9):
    return FrameObservation(
        t_ms=t, faces=faces,
        yaw=None if yaw_deg is None else math.radians(yaw_deg),
        pitch=None if pitch_deg is None else math.radians(pitch_deg),
        quality=q,
    )


def test_clean_session_is_low_band():
    obs = [_o(t, 1, 1) for t in range(0, 20001, 200)]
    res = analyze_observations(obs, **CFG)
    assert isinstance(res, AnalysisResult)
    assert res.risk_band == "low"
    assert res.detector_summary["off_screen_pct"] < 0.05
    assert res.detector_summary["max_faces"] == 1
    assert len(res.gaze_heatmap["grid"]) == 5 and len(res.gaze_heatmap["grid"][0]) == 5


def test_heavy_off_screen_is_high_band():
    # >25% of the session looking right.
    obs = [_o(t, 1, 1) for t in range(0, 10001, 200)]      # ~10s centered
    obs += [_o(t, 30, 0) for t in range(10200, 16001, 200)]  # ~6s off (>25% of 16s)
    res = analyze_observations(obs, **CFG)
    assert res.risk_band == "high"
    assert res.flagged_intervals  # at least the off-screen interval


def test_all_unscorable_is_insufficient_data():
    obs = [FrameObservation(t_ms=t, faces=0, yaw=None, pitch=None, quality=0.0)
           for t in range(0, 5001, 200)]
    res = analyze_observations(obs, **CFG)
    assert res.risk_band == "insufficient_data"
    assert res.gaze_signal_quality in ("unscorable", "low-light")
    assert res.unscorable_pct > 0.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_aggregate.py -v`
Expected: FAIL — `ImportError: cannot import name 'AnalysisResult'`

- [ ] **Step 3: Add heatmap, band, and aggregator to `detectors.py`**

Append to `app/modules/vision/detectors.py`:

```python
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
    thresholds = dict(zone_yaw_deg=zone_yaw_deg, zone_pitch_deg=zone_pitch_deg, far_off_deg=far_off_deg)
    total = len(obs)
    scorable = [o for o in obs if _scorable(o)]
    unscorable_pct = round(1 - (len(scorable) / total), 3) if total else 1.0

    base = estimate_baseline(obs)

    off = detect_off_screen_intervals(obs, base, min_ms=off_screen_min_ms, thresholds=thresholds)
    downs = detect_down_glances(obs, base, min_ms=down_glance_min_ms, max_ms=down_glance_max_ms, thresholds=thresholds)
    reads = detect_reading_sweeps(obs, base, window_ms=reading_window_ms, min_reversals=reading_min_reversals, thresholds=thresholds)
    faces = detect_multi_face_intervals(obs, min_ms=multi_face_min_ms)

    # Off-screen % over scorable frames.
    off_frames = sum(1 for o in scorable if classify_zone(o.yaw, o.pitch, base[0], base[1], **thresholds) != "center")
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
        "multi_face_intervals": [{"start_ms": i.start_ms, "end_ms": i.end_ms, "max_faces": i.max_faces} for i in faces],
    }

    # --- Transparent 3-tier band (spec §16.5) ---
    if unscorable_pct > max_unscorable_pct:
        band = "insufficient_data"
    elif (off_pct >= band_high_off_screen_pct or max_faces >= 2
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_detectors_aggregate.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/detectors.py tests/vision/test_detectors_aggregate.py
git commit -m "feat(vision): heatmap + 3-tier band + analyze_observations aggregator"
```

---

## Task 6: Migration 0051 + `SessionProctoringAnalysis` ORM + RLS registration

**Files:**
- Create: `migrations/versions/0051_session_proctoring_analysis.py`, `app/modules/vision/models.py`
- Modify: `app/main.py` (`_TENANT_SCOPED_TABLES`)
- Test: `tests/vision/test_proctoring_analysis_rls.py`

- [ ] **Step 1: Write the failing test**

Note: the test DB uses `Base.metadata.create_all` with `DB_RUNTIME_ROLE=""` (postgres bypasses RLS), so a live cross-tenant read cannot exercise policies. Per the repo convention (`project_test_harness_rls`), assert membership in `_TENANT_SCOPED_TABLES` (boot assertion + migration enforce the policy pair in real envs) and the model's tenant column.

```python
# tests/vision/test_proctoring_analysis_rls.py
from app.main import _TENANT_SCOPED_TABLES
from app.modules.vision.models import SessionProctoringAnalysis


def test_table_registered_for_rls_completeness():
    assert "session_proctoring_analysis" in _TENANT_SCOPED_TABLES


def test_model_is_tenant_scoped():
    cols = SessionProctoringAnalysis.__table__.columns
    assert "tenant_id" in cols
    assert "session_id" in cols
    assert cols["session_id"].unique is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_proctoring_analysis_rls.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.models`

- [ ] **Step 3a: Write the ORM model `app/modules/vision/models.py`**

```python
# app/modules/vision/models.py
"""ORM for session_proctoring_analysis — one row per session (features only).

Stores NO frames/templates (spec §16.6/D6): only derived gaze features, the
risk band, flagged intervals, heatmap, and model_versions for auditability.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionProctoringAnalysis(Base):
    __tablename__ = "session_proctoring_analysis"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    risk_band: Mapped[str | None] = mapped_column(Text)
    detector_summary: Mapped[dict | None] = mapped_column(JSONB)
    gaze_heatmap: Mapped[dict | None] = mapped_column(JSONB)
    flagged_intervals: Mapped[list | None] = mapped_column(JSONB)
    gaze_signal_quality: Mapped[str | None] = mapped_column(Text)
    unscorable_pct: Mapped[float | None] = mapped_column(Numeric)
    model_versions: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    frames_analyzed: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 3b: Write the migration `migrations/versions/0051_session_proctoring_analysis.py`**

```python
"""session_proctoring_analysis — post-session vision proctoring features.

One row per session. Stores derived gaze/multi-face features only (no frames).
Canonical tenant_isolation + service_bypass RLS pair (NULLIF discipline).

Rollback: downgrade drops the table (policies + trigger drop with it).

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON {table}
          USING (current_setting('app.bypass_rls', true) = 'true');
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO nexus_app;")


def upgrade() -> None:
    op.create_table(
        "session_proctoring_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("risk_band", sa.Text()),
        sa.Column("detector_summary", postgresql.JSONB()),
        sa.Column("gaze_heatmap", postgresql.JSONB()),
        sa.Column("flagged_intervals", postgresql.JSONB()),
        sa.Column("gaze_signal_quality", sa.Text()),
        sa.Column("unscorable_pct", sa.Numeric()),
        sa.Column("model_versions", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("frames_analyzed", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute(
        "ALTER TABLE session_proctoring_analysis ADD CONSTRAINT spa_status_check "
        "CHECK (status IN ('pending','running','ready','failed','unscorable'))"
    )
    _enable_rls("session_proctoring_analysis")
    op.execute("""
        CREATE TRIGGER session_proctoring_analysis_touch_updated_at
            BEFORE UPDATE ON session_proctoring_analysis
            FOR EACH ROW EXECUTE FUNCTION touch_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS session_proctoring_analysis_touch_updated_at ON session_proctoring_analysis;")
    op.drop_table("session_proctoring_analysis")
```

- [ ] **Step 3c: Register the table in `app/main.py`**

In `_TENANT_SCOPED_TABLES`, after the `"session_reports",` line, add:

```python
    # Phase 3D vision — post-session proctoring analysis (migration 0051).
    "session_proctoring_analysis",
```

- [ ] **Step 4: Run test + apply migration**

Run: `docker compose run --rm nexus pytest tests/vision/test_proctoring_analysis_rls.py -v` → Expected: PASS
Run: `docker compose run --rm nexus alembic upgrade head` → Expected: applies `0051` cleanly.
Run: `docker compose run --rm nexus alembic downgrade -1 && docker compose run --rm nexus alembic upgrade head` → Expected: rollback + re-apply both succeed.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0051_session_proctoring_analysis.py app/modules/vision/models.py app/main.py tests/vision/test_proctoring_analysis_rls.py
git commit -m "feat(vision): session_proctoring_analysis table + RLS + ORM (migration 0051)"
```

---

## Task 7: Storage — `download_to_path`

**Files:**
- Modify: `app/storage/base.py`, `app/storage/s3.py`
- Test: `tests/vision/test_storage_download.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_storage_download.py
from unittest.mock import MagicMock

import pytest

from app.storage.s3 import S3CompatibleStorage


@pytest.mark.asyncio
async def test_download_to_path_calls_boto_download_file(tmp_path, monkeypatch):
    store = S3CompatibleStorage(
        bucket="rec", region="auto", endpoint_url="https://r2.example",
        access_key_id="k", secret_access_key="s", force_path_style=False,
    )
    fake_client = MagicMock()
    monkeypatch.setattr(store, "_client", lambda: fake_client)
    dest = tmp_path / "v.mp4"
    await store.download_to_path("sessions/abc/recording.mp4", str(dest))
    fake_client.download_file.assert_called_once_with(
        "rec", "sessions/abc/recording.mp4", str(dest)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_storage_download.py -v`
Expected: FAIL — `AttributeError: 'S3CompatibleStorage' object has no attribute 'download_to_path'`

- [ ] **Step 3: Implement**

In `app/storage/base.py`, add to the `ObjectStorage` Protocol (after `head`):

```python
    async def download_to_path(self, key: str, dest_path: str) -> None:
        """Download the object to a local filesystem path (overwrites)."""
        ...
```

In `app/storage/s3.py`, add to `S3CompatibleStorage` (after `head`):

```python
    async def download_to_path(self, key: str, dest_path: str) -> None:
        client = self._client()
        await asyncio.to_thread(client.download_file, self._bucket, key, dest_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_storage_download.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/base.py app/storage/s3.py tests/vision/test_storage_download.py
git commit -m "feat(storage): add download_to_path for offline analysis"
```

---

## Task 8: `MobileGazeEstimator` (MobileGaze ONNX, lazy heavy imports) + estimator config

**Model choice (revised 2026-05-30):** we use **MobileGaze** (`yakhyo/gaze-estimation`, MIT — a maintained L2CS-Net reimplementation) via **ONNX** (`onnxruntime`, **no torch**) instead of the original `l2cs` pip package. Face detection = `uniface` **RetinaFace** (ONNX, auto-downloads its model). v1 weights = `resnet34_gaze.onnx` (from the repo's releases). **Licensing is unchanged:** those weights are Gaze360-trained = NON-COMMERCIAL, dev/POC only (spec §16.8). The `GazeEstimator` seam (Task 2) is untouched.

**Files:**
- Modify: `app/config.py` (3 estimator fields + 1 default change), `app/modules/vision/config.py` (3 properties), `tests/vision/test_vision_config.py` (extend)
- Create: `app/modules/vision/gaze/mobilegaze.py`
- Test: `tests/vision/test_gaze_mobilegaze_lazy.py`

No real-model test (manual per D9). Automated guard: importing the wrapper module must NOT import onnxruntime/cv2/uniface (keeps the lean nexus image safe).

> **External-API adaptation point:** the `uniface` RetinaFace API and the `resnet34_gaze.onnx` output names are external. The code below targets the documented API (`from uniface.detection import RetinaFace`; `detector.detect(img)` → list of objects with `.bbox`/`.confidence`; ONNX outputs decoded by name). After writing it, run a quick `docker compose run --rm nexus-vision-worker python -c "from uniface.detection import RetinaFace; print('ok')"` (once Task 15's image exists) to confirm the import path, and adapt if the installed version differs. Do NOT block the lazy-import test on this — that test doesn't import the heavy deps.

- [ ] **Step 1: Extend the estimator config**

In `app/config.py`, change the existing `vision_gaze_arch` default and add three fields (in the same Vision block from Task 1):

```python
    vision_gaze_arch: str = "resnet34"  # changed from "ResNet50" — matches resnet34_gaze.onnx
    vision_gaze_input_size: int = 448
    # Sign mapping from the model's pitch/yaw to our convention (pitch+ = down,
    # yaw+ = camera-right). Flip to -1 during manual calibration (D9) if
    # looking-down is not classified as the 'down' zone / left-right is mirrored.
    vision_gaze_pitch_sign: int = 1
    vision_gaze_yaw_sign: int = 1
```

In `app/modules/vision/config.py`, add three properties to `VisionConfig`:

```python
    @property
    def gaze_input_size(self) -> int:
        return self._s.vision_gaze_input_size

    @property
    def gaze_pitch_sign(self) -> int:
        return self._s.vision_gaze_pitch_sign

    @property
    def gaze_yaw_sign(self) -> int:
        return self._s.vision_gaze_yaw_sign
```

Extend `tests/vision/test_vision_config.py::test_vision_config_defaults` with:

```python
    assert cfg.gaze_input_size == 448
    assert cfg.gaze_arch == "resnet34"
    assert cfg.gaze_pitch_sign in (1, -1)
```

Run: `docker compose run --rm nexus pytest tests/vision/test_vision_config.py -v` → Expected: PASS.

- [ ] **Step 2: Write the failing lazy-import test**

```python
# tests/vision/test_gaze_mobilegaze_lazy.py
import sys


def test_importing_module_does_not_import_heavy_deps():
    # Importing the wrapper must stay light — onnxruntime/cv2/uniface load only
    # when an estimator is constructed (inside the vision-worker image).
    for mod in ("onnxruntime", "cv2", "uniface"):
        sys.modules.pop(mod, None)
    import importlib
    import app.modules.vision.gaze.mobilegaze as m
    importlib.reload(m)
    assert "onnxruntime" not in sys.modules
    assert "cv2" not in sys.modules
    assert "uniface" not in sys.modules
    assert hasattr(m, "MobileGazeEstimator")
```

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_mobilegaze_lazy.py -v` → Expected: FAIL (`ModuleNotFoundError: app.modules.vision.gaze.mobilegaze`).

- [ ] **Step 3: Write `gaze/mobilegaze.py`**

```python
# app/modules/vision/gaze/mobilegaze.py
"""MobileGaze (yakhyo/gaze-estimation, MIT) ONNX gaze estimator (v1).

`resnet34_gaze.onnx` is Gaze360-trained = NON-COMMERCIAL, dev/POC only
(spec §16.8). Heavy deps (onnxruntime / cv2 / numpy / uniface) import LAZILY in
__init__ so the lean nexus API image can import the module graph without them.

Pipeline (mirrors the MobileGaze ONNX inference): RetinaFace detect → per-face
crop → BGR->RGB, resize to input_size², /255, ImageNet-normalize, CHW, batch →
ONNX → 90-bin softmax expectation (×4 − 180) → degrees → radians.
"""
from __future__ import annotations

import structlog

from app.modules.vision.gaze.base import FaceGaze

log = structlog.get_logger("vision.gaze.mobilegaze")


class MobileGazeEstimator:
    """One instance per worker process (model load + detector init are costly).
    `estimate` returns one FaceGaze per detected face.
    """

    def __init__(
        self,
        *,
        weights_path: str,
        input_size: int = 448,
        pitch_sign: int = 1,
        yaw_sign: int = 1,
    ) -> None:
        # Lazy — only the vision-worker image has these installed.
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
        from uniface.detection import RetinaFace  # noqa: PLC0415

        self._np = np
        self._session = ort.InferenceSession(weights_path, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        self._detector = RetinaFace()
        self._size = (input_size, input_size)
        self._pitch_sign = pitch_sign
        self._yaw_sign = yaw_sign
        self._mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        self._std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
        self._idx = np.arange(90, dtype=np.float32)
        log.info("vision.gaze.mobilegaze.loaded", outputs=self._output_names, input_size=input_size)

    def _preprocess(self, crop_bgr):
        import cv2  # noqa: PLC0415

        np = self._np
        img = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, self._size).astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = (img - self._mean) / self._std
        return np.expand_dims(img, 0).astype(np.float32)

    def _decode(self, logits):
        """90-bin softmax expectation → radians (binwidth 4°, offset 180°)."""
        np = self._np
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        deg = float((probs * self._idx).sum(axis=1)[0] * 4.0 - 180.0)
        return float(np.radians(deg))

    def _split_yaw_pitch(self, outs):
        """Map outputs to (yaw_logits, pitch_logits). Prefer output NAMES (robust
        to export order); fall back to L2CS/MobileGaze [pitch, yaw] order.
        VERIFY the sign/zone mapping in the manual D9 test.
        """
        named = dict(zip(self._output_names, outs, strict=False))
        yaw_k = next((k for k in self._output_names if "yaw" in k.lower()), None)
        pitch_k = next((k for k in self._output_names if "pitch" in k.lower()), None)
        if yaw_k is not None and pitch_k is not None:
            return named[yaw_k], named[pitch_k]
        return outs[1], outs[0]  # fallback: [pitch, yaw]

    def estimate(self, frame_bgr) -> list[FaceGaze]:
        h, w = frame_bgr.shape[:2]
        faces = self._detector.detect(frame_bgr)
        out: list[FaceGaze] = []
        for f in faces:
            x1, y1, x2, y2 = (int(v) for v in f.bbox[:4])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            inp = self._preprocess(frame_bgr[y1:y2, x1:x2])
            outs = self._session.run(self._output_names, {self._input_name: inp})
            yaw_logits, pitch_logits = self._split_yaw_pitch(outs)
            out.append(
                FaceGaze(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    pitch=self._decode(pitch_logits) * self._pitch_sign,
                    yaw=self._decode(yaw_logits) * self._yaw_sign,
                    score=float(getattr(f, "confidence", 1.0)),
                )
            )
        return out
```

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_mobilegaze_lazy.py -v` → Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/config.py app/modules/vision/config.py app/modules/vision/gaze/mobilegaze.py tests/vision/test_vision_config.py tests/vision/test_gaze_mobilegaze_lazy.py
git commit -m "feat(vision): MobileGazeEstimator (ONNX, no torch) + estimator config"
```

---

## Task 9: `analysis.py` — download → sample → estimate → analyze

**Files:**
- Create: `app/modules/vision/analysis.py`
- Test: `tests/vision/test_analysis_observations.py`

The frame I/O (ffmpeg + cv2) is manual-only (D9). The unit test covers the pure seam: `observations_from_estimates` (maps per-frame estimator output → `FrameObservation`, picking the largest-bbox face as primary).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_analysis_observations.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.analysis`

- [ ] **Step 3: Write `analysis.py`**

```python
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
            t_ms=t_ms, faces=len(faces), yaw=primary.yaw, pitch=primary.pitch, quality=primary.score,
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
        zone_yaw_deg=cfg.zone_yaw_deg, zone_pitch_deg=cfg.zone_pitch_deg, far_off_deg=cfg.far_off_deg,
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
```

Note: `analysis.py` does NOT import storage/tempfile/os — the actor (Task 10) owns the async R2 download and passes a local path. Header imports are only `structlog`, config, detectors, and gaze (as shown above). `_sample_frames` imports `cv2` lazily.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_analysis_observations.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/analysis.py tests/vision/test_analysis_observations.py
git commit -m "feat(vision): analysis orchestration + observations_from_estimates (pure)"
```

---

## Task 10: The `analyze_session_proctoring` actor

**Files:**
- Create: `app/modules/vision/actors.py`
- Test: `tests/vision/test_actor_idempotency.py`

(`analysis.py` already exposes `run_analysis(estimator, *, local_video_path)` from Task 9 — no change needed here. The actor owns the async R2 download.)

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_actor_idempotency.py
import uuid

import pytest

from app.modules.vision import actors as vision_actors


def _never_analyze(*a, **k):
    raise AssertionError("must not analyze on a skip/none action")


@pytest.mark.asyncio
async def test_actor_skips_when_already_done(monkeypatch):
    # Existing ready/unscorable row → _load_state returns ("skip", None) → no work.
    async def _fake(db, session_id, tenant_id):
        return "skip", None

    monkeypatch.setattr(vision_actors, "_load_state", _fake)
    monkeypatch.setattr(vision_actors, "run_analysis", _never_analyze)
    await vision_actors._run(str(uuid.uuid4()), str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_actor_skips_when_no_recording(monkeypatch):
    # No usable recording → _load_state returns ("none", None) → no work.
    async def _fake(db, session_id, tenant_id):
        return "none", None

    monkeypatch.setattr(vision_actors, "_load_state", _fake)
    monkeypatch.setattr(vision_actors, "run_analysis", _never_analyze)
    await vision_actors._run(str(uuid.uuid4()), str(uuid.uuid4()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_actor_idempotency.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.actors`

- [ ] **Step 3: Write `app/modules/vision/actors.py`**

Top-level imports stay light (no onnxruntime/cv2/uniface). `run_analysis` and `MobileGazeEstimator` are imported lazily inside `_run`.

```python
# app/modules/vision/actors.py
"""Dramatiq actor: post-session vision proctoring analysis (vision queue).

Idempotent on session_id (status row). Runs on a bypass-RLS session with an
explicit tenant_id filter on every query (RLS-only defense, mirrors
interview_runtime.service). Persists FEATURES ONLY — never frames (spec §16).
"""
from __future__ import annotations

import os
import tempfile
import uuid

import dramatiq
import structlog
from sqlalchemy import select, text

from app.database import get_bypass_session
from app.modules.session.models import Session
from app.modules.vision.config import vision_config
from app.modules.vision.models import SessionProctoringAnalysis
from app.storage import get_object_storage

# NOTE: run_analysis is re-exported here so tests can monkeypatch
# `vision_actors.run_analysis`. The heavy gaze import stays inside _run.
from app.modules.vision.analysis import run_analysis  # light: no cv2/torch at import

log = structlog.get_logger("vision.actor")

# Genuinely-finished SUCCESS states. A row in one of these is never re-analyzed.
# A 'running'/'failed'/'pending' row is RECLAIMED and re-analyzed — so Dramatiq's
# own retries (and a re-enqueue after a worker crash) actually re-run, instead of
# being silently swallowed by the idempotency gate.
_DONE = {"ready", "unscorable"}


async def _load_state(db, session_id: str, tenant_id: str):
    """Return ``(action, recording_key)`` where action is:
      "skip" — an existing row is already done (ready/unscorable); do nothing.
      "run"  — (re)analyze; recording_key is the R2 object key. A pre-existing
               running/failed/pending row is RECLAIMED to 'running' (not
               duplicated) so retries/crash-recovery re-run cleanly.
      "none" — the session has no usable recording yet; do nothing.
    """
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)
    existing = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == sid,
                SessionProctoringAnalysis.tenant_id == tid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status in _DONE:
        return "skip", None

    sess = (
        await db.execute(
            select(Session).where(Session.id == sid, Session.tenant_id == tid)
        )
    ).scalar_one_or_none()
    if sess is None or sess.recording_status != "ready" or not sess.recording_s3_key:
        return "none", None

    if existing is not None:
        # Reclaim a crashed/failed/pending row — re-drive it rather than insert
        # a duplicate (session_id is UNIQUE).
        existing.status = "running"
        existing.error = None
    else:
        db.add(SessionProctoringAnalysis(tenant_id=tid, session_id=sid, status="running"))
    return "run", sess.recording_s3_key


async def _persist(db, session_id: str, tenant_id: str, *, status: str, result=None, frames=0, error=None):
    sid = uuid.UUID(session_id)
    tid = uuid.UUID(tenant_id)
    row = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == sid,
                SessionProctoringAnalysis.tenant_id == tid,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Phase-1 insert never committed (rare: DB error between create + commit).
        # Don't raise here — that would mask the original exception on the error
        # path. The re-enqueue will recreate the row.
        log.warning("vision.actor.persist_no_row", session_id=session_id)
        return
    row.status = status
    row.error = error
    row.frames_analyzed = frames
    if result is not None:
        row.risk_band = result.risk_band
        row.detector_summary = result.detector_summary
        row.gaze_heatmap = result.gaze_heatmap
        row.flagged_intervals = result.flagged_intervals
        row.gaze_signal_quality = result.gaze_signal_quality
        row.unscorable_pct = result.unscorable_pct
        row.model_versions = {
            "gaze": "mobilegaze-gaze360",
            "weights_path": vision_config.gaze_weights_path,
            "arch": vision_config.gaze_arch,
            "pipeline": "v1",
        }


async def _run(session_id: str, tenant_id: str) -> None:
    safe_tid = str(uuid.UUID(tenant_id))

    # Phase 1: idempotency gate + claim/reclaim a 'running' row (own transaction).
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
        action, recording_key = await _load_state(db, session_id, tenant_id)
        await db.commit()

    if action == "skip":
        log.info("vision.actor.skip_already_done", session_id=session_id)
        return
    if action == "none":
        log.info("vision.actor.no_recording", session_id=session_id)
        return
    # action == "run" → recording_key is set; proceed.

    # Phase 2: heavy work OUTSIDE the DB transaction.
    try:
        from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator  # noqa: PLC0415

        estimator = MobileGazeEstimator(
            weights_path=vision_config.gaze_weights_path,
            input_size=vision_config.gaze_input_size,
            pitch_sign=vision_config.gaze_pitch_sign,
            yaw_sign=vision_config.gaze_yaw_sign,
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "recording.mp4")
            await get_object_storage().download_to_path(recording_key, dest)
            result, frames = run_analysis(estimator, local_video_path=dest)
        final_status = "unscorable" if result.risk_band == "insufficient_data" else "ready"
    except Exception as exc:  # noqa: BLE001
        log.error("vision.actor.failed", session_id=session_id, exc_info=exc)
        async with get_bypass_session() as db:
            await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
            await _persist(db, session_id, tenant_id, status="failed", error=str(exc)[:500])
            await db.commit()
        raise

    # Phase 3: persist results (own transaction).
    async with get_bypass_session() as db:
        await db.execute(text(f"SET LOCAL app.current_tenant = '{safe_tid}'"))
        await _persist(db, session_id, tenant_id, status=final_status, result=result, frames=frames)
        await db.commit()
    log.info("vision.actor.done", session_id=session_id, band=result.risk_band, frames=frames)


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=120_000, queue_name="vision")
async def analyze_session_proctoring(session_id: str, tenant_id: str) -> None:
    await _run(session_id, tenant_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_actor_idempotency.py tests/vision/test_analysis_observations.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/actors.py tests/vision/test_actor_idempotency.py
git commit -m "feat(vision): analyze_session_proctoring actor (idempotent, features-only)"
```

---

## Task 11: Read service + schema

**Files:**
- Create: `app/modules/vision/service.py`, `app/modules/vision/schemas.py`
- Test: `tests/vision/test_service_read.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_service_read.py
from app.modules.vision.schemas import ProctoringAnalysisRead


def test_read_schema_absent_default():
    r = ProctoringAnalysisRead(status="absent")
    assert r.status == "absent"
    assert r.risk_band is None
    assert r.flagged_intervals == []


def test_read_schema_full_roundtrip():
    r = ProctoringAnalysisRead(
        status="ready", risk_band="medium",
        detector_summary={"off_screen_pct": 0.12, "max_faces": 1,
                          "down_glance_count": 4, "reading_sweep_intervals": 1,
                          "multi_face_intervals": []},
        gaze_heatmap={"grid": [[0] * 5] * 5, "off_screen_timeline": [0.0]},
        flagged_intervals=[{"start_ms": 1000, "end_ms": 3200, "kind": "off_screen_sustained", "confidence": 0.65}],
        gaze_signal_quality="good", unscorable_pct=0.05,
    )
    assert r.model_dump(mode="json")["risk_band"] == "medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_service_read.py -v`
Expected: FAIL — `ModuleNotFoundError: app.modules.vision.schemas`

- [ ] **Step 3a: Write `schemas.py`**

```python
# app/modules/vision/schemas.py
from __future__ import annotations

from pydantic import BaseModel, Field


class ProctoringAnalysisRead(BaseModel):
    """Report-page payload. `status='absent'` when no analysis row exists."""

    status: str  # absent | pending | running | ready | failed | unscorable
    risk_band: str | None = None
    detector_summary: dict | None = None
    gaze_heatmap: dict | None = None
    flagged_intervals: list[dict] = Field(default_factory=list)
    gaze_signal_quality: str | None = None
    unscorable_pct: float | None = None
```

- [ ] **Step 3b: Write `service.py`**

```python
# app/modules/vision/service.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.vision.models import SessionProctoringAnalysis
from app.modules.vision.schemas import ProctoringAnalysisRead


async def get_session_proctoring_analysis(
    db: AsyncSession, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> ProctoringAnalysisRead:
    """Tenant-scoped read. Returns status='absent' when no row exists."""
    row = (
        await db.execute(
            select(SessionProctoringAnalysis).where(
                SessionProctoringAnalysis.session_id == session_id,
                SessionProctoringAnalysis.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return ProctoringAnalysisRead(status="absent")
    return ProctoringAnalysisRead(
        status=row.status,
        risk_band=row.risk_band,
        detector_summary=row.detector_summary,
        gaze_heatmap=row.gaze_heatmap,
        flagged_intervals=row.flagged_intervals or [],
        gaze_signal_quality=row.gaze_signal_quality,
        unscorable_pct=float(row.unscorable_pct) if row.unscorable_pct is not None else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_service_read.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/service.py app/modules/vision/schemas.py tests/vision/test_service_read.py
git commit -m "feat(vision): proctoring analysis read service + schema"
```

---

## Task 12: Module public API

**Files:**
- Modify: `app/modules/vision/__init__.py`
- Test: `tests/vision/test_public_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_public_api.py
def test_public_api_exports():
    from app.modules.vision import (
        analyze_session_proctoring,
        get_session_proctoring_analysis,
        ProctoringAnalysisRead,
        SessionProctoringAnalysis,
    )
    assert analyze_session_proctoring is not None
    assert get_session_proctoring_analysis is not None
    assert ProctoringAnalysisRead is not None
    assert SessionProctoringAnalysis is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_public_api.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Write `__init__.py`**

```python
# app/modules/vision/__init__.py
"""Vision proctoring (server plane) — public API.

Heavy deps (onnxruntime/cv2/uniface) are imported lazily inside the actor/estimator, so
importing this package in the lean nexus API process is safe.
"""
from app.modules.vision.actors import analyze_session_proctoring
from app.modules.vision.models import SessionProctoringAnalysis
from app.modules.vision.schemas import ProctoringAnalysisRead
from app.modules.vision.service import get_session_proctoring_analysis

__all__ = [
    "ProctoringAnalysisRead",
    "SessionProctoringAnalysis",
    "analyze_session_proctoring",
    "get_session_proctoring_analysis",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_public_api.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/__init__.py tests/vision/test_public_api.py
git commit -m "feat(vision): module public API"
```

---

## Task 13: Enqueue trigger on recording-ready

**Files:**
- Modify: `app/modules/session/recording.py`
- Test: `tests/vision/test_recording_enqueue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_recording_enqueue.py
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.modules.session import recording as rec


def test_enqueue_on_ready(monkeypatch):
    sess = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        recording_status="ready", recording_s3_key="sessions/x/r.mp4",
    )
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    rec._maybe_enqueue_vision(sess)
    sent.assert_called_once_with(str(sess.id), str(sess.tenant_id))


def test_no_enqueue_when_not_ready(monkeypatch):
    sess = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                           recording_status="recording", recording_s3_key=None)
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    rec._maybe_enqueue_vision(sess)
    sent.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_recording_enqueue.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_maybe_enqueue_vision'`

- [ ] **Step 3: Wire the trigger in `recording.py`**

Add near the top-level helpers (the actor import is light — safe in the API process):

```python
def _enqueue_vision_analysis(session_id: str, tenant_id: str) -> None:
    # Imported here (not module top) to keep the import graph obviously light
    # and to make monkeypatching in tests trivial.
    from app.modules.vision import analyze_session_proctoring

    analyze_session_proctoring.send(session_id, tenant_id)


def _maybe_enqueue_vision(sess: Session) -> None:
    """Best-effort: enqueue post-session vision analysis once the recording is
    ready. The actor is idempotent (its own status row), so re-enqueue on every
    report read is safe. Never raises into the playback path.
    """
    if sess.recording_status != "ready" or not sess.recording_s3_key:
        return
    try:
        _enqueue_vision_analysis(str(sess.id), str(sess.tenant_id))
    except Exception:  # noqa: BLE001
        log.warning("recording.vision_enqueue_failed", session_id=str(sess.id), exc_info=True)
```

Then call it inside `get_session_recording_playback`, immediately after `await _reconcile(db, sess)` (line ~144):

```python
    await _reconcile(db, sess)
    _maybe_enqueue_vision(sess)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_recording_enqueue.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/session/recording.py tests/vision/test_recording_enqueue.py
git commit -m "feat(vision): enqueue analysis when recording becomes ready (idempotent)"
```

---

## Task 14: Report endpoint `GET /api/reports/session/{id}/proctoring`

**Files:**
- Modify: `app/modules/reporting/router.py`
- Test: `tests/vision/test_proctoring_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/vision/test_proctoring_endpoint.py
import uuid

import pytest


@pytest.mark.asyncio
async def test_proctoring_endpoint_absent(client, auth_headers_factory):
    # Reuse the reporting test fixtures (auth + tenant). A session with no
    # analysis row returns status='absent' with 200.
    headers = auth_headers_factory(permissions={"reports.view"})
    resp = await client.get(f"/api/reports/session/{uuid.uuid4()}/proctoring", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "absent"
```

> If the reporting tests use different fixture names, mirror the exact fixtures used by `tests/.../test_*recording*` for the `/recording` endpoint — copy that test's setup verbatim and only change the URL suffix to `/proctoring` and the expected body.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_proctoring_endpoint.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add the endpoint**

In `app/modules/reporting/router.py`, add an import near the other module imports:

```python
from app.modules.vision import get_session_proctoring_analysis
```

And add the endpoint after `get_session_recording_endpoint` (mirror its RBAC + 404 pattern):

```python
@router.get(
    "/session/{session_id}/proctoring",
    summary="Get the post-session vision proctoring analysis (evidence for review)",
)
async def get_session_proctoring_endpoint(
    session_id: uuid_mod.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Return `{status, risk_band?, detector_summary?, gaze_heatmap?,
    flagged_intervals[], gaze_signal_quality?, unscorable_pct?}`. Evidence for
    human review — never an auto-decision. RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)
    analysis = await get_session_proctoring_analysis(
        db, session_id=session_id, tenant_id=user.user.tenant_id
    )
    return analysis.model_dump(mode="json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_proctoring_endpoint.py -v` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/reporting/router.py tests/vision/test_proctoring_endpoint.py
git commit -m "feat(vision): GET /api/reports/session/{id}/proctoring endpoint"
```

---

## Task 15: Worker registration + infra (vision image + service)

**Files:**
- Modify: `app/worker.py`, `pyproject.toml`, `docker-compose.yml`
- Create: `Dockerfile.vision`
- No unit test (infra). Verified by starting the service.

- [ ] **Step 1: Register the actor in `app/worker.py`**

After the reporting actors import, add:

```python
# Phase 3D vision — post-session proctoring analysis actor (vision queue).
from app.modules.vision import actors as _vision_actors  # noqa: F401, E402
```

- [ ] **Step 2: Add the vision dependency group to `pyproject.toml`**

Add an optional group (ONNX inference — **no torch**, so the image stays far lighter than a torch build):

```toml
[project.optional-dependencies]
vision = [
    "onnxruntime>=1.17",
    "opencv-python-headless>=4.9",
    "numpy>=1.26",
    "uniface[cpu]>=0.1",
]
```

> Verify the exact `uniface` extras/version against PyPI when implementing (the package provides RetinaFace ONNX face detection and auto-downloads its model on first use). If `uniface[cpu]` doesn't resolve, use plain `uniface` + `onnxruntime`.

- [ ] **Step 3: Create `Dockerfile.vision`**

```dockerfile
# Dockerfile.vision — image for the nexus-vision-worker only.
# ONNX gaze inference (onnxruntime + uniface RetinaFace + opencv + ffmpeg).
# No torch — much lighter than a torch build.
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir -e ".[vision]"
COPY . .

CMD ["dramatiq", "app.worker", "--processes", "1", "--threads", "2", "-Q", "vision"]
```

> If the project installs via `uv`, mirror the install step from the main `Dockerfile` and add `--extra vision`. Match whatever the main `Dockerfile` does — only the apt packages + the `[vision]` extra + the `-Q vision` command differ.

- [ ] **Step 4: Add the `nexus-vision-worker` service to `docker-compose.yml`**

First, **download the v1 weights** to a host path (operator step, one-time — these are the NON-COMMERCIAL Gaze360 weights, dev/POC only):
```bash
mkdir -p backend/nexus/models
curl -L -o backend/nexus/models/resnet34_gaze.onnx \
  https://github.com/yakhyo/gaze-estimation/releases/download/weights/resnet34_gaze.onnx
```
(Add `backend/nexus/models/` to `.gitignore` — do NOT commit the weights.)

Append under `services:` (mirror `nexus-worker`'s env, but build from `Dockerfile.vision`, mount the weights + a uniface model cache, and pin `VISION_GAZE_WEIGHTS_PATH`):

```yaml
  nexus-vision-worker:
    build:
      context: .
      dockerfile: Dockerfile.vision
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@host.docker.internal:54322/postgres
      - REDIS_URL=redis://redis:6379/0
      - VISION_GAZE_WEIGHTS_PATH=/weights/resnet34_gaze.onnx
      # uniface auto-downloads its RetinaFace model here (persisted via the volume below).
      - UNIFACE_CACHE_DIR=/uniface-cache
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - .:/app
      # Mount the NON-COMMERCIAL Gaze360 ONNX weights read-only (dev/POC only).
      - ./models:/weights:ro
      - uniface-cache:/uniface-cache
    command: dramatiq app.worker --processes 1 --threads 2 -Q vision
```

Add `uniface-cache:` to the top-level `volumes:` block in `docker-compose.yml` (alongside `redisdata`, `hf-cache`).

- [ ] **Step 5: Verify the service starts + commit**

Run: `docker compose build nexus-vision-worker` → Expected: builds (onnxruntime + uniface install; no torch).
Run: `docker compose up -d nexus-vision-worker && docker compose logs --tail=20 nexus-vision-worker` → Expected: worker boots, connects to Redis, registers the `vision` queue (no traceback).
Run (confirm the external APIs resolve in the image): `docker compose run --rm nexus-vision-worker python -c "from uniface.detection import RetinaFace; import onnxruntime; print('ok')"` → Expected: prints `ok` (adapt the `MobileGazeEstimator` import path in Task 8 if this differs).
Run (regression — lean image still imports the actor for `.send()` without onnxruntime/torch): `docker compose run --rm nexus python -c "import app.modules.vision; import app.modules.session.recording; print('ok')"` → Expected: prints `ok`.

```bash
git add app/worker.py pyproject.toml Dockerfile.vision docker-compose.yml .gitignore
git commit -m "feat(vision): nexus-vision-worker service + ONNX vision deps (-Q vision)"
```

---

## Task 16: Frontend — API client + hook

**Files:**
- Modify: `frontend/app/lib/api/reports.ts`
- Create: `frontend/app/lib/hooks/use-session-proctoring.ts`
- Test: `frontend/app/tests/api/proctoring.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/app/tests/api/proctoring.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { reportsApi } from '@/lib/api/reports'
import * as client from '@/lib/api/client'

describe('reportsApi.getProctoring', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('calls the proctoring endpoint with the token', async () => {
    const spy = vi.spyOn(client, 'apiFetch').mockResolvedValue({ status: 'absent', flagged_intervals: [] })
    await reportsApi.getProctoring('tok', 'sess-1')
    expect(spy).toHaveBeenCalledWith(
      '/api/reports/session/sess-1/proctoring',
      expect.objectContaining({ token: 'tok' }),
    )
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- proctoring`
Expected: FAIL — `getProctoring` is not a function.

- [ ] **Step 3: Add types + fetcher to `lib/api/reports.ts`**

After the `RecordingPlayback` interface, add:

```typescript
export type ProctoringStatus =
  | 'absent' | 'pending' | 'running' | 'ready' | 'failed' | 'unscorable'
export type RiskBand = 'low' | 'medium' | 'high' | 'insufficient_data'

export interface ProctoringFlaggedInterval {
  start_ms: number
  end_ms: number
  kind: string
  confidence: number
}

export interface ProctoringDetectorSummary {
  off_screen_pct: number
  down_glance_count: number
  reading_sweep_intervals: number
  max_faces: number
  multi_face_intervals: { start_ms: number; end_ms: number; max_faces: number }[]
}

export interface ProctoringHeatmap {
  grid: number[][]
  scorable_frames?: number
  off_screen_timeline: number[]
}

export interface ProctoringAnalysis {
  status: ProctoringStatus
  risk_band: RiskBand | null
  detector_summary: ProctoringDetectorSummary | null
  gaze_heatmap: ProctoringHeatmap | null
  flagged_intervals: ProctoringFlaggedInterval[]
  gaze_signal_quality: string | null
  unscorable_pct: number | null
}
```

Inside the `reportsApi` object, after `getRecording`, add:

```typescript
  getProctoring: (
    token: string,
    sessionId: string,
    opts?: { signal?: AbortSignal },
  ): Promise<ProctoringAnalysis> =>
    apiFetch<ProctoringAnalysis>(
      `/api/reports/session/${sessionId}/proctoring`,
      { token, signal: opts?.signal },
    ),
```

- [ ] **Step 4: Create the hook + run test**

```typescript
// frontend/app/lib/hooks/use-session-proctoring.ts
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type ProctoringAnalysis } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Fetch a session's post-session vision proctoring analysis. Polls every 5s
 * while still pending/running (the actor runs offline), then stops.
 */
export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  return useQuery<ProctoringAnalysis>({
    queryKey: ['session-proctoring', sessionId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.getProctoring(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) =>
      q.state.data?.status === 'pending' || q.state.data?.status === 'running' ? 5000 : false,
    refetchOnWindowFocus: true,
  })
}
```

Run: `cd frontend/app && npm run test -- proctoring` → Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/lib/hooks/use-session-proctoring.ts frontend/app/tests/api/proctoring.test.ts
git commit -m "feat(reports): proctoring analysis API client + hook"
```

---

## Task 17: Frontend — `ProctoringIntegrityPanel` + ReportView/SessionPlayback wiring

**Files:**
- Create: `frontend/app/components/dashboard/reports/ProctoringIntegrityPanel.tsx`
- Modify: `frontend/app/components/dashboard/reports/SessionPlayback.tsx`, `frontend/app/components/dashboard/reports/ReportView.tsx`
- Test: `frontend/app/tests/components/ProctoringIntegrityPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/ProctoringIntegrityPanel.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { ProctoringIntegrityPanel } from '@/components/dashboard/reports/ProctoringIntegrityPanel'
import * as hook from '@/lib/hooks/use-session-proctoring'

function mockAnalysis(over = {}) {
  return {
    status: 'ready', risk_band: 'medium',
    detector_summary: { off_screen_pct: 0.12, down_glance_count: 4, reading_sweep_intervals: 1, max_faces: 1, multi_face_intervals: [] },
    gaze_heatmap: { grid: Array.from({ length: 5 }, () => [0, 0, 1, 0, 0]), off_screen_timeline: [0.1] },
    flagged_intervals: [{ start_ms: 3000, end_ms: 5000, kind: 'off_screen_sustained', confidence: 0.65 }],
    gaze_signal_quality: 'good', unscorable_pct: 0.05, ...over,
  }
}

describe('ProctoringIntegrityPanel', () => {
  it('renders the band + "for review, not a decision" disclaimer and fires onSeek', () => {
    vi.spyOn(hook, 'useSessionProctoring').mockReturnValue({ data: mockAnalysis(), isLoading: false } as never)
    const onSeek = vi.fn()
    render(<ProctoringIntegrityPanel sessionId="s1" onSeek={onSeek} />)
    expect(screen.getByText(/for review/i)).toBeTruthy()
    expect(screen.getByText(/MEDIUM/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /jump to/i }))
    expect(onSeek).toHaveBeenCalledWith(3000)
  })

  it('shows insufficient-data state without a scary band', () => {
    vi.spyOn(hook, 'useSessionProctoring').mockReturnValue({
      data: mockAnalysis({ risk_band: 'insufficient_data', status: 'unscorable', gaze_signal_quality: 'unscorable' }),
      isLoading: false,
    } as never)
    render(<ProctoringIntegrityPanel sessionId="s1" onSeek={() => {}} />)
    expect(screen.getByText(/insufficient/i)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/app && npm run test -- ProctoringIntegrityPanel`
Expected: FAIL — module not found.

- [ ] **Step 3a: Add an imperative seek handle to `SessionPlayback.tsx`**

Change the component signature + register the seek function. Replace the `export function SessionPlayback({ sessionId }: { sessionId: string | null })` line and add a `seekApiRef` prop:

```tsx
import type { MutableRefObject } from 'react'

export interface PlaybackSeekApi { seekToMs: (ms: number) => void }

export function SessionPlayback({
  sessionId,
  seekApiRef,
}: {
  sessionId: string | null
  seekApiRef?: MutableRefObject<PlaybackSeekApi | null>
}) {
```

Then, after the existing `seekTo` function, register the imperative handle:

```tsx
  useEffect(() => {
    if (!seekApiRef) return
    seekApiRef.current = {
      seekToMs: (ms: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = Math.max(0, (ms + offsetMs) / 1000)
        void v.play?.()
      },
    }
    return () => {
      if (seekApiRef) seekApiRef.current = null
    }
  }, [seekApiRef, offsetMs])
```

(The existing transcript `seekTo` is unchanged; this adds a ms-based handle the proctoring panel drives.)

- [ ] **Step 3b: Create `ProctoringIntegrityPanel.tsx`**

```tsx
// frontend/app/components/dashboard/reports/ProctoringIntegrityPanel.tsx
'use client'

import { useState } from 'react'

import { Dialog, DialogContent, DialogTitle } from '@/components/px'
import type { ProctoringAnalysis, RiskBand } from '@/lib/api/reports'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'

const CARD = 'rounded-xl border bg-white p-3.5'

function fmtTime(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const BAND_LABEL: Record<RiskBand, string> = {
  low: 'LOW', medium: 'MEDIUM', high: 'HIGH', insufficient_data: 'INSUFFICIENT DATA',
}
function bandColor(b: RiskBand | null): string {
  if (b === 'high') return '#b4232a'
  if (b === 'medium') return '#b87503'
  if (b === 'insufficient_data') return '#5b6b73'
  return '#2f7d4f'
}

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export function ProctoringIntegrityPanel({
  sessionId,
  onSeek,
}: {
  sessionId: string | null
  onSeek: (ms: number) => void
}) {
  const { data, isLoading } = useSessionProctoring(sessionId ?? '')
  const [open, setOpen] = useState(false)

  if (!sessionId || isLoading) {
    return <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <Header />
      <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>Loading…</p>
    </div>
  }
  if (!data || data.status === 'absent' || data.status === 'pending' || data.status === 'running') {
    return <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <Header />
      <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>
        {data?.status === 'pending' || data?.status === 'running'
          ? 'Analyzing the recording…' : 'No proctoring analysis for this session.'}
      </p>
    </div>
  }
  if (data.status === 'failed') {
    return <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <Header />
      <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>Analysis unavailable for this session.</p>
    </div>
  }

  const band = data.risk_band
  const top = [...data.flagged_intervals].slice(0, 3)

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <Header />
      <div className="mt-2 flex items-center gap-2">
        <span className="rounded-md px-2 py-0.5 text-[11px] font-bold text-white" style={{ background: bandColor(band) }}>
          {band ? BAND_LABEL[band] : '—'}
        </span>
        <span className="text-[10px]" style={{ color: '#7d929c' }}>for review, not a decision</span>
      </div>
      <p className="mt-1 text-[10.5px]" style={{ color: '#8aa0ac' }}>
        signal quality: {data.gaze_signal_quality ?? 'n/a'}
      </p>

      {top.length > 0 && (
        <ul className="mt-2 space-y-1">
          {top.map((iv, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => onSeek(iv.start_ms)}
                className="w-full rounded-md px-2 py-1 text-left text-[11px] transition-colors hover:bg-[var(--px-ai-bg)]"
              >
                <span className="font-semibold">{KIND_LABEL[iv.kind] ?? iv.kind}</span>
                <span className="ml-1" style={{ color: 'var(--px-ai)' }}>· jump to {fmtTime(iv.start_ms)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 text-[11px] font-semibold"
        style={{ color: 'var(--px-ai)' }}
      >
        View full proctoring detail →
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogTitle>Proctoring & Integrity — detail</DialogTitle>
          <DetectorBreakdown data={data} />
          <Heatmap heatmap={data.gaze_heatmap} />
          <FlaggedList intervals={data.flagged_intervals} onSeek={(ms) => { onSeek(ms); setOpen(false) }} />
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Header() {
  return (
    <div className="flex items-center justify-between">
      <h3 className="text-[12px] font-semibold">Proctoring & Integrity</h3>
      <span className="text-[9.5px] uppercase tracking-wide" style={{ color: '#9fb2bc' }}>evidence</span>
    </div>
  )
}

function DetectorBreakdown({ data }: { data: ProctoringAnalysis }) {
  const s = data.detector_summary
  if (!s) return null
  const rows: [string, string][] = [
    ['Off-screen', `${Math.round(s.off_screen_pct * 100)}% of session`],
    ['Down-glances', String(s.down_glance_count)],
    ['Reading sweeps', String(s.reading_sweep_intervals)],
    ['Max faces', String(s.max_faces)],
    ['Signal quality', data.gaze_signal_quality ?? 'n/a'],
  ]
  return (
    <table className="mt-2 w-full text-[12px]">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td className="py-0.5 pr-4 text-[#5b6b73]">{k}</td><td className="py-0.5 font-medium">{v}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function Heatmap({ heatmap }: { heatmap: ProctoringAnalysis['gaze_heatmap'] }) {
  if (!heatmap?.grid?.length) return null
  const flat = heatmap.grid.flat()
  const max = Math.max(1, ...flat)
  return (
    <div className="mt-3">
      <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide" style={{ color: '#8aa0ac' }}>
        Gaze heatmap (relative to baseline)
      </p>
      <div className="inline-grid gap-0.5" style={{ gridTemplateColumns: `repeat(${heatmap.grid[0].length}, 22px)` }}>
        {heatmap.grid.map((row, y) =>
          row.map((c, x) => (
            <div key={`${x}-${y}`} title={`${c}`} style={{
              width: 22, height: 22, borderRadius: 3,
              background: `rgba(31,125,79,${c / max})`, border: '1px solid rgba(0,0,0,0.06)',
            }} />
          )),
        )}
      </div>
      <p className="mt-1 text-[10px]" style={{ color: '#9fb2bc' }}>Center = looking at screen. Brighter = more time.</p>
    </div>
  )
}

function FlaggedList({ intervals, onSeek }: {
  intervals: ProctoringAnalysis['flagged_intervals']
  onSeek: (ms: number) => void
}) {
  if (!intervals.length) return <p className="mt-3 text-[11px]" style={{ color: '#7d929c' }}>No flagged moments.</p>
  return (
    <ul className="mt-3 max-h-[220px] space-y-1 overflow-y-auto">
      {intervals.map((iv, i) => (
        <li key={i}>
          <button type="button" onClick={() => onSeek(iv.start_ms)}
            className="w-full rounded-md px-2 py-1 text-left text-[11.5px] hover:bg-[var(--px-ai-bg)]">
            <span className="font-semibold">{KIND_LABEL[iv.kind] ?? iv.kind}</span>
            <span className="ml-1" style={{ color: 'var(--px-ai)' }}>· jump to {fmtTime(iv.start_ms)}</span>
            <span className="ml-1" style={{ color: '#9fb2bc' }}>({fmtTime(iv.start_ms)}–{fmtTime(iv.end_ms)})</span>
          </button>
        </li>
      ))}
    </ul>
  )
}
```

> Verify the `Dialog`/`DialogContent`/`DialogTitle` export names against `components/px/index.ts` before running — if the barrel exports a different shape (e.g. `Dialog.Root`), adapt the JSX to match. Do not invent a new dialog primitive.

- [ ] **Step 3c: Wire into `ReportView.tsx`**

Add imports + a shared seek ref, render the panel in the right sidebar, and pass the ref to `SessionPlayback`:

```tsx
import { useRef } from 'react'
import { ProctoringIntegrityPanel } from './ProctoringIntegrityPanel'
import type { PlaybackSeekApi } from './SessionPlayback'
```

Inside `ReportView`, before the `return`:

```tsx
  const seekApiRef = useRef<PlaybackSeekApi | null>(null)
  const handleSeek = (ms: number) => seekApiRef.current?.seekToMs(ms)
```

Change the playback node to pass the ref:

```tsx
            <SessionPlayback key="p" sessionId={report.session_id} seekApiRef={seekApiRef} />,
```

Add the panel to the right-column array (after `ScoresCard`, before `HumanDecisionPanel`):

```tsx
            <ProctoringIntegrityPanel key="proctoring" sessionId={report.session_id} onSeek={handleSeek} />,
```

- [ ] **Step 4: Run tests + type-check**

Run: `cd frontend/app && npm run test -- ProctoringIntegrityPanel` → Expected: PASS
Run: `cd frontend/app && npm run type-check` → Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/reports/ProctoringIntegrityPanel.tsx frontend/app/components/dashboard/reports/SessionPlayback.tsx frontend/app/components/dashboard/reports/ReportView.tsx frontend/app/tests/components/ProctoringIntegrityPanel.test.tsx
git commit -m "feat(reports): Proctoring & Integrity sidebar panel + jump-to-timestamp"
```

---

## Task 18: Pre-production docs (DPIA + bias-review + threat model)

**Files:**
- Create: `docs/security/2026-05-30-vision-proctoring-dpia.md`
- Modify: `docs/security/threat-model.md`
- No test (docs). These are spec §16.8/§10 pre-prod action items — write them now so they are not dropped.

- [ ] **Step 1: Write the DPIA + bias-review note**

Create `docs/security/2026-05-30-vision-proctoring-dpia.md` covering: data processed (recording = already-consented biometric video; derived gaze features only, no templates/frames stored — spec §16.6/D6); lawful basis + BIPA/GDPR special-category consent (consent-gated, versioned); purpose limitation (evidence for human review, never auto-reject — D1); retention (features tied to the session record; deletion on candidate-data deletion); **bias-review obligation** (gaze/face models have demographic performance gaps — skin tone, glasses; band thresholds carry a documented review obligation; `insufficient_data` + `gaze_signal_quality` prevent confident flags on unseeable frames); **open GA blocker**: NC Gaze360 weights must be replaced before commercial GA (spec §16.8).

- [ ] **Step 2: Update the threat model**

In `docs/security/threat-model.md`, add the new data path: R2 recording → `vision-worker` (downloads, samples, analyzes) → `session_proctoring_analysis` (tenant-scoped, RLS) → report endpoint (reports.view-gated). Note: the worker reads cross-tenant via bypass-RLS but filters every query by explicit `tenant_id`; features-only storage; no new external sub-processor (all in-VPC/compute).

- [ ] **Step 3: Commit**

```bash
git add docs/security/2026-05-30-vision-proctoring-dpia.md docs/security/threat-model.md
git commit -m "docs(security): vision-proctoring DPIA + bias-review + threat-model update"
```

---

## Final verification

- [ ] **Backend test suite (vision module):**

Run: `docker compose run --rm nexus pytest tests/vision -v` → Expected: all PASS.

- [ ] **Backend boot (RLS completeness covers the new table):**

Run: `docker compose up -d nexus && docker compose logs --tail=30 nexus | grep rls.completeness` → Expected: `rls.completeness_check_ok` (not `_failed`).

- [ ] **Frontend:**

Run: `cd frontend/app && npm run test && npm run type-check && npm run lint` → Expected: all green.

- [ ] **Manual end-to-end (per D9):** with `nexus-vision-worker` up and `VISION_GAZE_WEIGHTS_PATH` mounted, run a real/self-recorded session, open the report page (triggers the enqueue), wait for the actor, and confirm the panel renders a band + flagged moments + heatmap and that "jump to" seeks the player. Try a glasses/dark clip → expect `gaze_signal_quality` degraded / `insufficient_data`.

---

## Self-review notes (author)

- **Spec coverage:** §16.2 (Task 2, 8), §16.3 detectors (Tasks 3–5), §16.4 actor/pipeline (Tasks 9, 10), §16.5 band (Task 5), §16.6 table+RLS (Task 6), §16.7 trigger+endpoint+UI (Tasks 13, 14, 17), §16.8 NC-weights-open + DPIA (Tasks 15, 18), §16.9 module/infra (Tasks 11, 12, 15), §16.10 tests (each task + final), §16.11 build order (task order).
- **Deferred correctly:** liveness/tamper/learned-scoring — no task (pass 2).
- **Type consistency:** `FrameObservation`, `Interval`, `AnalysisResult`, `FaceGaze`, `GazeEstimator`, `ProctoringAnalysisRead`/`ProctoringAnalysis`, `PlaybackSeekApi` are defined before use and referenced consistently across tasks.
- **Known adaptation points flagged inline:** exact reporting test-fixture names (Task 14), `px` Dialog export shape (Task 17), `uv` vs pip in Dockerfile (Task 15). These are codebase-specific and must be matched, not guessed.
