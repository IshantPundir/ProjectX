# Vision Proctoring Performance Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make post-session vision proctoring bounded and scalable on CPU — cap onnxruntime thread fan-out, sample at 2 fps with an adaptive frame budget, downscale + ffmpeg-decode, load the model once per process, and run N single-threaded inference processes under a hard CPU cap — so it can never peg the host again.

**Architecture:** Four independent bounds + a backstop. (1) Bound fan-out: onnxruntime `intra_op=1` on the gaze session + a `cpus` cgroup cap on the worker. (2) Bound work: 2 fps, ~2000-frame adaptive-stride budget, 960px pre-detect downscale, ffmpeg-based decode (replaces the full-decode OpenCV loop). (3) Process-level estimator singleton. (4) `--processes N --threads 1` worker, scale by replicas. All knobs are env-driven `pydantic-settings` fields. The `GazeEstimator` seam is untouched so a future GPU estimator drops in.

**Tech Stack:** Python 3.13, onnxruntime (CPU), uniface RetinaFace, ffmpeg/ffprobe (already in `Dockerfile.vision`), numpy, Dramatiq, pytest. Spec: `docs/superpowers/specs/2026-06-01-vision-proctoring-perf-design.md`.

---

## File Structure

**New files:**
- `app/modules/vision/sampler.py` — frame sampling. Pure functions (`effective_fps`, `scaled_dimensions`, `build_ffprobe_cmd`, `build_ffmpeg_cmd`, `parse_probe_json`) + the ffmpeg/ffprobe I/O generator (`sample_frames`). One responsibility: turn a video path + budget into `(t_ms, frame_bgr)` pairs. Lazy heavy imports (numpy/subprocess), mirroring `analysis.py` discipline.
- `tests/vision/test_sampler.py` — unit tests for the pure functions (no ffmpeg/numpy needed).

**Modified files:**
- `app/config.py` — change `vision_sample_fps` default to 2.0; add `vision_max_frames`, `vision_max_frame_width`, `vision_ort_intra_op_threads`, `vision_worker_concurrency`.
- `app/modules/vision/config.py` — expose the new settings as `VisionConfig` properties.
- `app/modules/vision/analysis.py` — `run_analysis` calls the new sampler; delete the old `_sample_frames`.
- `app/modules/vision/gaze/mobilegaze.py` — accept `intra_op_threads`, build `SessionOptions`, apply to the gaze session + (capability-probed) to RetinaFace.
- `app/modules/vision/actors.py` — process-level lazy estimator singleton (`_get_estimator`) replaces per-call construction.
- `Dockerfile.vision` — `ENV OMP_NUM_THREADS=1 …`; default CMD to `--processes 4 --threads 1`.
- `docker-compose.yml` — `nexus-vision-worker`: add `cpus` cap; command uses `VISION_WORKER_CONCURRENCY`.
- `.env.example` — documented bounded-CPU block.
- `tests/vision/test_vision_config.py` — update `sample_fps` expectation (5.0 → 2.0) + assert new defaults.

**Out of scope (per spec §8):** GPU/batch, coarse+escalate, report-page re-enqueue guard, gaze input size, flag/band thresholds, the candidate-reel branch.

---

## Task 1: Config settings (env-driven knobs)

**Files:**
- Modify: `app/config.py:595` (+ insert new fields after it)
- Modify: `app/modules/vision/config.py:26-27` (add properties near `sample_fps`)
- Test: `tests/vision/test_vision_config.py`

- [ ] **Step 1: Update the failing config test**

Replace the two `sample_fps == 5.0` assertions and add the new-default assertions. In `tests/vision/test_vision_config.py`, change `test_vision_config_reads_settings` to set `VISION_SAMPLE_FPS` to `"2.0"` and assert `cfg.sample_fps == 2.0`, and replace `test_vision_config_defaults` body's `assert cfg.sample_fps == 5.0` with the block below. Add a new test:

```python
def test_vision_config_bounded_cpu_defaults():
    cfg = VisionConfig(Settings())
    assert cfg.sample_fps == 2.0
    assert cfg.max_frames == 2000
    assert cfg.max_frame_width == 960
    assert cfg.ort_intra_op_threads == 1


def test_vision_config_bounded_cpu_env_override(monkeypatch):
    monkeypatch.setenv("VISION_MAX_FRAMES", "1500")
    monkeypatch.setenv("VISION_MAX_FRAME_WIDTH", "640")
    monkeypatch.setenv("VISION_ORT_INTRA_OP_THREADS", "2")
    cfg = VisionConfig(Settings())
    assert cfg.max_frames == 1500
    assert cfg.max_frame_width == 640
    assert cfg.ort_intra_op_threads == 2
```

