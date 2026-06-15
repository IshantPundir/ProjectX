# Client Proctoring Hardening — Design

- **Date:** 2026-06-15
- **Status:** Approved (brainstorm) — pending implementation plan
- **Surface:** `frontend/session` (candidate interview) + one backend touchpoint in `backend/nexus/app/modules/session/`
- **Branch:** `feat/client-proctoring-hardening`
- **Supersedes nothing.** Extends the client half of the two-plane proctoring system first defined in `docs/superpowers/specs/2026-05-21-candidate-session-proctoring-design.md` and `2026-05-29-vision-proctoring-design.md`.

> **Architecture invariant (unchanged):** the client plane is a **coarse deterrent + signal for human review**. The backend is authoritative on soft-violation thresholds and termination. Accurate, eye-aware gaze remains the **server-side** `vision` module's job (ONNX, post-interview, GPU). Nothing in this design moves heavy/accurate inference onto the candidate's machine, and proctoring **never auto-rejects**.

---

## 1. Problem Statement

Four concrete weaknesses in the current client-side proctoring, all reported from live use:

1. **Background / multi-person faces are missed.** The face *count* is derived from MediaPipe `FaceLandmarker` (`facialTransformationMatrixes.length`). The landmarker is tuned for **one prominent foreground face** for landmark precision — it routinely fails to detect a second person sitting further back in the frame, defeating the `multiple_faces` signal.

2. **Soft violations are easy to miss.** Soft warnings surface only as a `sonner` toast + a brief `ViolationBorder` flash. Candidates miss them; there is no clear, unambiguous notice like the existing fullscreen/focus grace modals.

3. **Pre-start fullscreen gap (evasion).** `ProctoringGuard` arms a *single* `armed` flag ~800ms **after the agent first speaks** (`ARM_SETTLE_MS`). The fullscreen request in `onStart` is fire-and-forget. Between "Start interview" and the agent's first words, **nothing is watching** — a candidate can exit fullscreen, set up aids, and re-enter; `hasEntered` flips `true` on the eventual re-entry and records nothing.

4. **Second-screen / focus blind spot.** The focus guard relies on `window.blur`/`focus`. A browser tab **cannot** observe other physical monitors, the OS pointer, or which OS window holds focus when it is outside the page — this is the browser security sandbox. On a focus-follows-mouse compositor (e.g. Wayland), the interview window can keep focus while the candidate attends a second display, and `blur` never fires.

## 2. Goals

- **G1.** Robust, efficient detection of additional/background faces, staying within the existing MediaPipe ecosystem and the same-origin / no-third-party rule.
- **G2.** A clear modal **notice** for soft violations, visually consistent with the existing grace overlays, replacing (not layering over) the soft-violation toast.
- **G3.** Eliminate the pre-conversation unmonitored window so a fullscreen exit before the interview "begins" is caught exactly like a mid-interview exit.
- **G4.** Honest second-screen mitigation: a permission-free multi-monitor **pre-check gate** + an in-session **screen-change** signal, plus a strengthened head-pose looking-away signal that catches attention drifting off the primary screen even when focus never changes.

## 3. Non-Goals / Out of Scope

- **No ONNX / second inference runtime on the client** (rejected Option B). The deterrent plane stays MediaPipe-only.
- **No iris / eye-aware gaze on the client.** Head-pose-only remains the live signal (accurate gaze is the server plane). The previously-reverted iris path is not reintroduced.
- **No hard fullscreen gate** (holding the agent greeting until fullscreen is confirmed). Explicitly deferred — arm-at-connect closes the reported evasion; revisit only if candidates stall in the grace window.
- **No `getScreenDetails()` permission prompt.** We use only the permission-free `window.screen.isExtended`.

## 4. Enterprise / Code-Quality Constraints (binding on the plan)

