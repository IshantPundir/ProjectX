# Vision Proctoring — Performance Hardening (Bounded-CPU)

**Date:** 2026-06-01
**Status:** Design — approved direction, pending spec review
**Branch:** `fix/vision-proctoring-perf` (off `main`)
**Author:** Ishant Pundir (with Claude)
**Supersedes/extends:** the Phase 3D vision proctoring server plane (`app/modules/vision/*`, migration `0043_session_proctoring`)

---

## 1. Incident

**2026-06-01.** After a ~14-minute interview, the post-session vision proctoring
pass pegged **~2279% CPU (≈22.8 of 24 cores ≈ 95% of the host)** and hung on
"Analyzing" in the report's *Proctoring & Integrity* panel. The job is the
`nexus-vision-worker` Dramatiq service running the `analyze_session_proctoring`
actor: it downloads the session recording from R2 and runs every sampled frame
through RetinaFace (face detection) + a gaze ONNX model.

### Stabilization already in place (verified before this work)
- `nexus-vision-worker` is **stopped** (`docker compose stop nexus-vision-worker`) — CPU is free.
- The stuck row for session `f4892cff-3ba3-40f7-99a6-0ccc0357eb3d` was set to
  `status='unscorable'` so the report page (which re-enqueues vision analysis on
  *every* read via `_maybe_enqueue_vision`) won't re-trigger it. The actor's
  idempotency gate skips only rows in `{ready, unscorable}`.

---

## 2. Root cause (evidence-based)

Two **independent** failure modes were conflated in the incident:

### 2.1 CPU peg — unbounded thread fan-out
`app/modules/vision/gaze/mobilegaze.py:40` constructs the gaze session as
`ort.InferenceSession(weights_path, providers=["CPUExecutionProvider"])` with
**no `SessionOptions`**. onnxruntime then defaults `intra_op_num_threads` to the
host core count, so a *single* `run()` call fans out across **all 24 cores** for
its duration. The RetinaFace detector (`uniface`, also onnxruntime-backed,
`mobilegaze.py:43`) is likewise uncapped. A grep confirmed **zero** thread
configuration anywhere in `app/modules/vision/`, `Dockerfile.vision`, or
`app/config.py`. The Dramatiq worker additionally runs `--threads 2`
(`docker-compose.yml:147`); onnxruntime releases the GIL during native
inference, so two actor threads each launch all-core inference simultaneously →
oversubscription and sustained near-total saturation. `2279% ≈ 22.8 cores` is
exactly this signature on a 24-core box.

### 2.2 "Stuck" — unbounded work
`app/modules/vision/analysis.py:138-141` walks the **entire** recording in a
plain Python loop, one frame at a time:
```python
for t_ms, frame in _sample_frames(local_video_path, cfg.sample_fps):
    frames.append((t_ms, estimator.estimate(frame)))
```
At `vision_sample_fps = 5.0` (`config.py:595`) a 14-min video ≈ **4,200 frames**,
each a full-resolution RetinaFace detect + a 448² gaze inference, strictly
sequential, with **no max-frames / max-duration cap**. Under the thread
contention above, per-frame latency balloons and wall-clock exceeds the video
length → "stuck."

### 2.3 Ruled out: batching / memory
The initial hypothesis ("we pass all frames together") is **false**. Frames are
processed one at a time; `frames.append(...)` retains only tiny `FaceGaze` result
tuples (a bbox + two float angles + a score), never decoded image arrays. No
batch tensor, no memory blow-up. The real cause is the *inverse*: uncapped
per-call fan-out × unbounded frame count.

### 2.4 Secondary inefficiencies found
- **Per-session model reload:** `actors.py:211` constructs `MobileGazeEstimator`
  *inside* `_run` — i.e. reloads the ONNX model + re-inits RetinaFace on **every
  session** — despite the class docstring stating "one instance per worker
  process (model load + detector init are costly)."