Also in `test_vision_config_reads_settings`, change:
```python
    monkeypatch.setenv("VISION_SAMPLE_FPS", "2.0")
    ...
    assert cfg.sample_fps == 2.0
```
And in `test_vision_config_defaults`, change `assert cfg.sample_fps == 5.0` → `assert cfg.sample_fps == 2.0`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/vision/test_vision_config.py -v`
Expected: FAIL — `AttributeError: 'VisionConfig' object has no attribute 'max_frames'` and the default 2.0 assertions fail (still 5.0).

- [ ] **Step 3: Change `vision_sample_fps` default + add new Settings fields**

In `app/config.py`, change line 595:
```python
    vision_sample_fps: float = 2.0  # was 5.0 — see 2026-06-01 perf design
```
Immediately after it (before the `vision_zone_*` block), insert:
```python
    # Bounded-CPU work limits (2026-06-01 perf design). All env-overridable.
    # Hard frame budget per session: effective fps degrades to a wider uniform
    # stride on long recordings so worst-case cost is bounded regardless of length.
    vision_max_frames: int = 2000
    # Pre-detection downscale: cap frame width (px) before RetinaFace + gaze.
    vision_max_frame_width: int = 960
    # onnxruntime intra-op threads PER inference. Keep at 1 — parallelism comes
    # from worker process concurrency, NOT per-call fan-out (the 2026-06-01 peg).
    vision_ort_intra_op_threads: int = 1
    # Vision worker inference process count (Dramatiq --processes). Match to the
    # worker's cpus cap; scale throughput via replicas, not by exceeding the cap.
    vision_worker_concurrency: int = 4
```

- [ ] **Step 4: Expose the new settings on `VisionConfig`**

In `app/modules/vision/config.py`, after the `sample_fps` property (line 27), add:
```python
    @property
    def max_frames(self) -> int:
        return self._s.vision_max_frames

    @property
    def max_frame_width(self) -> int:
        return self._s.vision_max_frame_width

    @property
    def ort_intra_op_threads(self) -> int:
        return self._s.vision_ort_intra_op_threads

    @property
    def worker_concurrency(self) -> int:
        return self._s.vision_worker_concurrency
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/vision/test_vision_config.py -v`
Expected: PASS (all config tests green).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/modules/vision/config.py tests/vision/test_vision_config.py
git commit -m "feat(vision): add bounded-CPU config knobs, default fps 5->2"
```

---

## Task 2: Pure sampler logic (adaptive stride + ffmpeg command building)

**Files:**
- Create: `app/modules/vision/sampler.py`
- Test: `tests/vision/test_sampler.py`

These functions are pure (no subprocess, no numpy) so they unit-test in the lean `nexus` image.

- [ ] **Step 1: Write the failing tests**