- **No dead or stale code.** This work deletes the soft-violation toast path entirely (not bypassed), removes the landmarker as a *count* source once the detector owns counting, and **wires up the currently-dead `ReadingAccumulator`** (`vision/reading.ts`) — leaving no unused module.
- **No hacks/patches/workarounds.** Arming is fixed at its root (two-tier arming), not masked with timing fudges.
- **Same-origin assets only.** The new model binary is served from `public/mediapipe/`; no CDN, no CSP `connect-src` change.
- **Backend-authoritative severity** is preserved; the new `multiple_displays` kind is validated by Pydantic and classified server-side.
- All `components/interview/proctoring/` changes fall under **"Human Review Required For"** (per `frontend/session/CLAUDE.md`).

---

## 5. Section 1 — Vision face-detection upgrade (G1)

**Approach (chosen Option A):** add a MediaPipe `FaceDetector` with the officially-supported **short-range** model as the authoritative face *count*; keep `FaceLandmarker` for head-pose/gaze/blink of the primary face only.

> **Feasibility note (2026-06-15):** the modern Tasks `FaceDetector` ships **only the short-range BlazeFace model** (~2m); full-range is "coming soon" and not a vendorable Tasks asset. Force-loading a legacy full-range `.tflite` lacks Tasks metadata and is an unsupported hack — rejected per the no-hacks rule. **Decision:** short-range detector is the live deterrent (a real upgrade over the landmarker, which returns only the dominant face and drops a close second person); **far/background faces (>~2m) are owned by the server-side RetinaFace plane** (`app/modules/vision/`), which already runs post-interview and is far better at tiny faces than anything we'd run live. The client stays a deterrent — this is by design, not a gap.

### 5.1 New module `proctoring/vision/face-detector.ts`
- `createFaceDetector()` — lazily creates a MediaPipe `FaceDetector` (`@mediapipe/tasks-vision`, same package) from a **same-origin** short-range model `public/mediapipe/blaze_face_short_range.tflite`, with `runningMode: 'VIDEO'`, `delegate: 'GPU'`, and `minDetectionConfidence ≈ 0.3` (lowered from the 0.5 default to stretch effective range for a slightly-back second face). Mirrors `createFaceLandmarker()` exactly (dynamic import; **shared `FilesetResolver`** instance — created once, passed to both factories).
- Exposes a small typed result (`{ faceCount: number; topConfidence: number }`) derived from `detector.detectForVideo(video, ts).detections`, so `use-vision-guard` does not depend on MediaPipe types directly.

### 5.2 Asset
- Commit `blaze_face_short_range.tflite` into `public/mediapipe/`, fetched from the official MediaPipe model storage (`https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite`); no CDN at runtime. Document provenance + license in the plan (mirrors how `face_landmarker.task` is vendored).

### 5.3 `use-vision-guard.ts` changes
- Reuse the **single** detached `<video>` element and the existing `requestAnimationFrame` loop.
- `FaceLandmarker` keeps running **every frame** (pose/gaze/blink of the primary face).
- `FaceDetector` runs on a **throttle (~2–3 fps)** via a `lastDetectAt` accumulator — face presence/multi-face is a slow-moving signal, so this avoids doubling per-frame cost.
- **`faceCount` is now the detector's `detections.length`** (replacing `facialTransformationMatrixes.length`). The landmarker matrix is used **only** for pose. `signalQuality`'s `faceConfidence` uses the detector's `topConfidence`.
- `maybeFire('multiple_faces', faceCount >= 2, …)` and `maybeFire('face_not_visible', faceCount === 0, …)` fire off the detector count. `looking_away_sustained` stays on landmarker pose.
- Keep `multiple_faces` sustain ≥ 500ms so a single noisy detector frame can't fire it; existing rising-edge + re-arm logic absorbs the lower cadence.

### 5.4 Dev overlay
- `VisionDebugOverlay` adds the detector face count (and, optional, per-face boxes) to the existing readout.

### 5.5 Testing
- Unit-test the throttle/cadence accumulator and the `faceCount`-from-detector wiring; **mock `createFaceDetector`** alongside the existing `createFaceLandmarker` mock (jsdom cannot run MediaPipe WASM). Verify `multiple_faces` fires from the *detector* count, not the landmarker.

---

## 6. Section 2 — Violation notice popup (G2)

**Scope:** SOFT violations only. Hard violations terminate immediately and mount `ProctoringEndedScreen` (its own full-screen message) — they do not use this popup.