- **Decode waste:** `_sample_frames` calls `cap.read()` (full decode incl.
  color-convert) for *every* source frame and discards 14 of 15 via modulo — so a
  14-min/30fps video fully decodes ~25k frames to use ~1,800.
- **Full-resolution detection:** RetinaFace runs on the raw egress frame;
  `cv2.resize` only shrinks the *crop* fed to the gaze model, not the detect pass.

---

## 3. Goals & decisions

**Goal:** make post-session proctoring scale (target: eventually hundreds of
concurrent sessions, recordings up to ~15+ min) without pegging CPU, keeping
proctoring accuracy acceptable, and keeping cost low — *as an accuracy-vs-cost
design, not a snap patch*.

**Decisions taken (this session):**
1. **Bounded CPU now, GPU seam later.** No new infra; fix on the existing
   onnxruntime CPU worker, keep a clean seam for a future GPU/batch estimator.
2. **2 fps sampling.** Catches sustained off-screen (≥2 s), reading-sweep (4 s
   window), multi-face (≥1.5 s), and down-glances ≥~1 s. Sub-second flicks are
   abandoned as noise. 2.5× cheaper than 5 fps.
3. **ffmpeg-based sampling** (`-vf fps,scale`), ffmpeg already in the image.
4. **Hard CPU backstop:** `cpus: 4` on the worker + 4 single-threaded inference
   processes.

---

## 4. Design — the four bounds + a backstop

### 4.1 Bound fan-out (direct fix for the peg)

| Lever | Today | Proposed |
|---|---|---|
| ORT `intra_op_num_threads` (gaze session) | unset → all cores | **1** |
| ORT `inter_op_num_threads` + `execution_mode` | unset | **1 / `ORT_SEQUENTIAL`** |
| Docker `cpus:` limit on `nexus-vision-worker` | none | **4** (load-bearing backstop) |

A shared `onnxruntime.SessionOptions(intra_op_num_threads=1,
inter_op_num_threads=1, execution_mode=ORT_SEQUENTIAL)` is passed to the gaze
`InferenceSession`. Parallelism comes from worker concurrency, **not** per-call
threads — the standard CPU-serving pattern.

`uniface.RetinaFace` may not expose `SessionOptions`. Rather than a fragile
monkeypatch, the **`cpus: 4` cgroup limit is the guarantee**: even if RetinaFace
ignores thread prefs, the worker can never exceed 4 cores. Oversubscription
*within* a 4-core quota is cheap. Implementation will still attempt to pass
`sess_options`/`providers` to `RetinaFace(...)` if the installed `uniface`
version supports it (best-effort efficiency), but correctness does not depend on
it. (`OMP_NUM_THREADS=1` etc. set in `Dockerfile.vision` as belt-and-suspenders;
note onnxruntime uses its own thread pool, so the cgroup cap remains the real
guarantee.)

### 4.2 Bound work

| Lever | Today | Proposed | Mechanism |
|---|---|---|---|
| Sample rate | 5 fps | **2 fps** | `vision_sample_fps = 2.0` |
| Frame budget | none | **~2,000 frame cap, adaptive stride** | new `vision_max_frames` |
| Pre-inference downscale | none | **cap width ~960 px** | new `vision_max_frame_width` |
| Decode | `cv2.read()` every frame | **ffmpeg `-vf fps=2,scale=960:-1`** | rewrite `_sample_frames` |

**Adaptive stride / frame budget.** The effective sampling fps is
`min(target_fps, max_frames / duration_seconds)`. A 16-min video runs at full
2 fps (~1,920 frames); a 40-min video auto-drops to ~0.8 fps to stay within the
~2,000-frame budget — **uniform across the whole session**, so coverage stays
whole-session rather than a hard time-cap blinding the back half. The budget is
the worst-case cost bound *regardless of recording length*.