Create `tests/vision/test_sampler.py`:
```python
# tests/vision/test_sampler.py
import pytest

from app.modules.vision.sampler import (
    build_ffmpeg_cmd,
    build_ffprobe_cmd,
    effective_fps,
    parse_probe_json,
    scaled_dimensions,
)


def test_effective_fps_under_budget_uses_target():
    # 16 min at 2 fps = 1920 frames <= 2000 budget → full target fps.
    assert effective_fps(16 * 60, target_fps=2.0, max_frames=2000) == 2.0


def test_effective_fps_over_budget_degrades_uniformly():
    # 40 min at budget 2000 → 2000/2400s = 0.8333... fps.
    eff = effective_fps(40 * 60, target_fps=2.0, max_frames=2000)
    assert eff == pytest.approx(2000 / 2400)
    assert eff < 2.0


def test_effective_fps_zero_or_unknown_duration_falls_back_to_target():
    assert effective_fps(0.0, target_fps=2.0, max_frames=2000) == 2.0
    assert effective_fps(-1.0, target_fps=2.0, max_frames=2000) == 2.0


def test_scaled_dimensions_downscales_wide_and_forces_even():
    # 1280x720 capped to 960 wide → 960x540 (both even).
    assert scaled_dimensions(1280, 720, 960) == (960, 540)


def test_scaled_dimensions_no_upscale_small_source():
    # 640x480 under cap → unchanged (already even).
    assert scaled_dimensions(640, 480, 960) == (640, 480)


def test_scaled_dimensions_rounds_odd_to_even():
    # 963x721 capped to 960 → width even 960, height even.
    out_w, out_h = scaled_dimensions(963, 721, 960)
    assert out_w % 2 == 0 and out_h % 2 == 0


def test_scaled_dimensions_rejects_nonpositive():
    with pytest.raises(ValueError):
        scaled_dimensions(0, 720, 960)


def test_build_ffprobe_cmd_shape():
    cmd = build_ffprobe_cmd("/tmp/rec.mp4")
    assert cmd[0] == "ffprobe"
    assert "/tmp/rec.mp4" in cmd
    assert "-of" in cmd and "json" in cmd


def test_build_ffmpeg_cmd_has_fps_scale_and_rawvideo():
    cmd = build_ffmpeg_cmd("/tmp/rec.mp4", eff_fps=2.0, out_w=960, out_h=540)
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "fps=2.0" in joined or "fps=2.000000" in joined
    assert "scale=960:540" in joined
    assert "rawvideo" in cmd
    assert "bgr24" in cmd
    assert "pipe:1" in cmd


def test_parse_probe_json_extracts_dims_and_duration():
    raw = (
        '{"streams":[{"width":1280,"height":720}],'
        '"format":{"duration":"840.5"}}'
    )
    w, h, dur = parse_probe_json(raw)
    assert (w, h) == (1280, 720)
    assert dur == pytest.approx(840.5)


def test_parse_probe_json_missing_duration_returns_zero():
    raw = '{"streams":[{"width":1280,"height":720}],"format":{}}'
    w, h, dur = parse_probe_json(raw)
    assert (w, h, dur) == (1280, 720, 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/vision/test_sampler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.modules.vision.sampler'`.

- [ ] **Step 3: Write the pure sampler functions**

Create `app/modules/vision/sampler.py`:
```python
# app/modules/vision/sampler.py
"""Frame sampling for vision proctoring — ffmpeg-based, bounded.

Pure functions (effective_fps, scaled_dimensions, build_*_cmd, parse_probe_json)
are import-light and unit-tested in the lean image. `sample_frames` shells out to
ffprobe + ffmpeg (both in Dockerfile.vision) and yields (t_ms, frame_bgr); numpy
imports lazily inside it, mirroring analysis.py discipline.

Design (2026-06-01 perf): replaces the old full-decode OpenCV loop. ffmpeg
decodes keyframe-aware and emits ONLY the sampled, pre-downscaled frames at a
constant effective fps, so index->timestamp is exact and worst-case frame count
is bounded by the budget.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import structlog

log = structlog.get_logger("vision.sampler")


def effective_fps(duration_seconds: float, target_fps: float, max_frames: int) -> float:
    """Sampling fps after applying the frame budget.

    = min(target_fps, max_frames / duration). Long recordings degrade to a wider
    uniform stride instead of being truncated. Unknown/zero duration → target.
    """
    if duration_seconds <= 0 or target_fps <= 0:
        return target_fps
    return min(target_fps, max_frames / duration_seconds)


def scaled_dimensions(src_w: int, src_h: int, max_width: int) -> tuple[int, int]:
    """Output (w, h) preserving aspect, capped to max_width, both forced even.

    Even dims keep ffmpeg's scaler and common pixel formats happy and let us
    compute the exact rawvideo frame byte size up front. No upscaling.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"invalid source dimensions: {src_w}x{src_h}")
    out_w = min(src_w, max_width)
    out_h = round(src_h * out_w / src_w)
    out_w -= out_w % 2
    out_h -= out_h % 2
    return max(2, out_w), max(2, out_h)


def build_ffprobe_cmd(path: str) -> list[str]:
    """ffprobe argv → JSON with the first video stream's width/height + duration."""
    return [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]


def parse_probe_json(raw: str) -> tuple[int, int, float]:
    """(width, height, duration_seconds) from ffprobe JSON. Missing duration → 0.0."""
    doc = json.loads(raw)
    stream = doc["streams"][0]
    w = int(stream["width"])
    h = int(stream["height"])
    dur_raw = doc.get("format", {}).get("duration")
    dur = float(dur_raw) if dur_raw not in (None, "", "N/A") else 0.0
    return w, h, dur


def build_ffmpeg_cmd(path: str, eff_fps: float, out_w: int, out_h: int) -> list[str]:
    """ffmpeg argv → constant-fps, downscaled raw BGR24 frames on stdout."""
    vf = f"fps={eff_fps:.6f},scale={out_w}:{out_h}"
    return [
        "ffmpeg", "-v", "error",
        "-i", path,
        "-vf", vf,
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "pipe:1",
    ]


def sample_frames(
    video_path: str, *, target_fps: float, max_frames: int, max_width: int
) -> Iterator[tuple[int, "object"]]:
    """Yield (t_ms, frame_bgr ndarray) sampled at the budget-bounded effective fps.

    ffprobe for dims+duration → compute eff_fps + output dims → ffmpeg pipe →
    read fixed-size frames. Raises RuntimeError if ffmpeg exits non-zero.
    """
    import subprocess  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    probe = subprocess.run(
        build_ffprobe_cmd(video_path), capture_output=True, text=True, check=True
    )
    src_w, src_h, duration = parse_probe_json(probe.stdout)
    eff = effective_fps(duration, target_fps, max_frames)
    out_w, out_h = scaled_dimensions(src_w, src_h, max_width)
    frame_bytes = out_w * out_h * 3

    log.info(
        "vision.sampler.start", duration_s=round(duration, 1), eff_fps=round(eff, 4),
        out_w=out_w, out_h=out_h, budget=max_frames,
    )

    proc = subprocess.Popen(
        build_ffmpeg_cmd(video_path, eff, out_w, out_h),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    i = 0
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(out_h, out_w, 3)
            yield int(round(i / eff * 1000)), frame
            i += 1
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        ret = proc.wait()
        if ret not in (0, None):
            err = (proc.stderr.read().decode("utf-8", "replace") if proc.stderr else "")[:500]
            log.error("vision.sampler.ffmpeg_failed", returncode=ret, stderr=err)
            raise RuntimeError(f"ffmpeg exited {ret}: {err}")
        if proc.stderr is not None:
            proc.stderr.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/vision/test_sampler.py -v`