### 6.1 New component `proctoring/ViolationNoticeOverlay.tsx`
- Centered glass card matching `FullscreenGraceOverlay`/`FocusGraceOverlay` (`px-glass-strong`, backdrop blur, `z-[70]`), **caution-colored** (`var(--px-caution)`).
- Content: a heading conveying the issue, body = `VIOLATION_LABEL[kind]`, and **"Warning X of N"** (soft count vs `soft_violation_limit`, both already tracked by the controller).
- An **"I understand"** acknowledge button **and auto-dismiss after ~6s**. Latest violation wins; a new soft violation resets the timer.
- Accessibility: `role="alertdialog"`, `aria-live="assertive"`, respects `prefers-reduced-motion`, focusable acknowledge button. The scrim is **visual only** — LiveKit audio and the agent keep running underneath (a notice, not a pause).

### 6.2 Wiring (`use-proctoring-controller.ts`)
- Controller gains `notice: { kind, softCount, limit } | null` + `dismissNotice()`, set in the soft-violation branch (currently lines 71–82). `ProctoringGuard` renders `<ViolationNoticeOverlay>` when `notice` is set.
- **Remove the `sonner` `toast.warning(...)` soft-violation call** (replaced by the modal — no dead toast path left). The hard-violation `toast.error(...)` path is unchanged (terminate → `ProctoringEndedScreen`).
- **`ViolationBorder` is retained** as a brief severity-colored accent behind the popup (extra peripheral signal), per decision.

### 6.3 Testing
- Component test: a soft violation sets `notice`, renders the modal with the correct label + "Warning X of N", auto-dismisses after the timer, and acknowledges on click. Assert `toast.warning` is **not** called for soft violations.

---

## 7. Section 3 — Close the pre-start fullscreen gap (G3)

**Root cause:** all guards gate on one `armed` flag set ~800ms after the agent's first speech. Fix by **splitting arming into two tiers.**

### 7.1 Two-tier arming in `ProctoringGuard.tsx`
- **`envArmed` (new)** — derived from the LiveKit **room connection state** (`useConnectionState()` from `@livekit/components-react`, `=== ConnectionState.Connected`). Gates the **environment guards**: `useVisibilityGuard`, `useFocusGuard`, `useFullscreenGuard`, `useKeyboardGuard`, `useDevtoolsGuard`. These do not need the camera track, so they arm as soon as the room is connected — making the pre-conversation window monitored identically to mid-interview.
- **`armed` (existing)** — the `~800ms-after-speech` settle is retained **only** for `useVisionGuard` (it needs the camera track + a settled agent to avoid false "looking away" while the candidate gets seated).
- Both still gate on `cfg.enabled`. Update the line 44–46 comment to describe the two tiers accurately (no stale rationale left behind).

### 7.2 Why early focus/visibility arming is safe
- Camera/mic permission prompts happen in the pre-check wizard (`CameraMicStep`), **before** `onStart`. By `connected`, permissions are granted, so arming focus/visibility at `connected` will not fire false blurs from a permission dialog. Arming on the `connected` **state** (not bare mount) avoids any connect-time flicker.

### 7.3 Fullscreen behavior after the fix
- The fire-and-forget `requestFullscreen()` in `app.tsx onStart` stays as the immediate best-effort entry, now **backed by a guard armed from `connected`**: a denied request or any subsequent exit immediately surfaces the existing `FullscreenGraceOverlay` (return-or-terminate), instead of going unnoticed. The `hasEntered` initial-entry logic is unchanged — but it now operates from `connected`, before the candidate can perform an unobserved exit/re-enter cycle.

### 7.4 Testing
- Unit-test that env guards receive `armed: true` at `ConnectionState.Connected` while the vision guard remains gated on the speech-settle path. Test the fullscreen guard fires `fullscreen_abandoned` when an exit during the pre-speech window runs the grace clock out.

---

## 8. Section 4 — Second-screen handling (G4)