**ffmpeg sampler.** Replace the OpenCV per-frame decode loop with an ffmpeg
subprocess: `ffmpeg -i <recording> -vf "fps=<eff_fps>,scale=960:-1" -f
rawvideo -pix_fmt bgr24 pipe:1`, reading frames off stdout into numpy arrays of
known `(h, w, 3)`. ffmpeg decodes keyframe-aware and emits only the sampled,
pre-scaled frames in C. Frame timestamps are reconstructed from the effective
fps and frame index (`t_ms = round(i / eff_fps * 1000)`). The thumbnail path
(`grab_thumbnails`) still seeks the original file with OpenCV and is unaffected
(it grabs a handful of frames, not a full pass).

> The effective fps for **timestamp reconstruction** must be the *exact* rate
> handed to ffmpeg, so flag interval timing (`off_screen_min_ms`,
> `down_glance_*`, `reading_window_ms`) stays correct. ffmpeg's `fps` filter
> resamples to a constant rate, which makes index→timestamp exact.

### 4.3 Process-level estimator singleton
Hoist `MobileGazeEstimator` construction out of `_run` into a **lazy
module-level singleton per worker process** (built on first actor call /
Dramatiq prewarm). Model + detector load once per process, not once per session.

### 4.4 Concurrency model
`--processes 1 --threads 2` → **`--processes 4 --threads 1`** (driven by a new
`VISION_WORKER_CONCURRENCY` env, default 4, matched to the `cpus: 4` cap). N
independent single-threaded inferences give clean linear throughput with no GIL
contention on the cv2/preprocessing/decode-pipe parts. **Scale to hundreds
concurrent by adding cpu-capped worker replicas**, not by widening one box.

### 4.5 GPU seam (unchanged, documented)
The `GazeEstimator` protocol (`app/modules/vision/gaze/base.py`) stays. All
bounds (fps, frame budget, downscale, thread caps, concurrency) live in
config/orchestration, **not** in the estimator. A future `CudaGazeEstimator`
(batched, GPU `providers`) slots behind the same seam when a real enterprise
client triggers it — the "GPU later" half of the chosen path.

---

## 5. Expected outcome

~1,920 frames/session × single-core (detect + gaze on a 960 px frame) ≈
**~60–90 s of one core per session**. At 4 processes ≈ **~160 sessions/hour per
cpu-capped (4-core) worker**; replicas scale linearly. Host stays responsive
throughout; the worker can never exceed 4 cores regardless of dependency
behavior.

---

## 6. Config surface — fully env-driven

Every bounded-CPU value is a `pydantic-settings` field on `Settings`
(`app/config.py`), so each is **overridable in any environment via its uppercased
env var** with zero code change — the same pattern as the rest of the config.
This is the production tuning surface (Railway env vars at MVP, ECS task
env/Secrets at enterprise).

| Setting (`app/config.py`) | Env var | Old | New (default) |
|---|---|---|---|
| `vision_sample_fps` | `VISION_SAMPLE_FPS` | 5.0 | **2.0** |
| `vision_max_frames` | `VISION_MAX_FRAMES` | — | **2000** (new) |
| `vision_max_frame_width` | `VISION_MAX_FRAME_WIDTH` | — | **960** (new) |
| `vision_ort_intra_op_threads` | `VISION_ORT_INTRA_OP_THREADS` | — | **1** (new) |

`VISION_WORKER_CONCURRENCY` is a deploy/shell env var (consumed by the compose command + the Dockerfile CMD), NOT a pydantic-settings field — a Dramatiq `--processes` count is a launch-time CLI arg, so it can't be a runtime setting.

**`.env.example`** — add a documented block alongside the existing
`VISION_THUMBNAIL_*` entries (which today are the *only* documented vision vars;
`VISION_SAMPLE_FPS` was previously undocumented and gets added here too):