Expected: PASS (all 11 pure-function tests).

- [ ] **Step 5: Verify the module stays import-light**

Run: `docker compose run --rm nexus python -c "import sys; import app.modules.vision.sampler; assert 'numpy' not in sys.modules and 'subprocess' not in sys.modules; print('light OK')"`
Expected: prints `light OK` (numpy/subprocess import only inside `sample_frames`).

- [ ] **Step 6: Commit**

```bash
git add app/modules/vision/sampler.py tests/vision/test_sampler.py
git commit -m "feat(vision): ffmpeg frame sampler with adaptive-stride budget + downscale"
```

---

## Task 3: Wire `run_analysis` to the new sampler

**Files:**
- Modify: `app/modules/vision/analysis.py:43-61` (delete `_sample_frames`), `:130-156` (`run_analysis`)
- Test: `tests/vision/test_analysis_sampler_wiring.py` (new)

- [ ] **Step 1: Write the failing test**

The wiring must call `sample_frames` with config-derived params and feed each frame to the estimator. Create `tests/vision/test_analysis_sampler_wiring.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_analysis_sampler_wiring.py -v`
Expected: FAIL — `AttributeError: module 'app.modules.vision.analysis' has no attribute 'sample_frames'`.

- [ ] **Step 3: Replace `_sample_frames` import/loop in `analysis.py`**