### 8.1 Multi-monitor pre-check gate (4a)
- **Pre-check:** read **`window.screen.isExtended`** in `CameraMicStep` (live-updating via the screen `change` event). **As-built (amended 2026-06-15):** a detected second display is a **non-blocking warning**, not a hard block — the candidate sees a caution message ("We detected more than one display. A single screen is recommended — using multiple displays is flagged during the interview.") and may still **Continue**. This keeps the multi-display signal uniformly a *warning* (consistent with the in-session soft `multiple_displays` and the "deterrent, not a hard wall" philosophy), and unblocks legitimate dual-monitor dev/test setups. (The original design blocked Continue; relaxed to a warning after live testing.)
- **Graceful degradation:** `isExtended` is Chromium-supported but not universal. When `undefined`/unsupported, **no warning** (detect where we can). No `getScreenDetails()` permission prompt.
- **In-session:** subscribe to the screen `change` event during the live session; if displays become extended mid-interview, fire a **`multiple_displays`** violation (soft, counted) → surfaces via the Section 2 popup.

### 8.2 New violation kind `multiple_displays`
Threads through end-to-end:
- **Frontend:** add to `ProctoringKind` (`lib/api/candidate-session.ts`), `VIOLATION_LABEL` (`violation-kinds.ts`) — phrase e.g. "using more than one display". Remains **not** in `HARD_KINDS` (soft).
- **Backend:** add `"multiple_displays": "soft"` to `VIOLATION_SEVERITY` (`app/modules/session/proctoring.py`) and to the `ProctoringKind` Pydantic literal (`app/modules/session/schemas.py:86`), so the `/proctoring/event` endpoint validates + classifies it. (Pure-function `proctoring.py` is unit-tested — add the case.)

### 8.3 Strengthen in-session gaze (4b)
- **Wire up the dead `ReadingAccumulator`** (`vision/reading.ts`): a rolling-window detector flagging repeated left↔right off-screen glances — the "reading a second screen while the window keeps focus" pattern the single 1s sustained-off-center misses. Feed each frame's gaze zone into it within `use-vision-guard`'s tick and emit `looking_away_sustained` on its rising edge (alongside the existing sustain). Removes the last dead module in the subsystem.
- **Stays head-pose-only.** Tune `YAW_OFF` / window thresholds (`gaze.ts`, `reading.ts`) so persistent side-gaze toward an off-screen display is caught without nagging honest candidates who glance away briefly. Thresholds are tuned live via the debug overlay (documented in the plan).

### 8.4 Testing
- `proctoring.py`: `classify_severity("multiple_displays") == "soft"` and a `decide_termination` escalation case.
- Frontend: `isExtended` warning shows but `CameraMicStep` still permits Continue; `ReadingAccumulator` fires after the rolling-window pattern; the in-session screen-change handler reports `multiple_displays`.

---

## 8b. Post-implementation addition — proctoring termination dry-run toggle

Added 2026-06-15 (not in the original brainstorm) to test the full proctoring UX in production-like conditions without ending the session.

- **`PROCTORING_TERMINATION_ENABLED`** — backend env (`app/config.py`, `Settings.proctoring_termination_enabled: bool = True`). Default `true` ⇒ production behavior unchanged.
- **Backend is the source of truth** (it cancels the LiveKit room + transitions session state). When `false`, `record_proctoring_event` still appends the violation, increments counts, and returns them, but computes `effective_terminal = terminal and termination_enabled`: it skips the state transition / `cancel_room` / `proctoring_outcome` stamp and instead audits a distinct **`session.proctoring_termination_suppressed`** event (`would_be_outcome` payload). Returns `terminated=false`.
- **Propagated to the candidate app** via the existing `ProctoringConfig` (`/start`, `/rejoin`) as a new field **`terminate_enabled`** (`schemas.py`, default `True`). The frontend controller (`use-proctoring-controller.ts`) short-circuits `terminate()` when `config.terminate_enabled === false` **without latching `terminatedRef`**, so all subsequent warnings/popups/counter keep firing.
- **Both gates are load-bearing:** the frontend hard-violation path terminates locally *before* the backend responds (so the client gate is required); the backend cancels the room independently (so the backend gate is required).
- **Tests:** backend (`tests/test_session_proctoring_service.py`) — dry-run keeps the session `active`, no `cancel_room`, violation still recorded; default-true still terminates; `_build_proctoring_config` forwards the flag. Frontend (`use-proctoring-controller.test.tsx`) — no terminate + no latch under dry-run; default still terminates.