```bash
# --- Vision proctoring — bounded-CPU tuning (vision worker) ---
# Sample rate for gaze/proctoring analysis. 2 fps catches sustained off-screen,
# reading, multi-face, and down-glances >=~1s. Lower = cheaper, less sensitive.
VISION_SAMPLE_FPS=2.0
# Hard frame budget per session. Effective fps = min(VISION_SAMPLE_FPS,
# VISION_MAX_FRAMES / duration_seconds) — long recordings degrade to a wider
# uniform stride instead of being truncated. Bounds worst-case cost.
VISION_MAX_FRAMES=2000
# Pre-detection downscale: cap frame width (px) before RetinaFace + gaze.
VISION_MAX_FRAME_WIDTH=960
# onnxruntime intra-op threads PER inference. Keep at 1 — parallelism comes from
# worker concurrency, not per-call thread fan-out. (Raising this is what pegged
# the host in the 2026-06-01 incident.)
VISION_ORT_INTRA_OP_THREADS=1
# Inference processes the vision worker runs (Dramatiq --processes). Match to the
# worker's CPU cap. Scale throughput by adding worker REPLICAS, not by raising
# this past the cap.
VISION_WORKER_CONCURRENCY=4
```

**`docker-compose.yml`** `nexus-vision-worker`:
- add a hard CPU backstop — `cpus: "4"` (Compose v2 top-level) or
  `deploy.resources.limits.cpus: "4"`;
- command → `dramatiq app.vision_worker --processes ${VISION_WORKER_CONCURRENCY:-4} --threads 1 -Q vision`;
- the env var flows from `.env` via the existing `env_file`.

**`Dockerfile.vision`**: `ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`
(belt-and-suspenders for any BLAS/OpenMP fan-out; the `cpus:` cgroup cap remains
the real guarantee).

> Production tuning note: the CPU backstop has two coordinated knobs —
> `VISION_WORKER_CONCURRENCY` (app-level process count) and the compose/orchestrator
> `cpus` limit (cgroup ceiling). Keep them matched (concurrency ≤ cpu cap) per
> environment. On ECS this becomes the task's `cpu` units + the same env var.

---

## 7. Testing

**Unit (pure, no cv2/onnx):**
- Adaptive-stride / effective-fps math across a matrix of (duration, target_fps,
  max_frames) — including the budget-binding long-video case and the
  short-video no-binding case.
- Index→timestamp reconstruction matches expected `t_ms` at the chosen eff_fps.

**Integration (vision image):**
- ffmpeg sampler on a short real recording: assert frame count ≈ expected,
  frame dtype/shape `(h, ≤960, 3)`, monotonic timestamps.
- One short recording through the actor with `cpus: 4` applied: assert the gaze
  result persists, `frames_analyzed` within budget, and (manual) CPU stays
  bounded (`docker stats`) with wall-clock ≈ sub-2× video length.

**Regression guard:** existing pure-function tests
(`observations_from_estimates`, `select_flag_targets`, `analyze_observations`,
`_target_frame_index`) must stay green — the flag/band logic is unchanged.

---

## 8. Out of scope (explicit)
- GPU / batched inference implementation (seam preserved; not built).
- Coarse + escalate two-stage sampling.
- Report-page re-enqueue storm: bounded fast jobs make it non-urgent. Noted
  follow-up — add a `running`-state heartbeat/timeout guard so a genuinely
  in-flight job isn't reclaimed, and skip re-enqueue when a recent one exists.
- Anything on the `feat/candidate-reel-phase1-capture` branch (separate, known
  word-timing bug — untouched).
- Changing the gaze model input size (448²) or the flag/band thresholds — the
  detection contract and accuracy tuning stay as-is.

---

## 9. Rollback
Pure config + worker-shape changes plus a sampler rewrite behind the unchanged
`run_analysis` signature. Rollback = revert the branch; no migration, no schema
change, no data backfill. The stabilization (worker stopped, row `unscorable`)
is independent and remains valid until the optimized worker is redeployed.