In `app/modules/vision/analysis.py`, delete the entire `_sample_frames` function (lines 43-61). Add this import near the top (after line 13):
```python
from app.modules.vision.sampler import sample_frames
```
Then change the frame loop inside `run_analysis` (currently lines 138-140):
```python
    cfg = vision_config
    frames: list[tuple[int, list[FaceGaze]]] = []
    for t_ms, frame in sample_frames(
        local_video_path,
        target_fps=cfg.sample_fps,
        max_frames=cfg.max_frames,
        max_width=cfg.max_frame_width,
    ):
        frames.append((t_ms, estimator.estimate(frame)))
```
(The rest of `run_analysis` — `observations_from_estimates`, `analyze_observations` — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_analysis_sampler_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Confirm no dangling `_sample_frames` references**

Run: `grep -rn "_sample_frames" app/ tests/`
Expected: zero matches.

- [ ] **Step 6: Commit**

```bash
git add app/modules/vision/analysis.py tests/vision/test_analysis_sampler_wiring.py
git commit -m "refactor(vision): run_analysis samples via ffmpeg budget sampler"
```

---

## Task 4: Cap onnxruntime threads in the gaze estimator

**Files:**
- Modify: `app/modules/vision/gaze/mobilegaze.py:26-50`
- Test: `tests/vision/test_gaze_thread_cap.py` (new)

- [ ] **Step 1: Write the failing test**

We assert the constructor accepts `intra_op_threads` and applies it via `SessionOptions`. The test stubs `onnxruntime`/`uniface`/`numpy`/`cv2` in `sys.modules` so it runs in the lean image (no real ONNX). Create `tests/vision/test_gaze_thread_cap.py`:
```python
# tests/vision/test_gaze_thread_cap.py
import sys
import types

import pytest


@pytest.fixture
def stub_heavy_deps(monkeypatch):
    # Minimal stubs so MobileGazeEstimator.__init__ runs without real ONNX.
    captured = {}

    class _SessionOptions:
        def __init__(self):
            self.intra_op_num_threads = None
            self.inter_op_num_threads = None
            self.execution_mode = None

    class _ExecutionMode:
        ORT_SEQUENTIAL = "seq"

    class _InferenceSession:
        def __init__(self, weights_path, sess_options=None, providers=None):
            captured["intra"] = sess_options.intra_op_num_threads
            captured["inter"] = sess_options.inter_op_num_threads
            captured["mode"] = sess_options.execution_mode

        def get_inputs(self):
            return [types.SimpleNamespace(name="in")]

        def get_outputs(self):
            return [types.SimpleNamespace(name="yaw"), types.SimpleNamespace(name="pitch")]

    ort = types.ModuleType("onnxruntime")
    ort.SessionOptions = _SessionOptions
    ort.ExecutionMode = _ExecutionMode
    ort.InferenceSession = _InferenceSession
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    np = types.ModuleType("numpy")
    np.array = lambda *a, **k: types.SimpleNamespace(reshape=lambda *s: None)
    np.arange = lambda *a, **k: None
    np.float32 = "f32"
    monkeypatch.setitem(sys.modules, "numpy", np)

    uniface_det = types.ModuleType("uniface.detection")

    class _RetinaFace:
        def __init__(self):
            pass

    uniface_det.RetinaFace = _RetinaFace
    uniface_pkg = types.ModuleType("uniface")
    monkeypatch.setitem(sys.modules, "uniface", uniface_pkg)
    monkeypatch.setitem(sys.modules, "uniface.detection", uniface_det)
    return captured


def test_gaze_session_thread_capped(stub_heavy_deps):
    from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator

    MobileGazeEstimator(weights_path="/w.onnx", intra_op_threads=1)
    assert stub_heavy_deps["intra"] == 1
    assert stub_heavy_deps["inter"] == 1
    assert stub_heavy_deps["mode"] == "seq"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_thread_cap.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'intra_op_threads'`.

- [ ] **Step 3: Add the thread cap to the estimator**

In `app/modules/vision/gaze/mobilegaze.py`, change the `__init__` signature (line 26-33) to add the parameter:
```python
    def __init__(
        self,
        *,
        weights_path: str,
        input_size: int = 448,
        pitch_sign: int = 1,
        yaw_sign: int = 1,
        intra_op_threads: int = 1,
    ) -> None:
```
Then replace the session construction (lines 35-43) with:
```python
        # Lazy — only the vision-worker image has these installed.
        import inspect  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
        from uniface.detection import RetinaFace  # noqa: PLC0415

        self._np = np
        # Cap per-inference fan-out: one inference must NOT own the box. Throughput
        # comes from worker process concurrency. (The 2026-06-01 peg was uncapped
        # intra-op threads defaulting to the host core count.)
        so = ort.SessionOptions()
        so.intra_op_num_threads = intra_op_threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            weights_path, sess_options=so, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        # RetinaFace (uniface) is onnxruntime-backed too. Pass our capped options
        # if this uniface version accepts them; otherwise the worker's cpus cgroup
        # cap (docker-compose) is the hard backstop on its thread fan-out.
        rf_params = inspect.signature(RetinaFace.__init__).parameters
        if "sess_options" in rf_params:
            self._detector = RetinaFace(sess_options=so)
        elif "session_options" in rf_params:
            self._detector = RetinaFace(session_options=so)
        else:
            self._detector = RetinaFace()
            log.info("vision.gaze.retinaface.uncapped_relying_on_cgroup")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_gaze_thread_cap.py tests/vision/test_gaze_mobilegaze_lazy.py -v`
Expected: PASS (thread-cap test passes; the lazy-import test still passes — `inspect` is stdlib, heavy deps still lazy).

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/gaze/mobilegaze.py tests/vision/test_gaze_thread_cap.py
git commit -m "feat(vision): cap onnxruntime intra/inter-op threads on gaze + retinaface"
```

---

## Task 5: Process-level estimator singleton

**Files:**
- Modify: `app/modules/vision/actors.py:190-220` (`_run` + new module-level helper)
- Test: `tests/vision/test_estimator_singleton.py` (new)

- [ ] **Step 1: Write the failing test**

The estimator must be built once per process and reused. Create `tests/vision/test_estimator_singleton.py`:
```python
# tests/vision/test_estimator_singleton.py
from app.modules.vision import actors as vision_actors


def test_get_estimator_builds_once(monkeypatch):
    vision_actors._estimator = None  # reset process singleton
    calls = {"n": 0}

    class _FakeEstimator:
        def __init__(self, **kwargs):
            calls["n"] += 1

    import app.modules.vision.gaze.mobilegaze as mg
    monkeypatch.setattr(mg, "MobileGazeEstimator", _FakeEstimator)

    a = vision_actors._get_estimator()
    b = vision_actors._get_estimator()
    assert a is b
    assert calls["n"] == 1
    vision_actors._estimator = None  # clean up for other tests


def test_get_estimator_passes_thread_cap(monkeypatch):
    vision_actors._estimator = None
    captured = {}

    class _FakeEstimator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import app.modules.vision.gaze.mobilegaze as mg
    monkeypatch.setattr(mg, "MobileGazeEstimator", _FakeEstimator)

    vision_actors._get_estimator()
    assert captured["intra_op_threads"] == 1  # vision_config.ort_intra_op_threads
    vision_actors._estimator = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/vision/test_estimator_singleton.py -v`
Expected: FAIL — `AttributeError: module 'app.modules.vision.actors' has no attribute '_get_estimator'`.

- [ ] **Step 3: Add the singleton + use it in `_run`**

In `app/modules/vision/actors.py`, add near the top-level (after the `log = ...` line, ~line 34):
```python
import threading  # noqa: E402  (grouped with stdlib above in final form)

# Process-level gaze estimator. Model load + RetinaFace init are costly; build
# ONCE per worker process (was previously rebuilt on every actor call). With
# --threads 1 there is no intra-process race, but the lock keeps it correct if
# concurrency is ever raised.
_estimator = None
_estimator_lock = threading.Lock()


def _get_estimator():
    global _estimator
    if _estimator is None:
        with _estimator_lock:
            if _estimator is None:
                from app.modules.vision.gaze.mobilegaze import (  # noqa: PLC0415
                    MobileGazeEstimator,
                )
                _estimator = MobileGazeEstimator(
                    weights_path=vision_config.gaze_weights_path,
                    input_size=vision_config.gaze_input_size,
                    pitch_sign=vision_config.gaze_pitch_sign,
                    yaw_sign=vision_config.gaze_yaw_sign,
                    intra_op_threads=vision_config.ort_intra_op_threads,
                )
    return _estimator
```
(Move the `import threading` to the stdlib import group at the top — `import os`, `import tempfile`, `import threading`, `import uuid` — and drop the inline `# noqa`.)

Then in `_run`, replace the per-call construction (lines 209-216) — delete the `from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator` import and the `estimator = MobileGazeEstimator(...)` block — with:
```python
        estimator = _get_estimator()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/vision/test_estimator_singleton.py tests/vision/test_actor_idempotency.py -v`
Expected: PASS (singleton tests + existing idempotency tests green).

- [ ] **Step 5: Commit**

```bash
git add app/modules/vision/actors.py tests/vision/test_estimator_singleton.py
git commit -m "perf(vision): load gaze estimator once per worker process"
```

---

## Task 6: Worker shape — CPU cap, concurrency, OMP env

**Files:**
- Modify: `Dockerfile.vision:46`, `:57`
- Modify: `docker-compose.yml:147` (+ add cpus cap)
- Modify: `.env.example:113` (after the thumbnail block)

No new code logic — infra config. Verified by inspection + a worker-boot smoke test.

- [ ] **Step 1: Add OMP env + change default CMD in `Dockerfile.vision`**

In `Dockerfile.vision`, after line 46 (`ENV UNIFACE_CACHE_DIR=/opt/uniface-cache`), add:
```dockerfile
# Belt-and-suspenders: cap any BLAS/OpenMP fan-out in native deps. The real
# guarantee against pegging the host is the cpus cgroup cap on the compose
# service; onnxruntime intra-op is capped via SessionOptions in mobilegaze.py.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
```
Change line 57 (the CMD) to:
```dockerfile
CMD ["dramatiq", "app.vision_worker", "--processes", "4", "--threads", "1", "-Q", "vision"]
```

- [ ] **Step 2: Add cpus cap + concurrency env in `docker-compose.yml`**

In `docker-compose.yml`, in the `nexus-vision-worker` service, add a CPU limit (place near `restart:`):
```yaml
    # Hard backstop: the worker can never exceed this many cores regardless of
    # any dependency's thread fan-out (2026-06-01 incident pegged ~23 cores).
    # Keep VISION_WORKER_CONCURRENCY <= this value.
    cpus: 4
```
Add `VISION_WORKER_CONCURRENCY` to the service `environment:` block:
```yaml
      - VISION_WORKER_CONCURRENCY=${VISION_WORKER_CONCURRENCY:-4}
```
Change the command (line 147) to:
```yaml
    command: sh -c 'dramatiq app.vision_worker --processes ${VISION_WORKER_CONCURRENCY:-4} --threads 1 -Q vision'
```

- [ ] **Step 3: Document the env block in `.env.example`**

In `.env.example`, after line 113 (`VISION_THUMBNAIL_TOP_FLAG_COUNT=6`), insert:
```bash

# Vision proctoring — bounded-CPU tuning (vision worker). See
# docs/superpowers/specs/2026-06-01-vision-proctoring-perf-design.md.
# Sample rate (fps). 2.0 catches sustained off-screen, reading, multi-face, and
# down-glances >=~1s. Lower = cheaper, less sensitive.
VISION_SAMPLE_FPS=2.0
# Hard frame budget per session. Effective fps = min(VISION_SAMPLE_FPS,
# VISION_MAX_FRAMES / duration_seconds) — long recordings degrade to a wider
# uniform stride instead of truncating. Bounds worst-case cost.
VISION_MAX_FRAMES=2000
# Pre-detection downscale: cap frame width (px) before RetinaFace + gaze.
VISION_MAX_FRAME_WIDTH=960
# onnxruntime intra-op threads PER inference. Keep at 1 — parallelism comes from
# worker concurrency, not per-call fan-out. (Raising this pegged the host on
# 2026-06-01.)
VISION_ORT_INTRA_OP_THREADS=1
# Vision worker inference processes (Dramatiq --processes). Keep <= the worker's
# cpus cap (docker-compose / ECS task cpu). Scale throughput via REPLICAS.
VISION_WORKER_CONCURRENCY=4
```

- [ ] **Step 4: Validate compose config parses**

Run: `docker compose config | grep -A2 -iE "cpus|VISION_WORKER_CONCURRENCY"`
Expected: shows `cpus: 4.0` (or `"4"`) under the vision worker and the concurrency env var resolved.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile.vision docker-compose.yml .env.example
git commit -m "feat(vision): cpus backstop + N single-threaded procs + OMP caps + env docs"
```

---

## Task 7: Integration verification (vision image, real ffmpeg + ORT)

**Files:**
- Test: `tests/vision/test_sampler_integration.py` (new, `@pytest.mark.vision_integration`)

This runs inside the built vision image where ffmpeg + numpy exist. It generates a tiny synthetic clip with ffmpeg, then asserts the sampler yields the expected bounded frame count with correct dims + monotonic timestamps.

- [ ] **Step 1: Register the marker**

In `pyproject.toml` `[tool.pytest.ini_options]` `markers = [...]`, add:
```toml
    "vision_integration: requires the vision image (ffmpeg + numpy + onnxruntime)",
```

- [ ] **Step 2: Write the integration test**

Create `tests/vision/test_sampler_integration.py`:
```python
# tests/vision/test_sampler_integration.py
import os
import subprocess
import tempfile

import pytest

from app.modules.vision.sampler import sample_frames

pytestmark = pytest.mark.vision_integration


def _make_clip(path: str, *, seconds: int, w: int, h: int, src_fps: int) -> None:
    # Synthetic test pattern, no audio.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={src_fps}:duration={seconds}",
         "-pix_fmt", "yuv420p", path],
        check=True,
    )