---

## 9. Cross-Cutting Change Inventory

**Frontend — `frontend/session/`**
- `components/interview/proctoring/vision/face-detector.ts` — **new** (FaceDetector factory).
- `components/interview/proctoring/vision/reading.ts` — wired up (was dead); thresholds tuned.
- `components/interview/proctoring/vision/gaze.ts` — threshold tuning for side-gaze.
- `components/interview/proctoring/use-vision-guard.ts` — detector throttle; faceCount from detector; ReadingAccumulator integration.
- `components/interview/proctoring/ViolationNoticeOverlay.tsx` — **new** (soft-violation modal).
- `components/interview/proctoring/use-proctoring-controller.ts` — `notice` state; remove soft toast.
- `components/interview/proctoring/ProctoringGuard.tsx` — two-tier arming (`envArmed` via `useConnectionState`); render `ViolationNoticeOverlay`; updated comment.
- `components/interview/proctoring/VisionDebugOverlay.tsx` — detector count readout.
- `components/interview/proctoring/violation-kinds.ts` — `multiple_displays` label.
- `app/interview/[token]/CameraMicStep.tsx` (pre-check) — `isExtended` readiness gate. *(Human-review: camera/mic step flow.)*
- A new in-session screen-change subscription (likely a small `use-display-guard.ts` hook mounted by `ProctoringGuard`, env-armed) for `multiple_displays`.
- `lib/api/candidate-session.ts` — `ProctoringKind` gains `multiple_displays`. *(Human-review: sole API surface.)*
- `public/mediapipe/blaze_face_short_range.tflite` — **new** vendored asset.

**Backend — `backend/nexus/`**
- `app/modules/session/proctoring.py` — `multiple_displays` → soft (`VIOLATION_SEVERITY`).
- `app/modules/session/schemas.py:86` — add `multiple_displays` to the `ProctoringKind` literal.
- Tests under `tests/` for the new classification case.

## 10. Decisions Log (resolved during brainstorm)

- Vision = **Option A** (MediaPipe FaceDetector alongside the landmarker), using the supported **short-range** model at `minDetectionConfidence ≈ 0.3`; far/background faces are the server RetinaFace plane's job (full-range Tasks model unavailable — see §5 feasibility note). Detector sampled at ~2–3 fps. Model binary vendored into `public/mediapipe/`.
- Popup is for **soft** violations; **toast removed**; `ViolationBorder` **kept** as accent.
- **Arm-at-connect only**; hard fullscreen gate **deferred**.
- Second screen = **both** a pre-check `isExtended` gate **and** strengthened gaze (wire `ReadingAccumulator`). Display check **folded into `CameraMicStep`**. `multiple_displays` = **soft/counted**.

**As-built amendments (post-brainstorm, 2026-06-15):**
- Multi-display pre-check relaxed from a **hard block** to a **non-blocking warning** (see §8.1) — uniform "warn" signal; unblocks dual-monitor test setups.
- Added the **`PROCTORING_TERMINATION_ENABLED` dry-run toggle** (see §8b) — disables session termination on both planes while keeping all warnings/popups/counters/audit.

## 11. Risks & Mitigations

- **Two MediaPipe graphs on a weak candidate machine** → detector throttled to ~2–3 fps; shared `FilesetResolver`; GPU delegate. Revisit cadence if frame rate drops (debug overlay shows fps).
- **`isExtended` unsupported on some browsers** → graceful no-block; the gaze plane + server plane remain the backstop.
- **Short-range model misses far (>~2m) background faces** → accepted by design; the server-side RetinaFace plane is the authority on tiny/far faces. The client short-range detector still materially upgrades the common close-second-person case over the landmarker. `minDetectionConfidence ≈ 0.3` stretches effective range; revisit if it raises false positives (debug overlay shows the count + confidence).
- **Early-armed focus guard false positives** → arm on `connected` state; permissions already granted pre-`onStart`; covered by tests.