def test_sampler_bounds_frames_and_downscales():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "clip.mp4")
        _make_clip(clip, seconds=10, w=1280, h=720, src_fps=30)
        frames = list(sample_frames(clip, target_fps=2.0, max_frames=2000, max_width=960))

    # 10s @ 2fps ~= 20 frames (budget not binding).
    assert 18 <= len(frames) <= 22
    t0, f0 = frames[0]
    assert f0.shape == (540, 960, 3)          # 1280x720 -> 960x540
    times = [t for t, _ in frames]
    assert times == sorted(times)             # monotonic
    assert times[0] == 0


def test_sampler_budget_degrades_long_clip():
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "long.mp4")
        _make_clip(clip, seconds=30, w=640, h=480, src_fps=15)
        # Budget 30 frames over 30s forces ~1 fps (below target 2 fps).
        frames = list(sample_frames(clip, target_fps=2.0, max_frames=30, max_width=960))

    assert len(frames) <= 32                  # bounded by the budget, not 60
```

- [ ] **Step 3: Build the vision image**

Run: `docker compose build nexus-vision-worker`
Expected: image builds (ffmpeg present).

- [ ] **Step 4: Run the integration tests in the vision image**

The production vision worker image deliberately does not contain pytest (lean image); install ephemerally in a throwaway container.

Run:
```bash
docker compose run --rm --no-deps --entrypoint "" nexus-vision-worker \
  sh -c "pip install -q --target /tmp/testdeps pytest pytest-asyncio && PYTHONPATH=/tmp/testdeps:\$PYTHONPATH /tmp/testdeps/bin/pytest tests/vision/test_sampler_integration.py -m vision_integration -v"
```
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/vision/test_sampler_integration.py
git commit -m "test(vision): ffmpeg sampler integration — bounded frames + downscale"
```

---

## Task 8: Full suite + bounded-CPU smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full vision unit suite (lean image)**

The production vision worker image deliberately does not contain pytest (lean image); the unit suite runs in the lean `nexus` image which does have it (via `.[dev]`).

Run: `docker compose run --rm nexus pytest tests/vision -m "not vision_integration" -v`
Expected: PASS, no regressions in `test_detectors_*`, `test_analysis_observations`, `test_actor_idempotency`, `test_public_api`, etc.

- [ ] **Step 2: Lint the changed files**

Run: `docker compose run --rm nexus ruff check app/modules/vision app/config.py`
Expected: no errors.

- [ ] **Step 3: Bounded-CPU smoke against a real recording (manual)**

With the optimized worker built, start ONE vision worker and process a real ~14-min recording (re-enqueue via a report read, or directly send the actor a known session_id/tenant_id). Run the integration test via ephemeral install first to confirm the vision image is healthy:
```bash
docker compose run --rm --no-deps --entrypoint "" nexus-vision-worker \
  sh -c "pip install -q --target /tmp/testdeps pytest pytest-asyncio && PYTHONPATH=/tmp/testdeps:\$PYTHONPATH /tmp/testdeps/bin/pytest tests/vision/test_sampler_integration.py -m vision_integration -v"
```
Then start the worker and watch CPU in a second terminal:
```bash
docker stats --no-stream nexus-vision-worker
```
Expected: CPU% stays at/below the `cpus: 4` cap (≤ ~400%), NOT ~2279%. Wall-clock to `status='ready'` is within ~2× the recording length. Confirm the row reaches `ready`/`unscorable` and the report panel renders.

- [ ] **Step 4: Final commit / branch ready**

```bash
git add -A && git status
# If clean, the branch fix/vision-proctoring-perf is ready for review/merge.
```

---

## Self-Review Notes (author)

- **Spec coverage:** §4.1 fan-out → Tasks 4 (intra_op) + 6 (cpus/OMP). §4.2 work bounds → Tasks 1 (fps/budget/width config) + 2/3 (adaptive stride + ffmpeg + downscale). §4.3 singleton → Task 5. §4.4 concurrency → Task 6. §4.5 GPU seam → untouched `GazeEstimator` (no task needed; preserved by construction). §6 env surface → Tasks 1 + 6. §7 testing → Tasks 2,3,4,5,7,8. All covered.
- **Type/name consistency:** `sample_frames(video_path, *, target_fps, max_frames, max_width)` used identically in sampler.py, analysis.py wiring, and tests. `_get_estimator()` / `_estimator` consistent across actors.py + tests. `intra_op_threads` kwarg consistent in mobilegaze.py + singleton + test. `VisionConfig.max_frames/max_frame_width/ort_intra_op_threads/worker_concurrency` consistent.
- **No placeholders:** every code step shows full code; every run step shows the exact command + expected result.
