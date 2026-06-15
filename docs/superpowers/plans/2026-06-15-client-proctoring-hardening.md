# Client Proctoring Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the candidate-session client proctoring: a dedicated face *detector* for robust multi-person counting, a clear soft-violation modal, two-tier arming that closes the pre-start fullscreen evasion gap, and second-screen handling (multi-monitor pre-check gate + in-session signal + wired-up reading detector).

**Architecture:** Four independent phases on `frontend/session` (+ one backend touchpoint). The client plane stays a coarse *deterrent*; the backend is authoritative on severity/termination; accurate far-face gaze remains the server-side `vision` module's job. No new runtime, no CSP change, no dead code.

**Tech Stack:** Next.js 16 / React 19 / TypeScript strict, `@mediapipe/tasks-vision`, LiveKit React components, Vitest + Testing Library (jsdom), FastAPI/pytest (backend).

**Spec:** `docs/superpowers/specs/2026-06-15-client-proctoring-hardening-design.md`

---

## Conventions

- **Frontend test run (single file):** `cd frontend/session && npx vitest run <path>`
- **Frontend full proctoring suite:** `cd frontend/session && npx vitest run tests/components/interview/proctoring`
- **Backend test run:** `cd backend/nexus && docker compose run --rm nexus pytest <path> -q`
- **Type check (frontend):** `cd frontend/session && npm run type-check`
- Commit after every green task. All `components/interview/proctoring/` + `CameraMicStep` + `candidate-session.ts` changes are **Human-Review-Required** surfaces — keep diffs tight.
- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.

---

## Phase 1 — Vision face-detection upgrade (short-range FaceDetector)

Replace the landmarker-derived face *count* with a dedicated MediaPipe `FaceDetector` (short-range, officially supported), sampled at ~3 fps. The landmarker keeps doing head-pose/gaze/blink for the primary face.

### Task 1.1: Vendor the short-range FaceDetector model

**Files:**
- Create: `frontend/session/public/mediapipe/blaze_face_short_range.tflite`
- Create: `frontend/session/public/mediapipe/MODELS.md`

- [ ] **Step 1: Download the official model (same-origin, no CDN at runtime)**

Run:
```bash
cd frontend/session/public/mediapipe
curl -fSL -o blaze_face_short_range.tflite \
  "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
ls -l blaze_face_short_range.tflite
```
Expected: a file of roughly ~200–230 KB is written.

- [ ] **Step 2: Document provenance + license**

Create `frontend/session/public/mediapipe/MODELS.md`:
```markdown
# Vendored MediaPipe model assets

These models are served same-origin (no CDN at runtime) per the candidate-surface
no-third-party rule. Loaded by `components/interview/proctoring/vision/*`.

| File | Task | Source | License |
|---|---|---|---|
| `face_landmarker.task` | FaceLandmarker (head pose, blink) | MediaPipe model storage | Apache-2.0 |
| `blaze_face_short_range.tflite` | FaceDetector (multi-face count, ~2m) | https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite | Apache-2.0 |

The Tasks API ships no full-range FaceDetector model; far/background faces are the
server-side `vision` (RetinaFace) plane's responsibility. See
`docs/superpowers/specs/2026-06-15-client-proctoring-hardening-design.md` §5.
```

- [ ] **Step 3: Commit**

```bash
cd frontend/session
git add public/mediapipe/blaze_face_short_range.tflite public/mediapipe/MODELS.md
git commit -m "feat(proctoring): vendor short-range FaceDetector model + provenance"
```

### Task 1.2: Memoize the shared MediaPipe WASM fileset

**Files:**
- Modify: `frontend/session/components/interview/proctoring/vision/face-landmarker.ts`

- [ ] **Step 1: Replace the file with a memoized-fileset version**

Replace the entire contents of `face-landmarker.ts` with:
```ts
import type { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision'

type VisionFileset = Awaited<ReturnType<typeof FilesetResolver.forVisionTasks>>

let filesetPromise: Promise<VisionFileset> | null = null

/**
 * Memoized MediaPipe WASM fileset, shared by the FaceLandmarker AND the
 * FaceDetector so the WASM runtime is resolved once. SAME-ORIGIN (public/mediapipe/wasm).
 */
export function visionFileset(): Promise<VisionFileset> {
  filesetPromise ??= (async () => {
    const { FilesetResolver } = await import('@mediapipe/tasks-vision')
    return FilesetResolver.forVisionTasks('/mediapipe/wasm')
  })()
  return filesetPromise
}

/**
 * Lazily create a MediaPipe FaceLandmarker configured for live proctoring.
 * WASM + model are SAME-ORIGIN (public/mediapipe/*). Used ONLY for head pose +
 * blink of the primary face — the authoritative face COUNT is the FaceDetector
 * (see face-detector.ts). Dynamic import keeps the heavy SDK out of the
 * pre-/start bundle (LiveKit-bearing route only).
 */
export async function createFaceLandmarker(): Promise<FaceLandmarker> {
  const { FaceLandmarker } = await import('@mediapipe/tasks-vision')
  const fileset = await visionFileset()
  return FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/face_landmarker.task',
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    numFaces: 1, // pose/blink of the primary face only; count comes from FaceDetector
    outputFaceBlendshapes: true, // blink
    outputFacialTransformationMatrixes: true, // head pose
  })
}

/** Read a named blendshape score (0..1) from a FaceLandmarker category list. */
export function blendshape(
  categories: Array<{ categoryName: string; score: number }> | undefined,
  name: string,
): number {
  if (!categories) return 0
  const c = categories.find((x) => x.categoryName === name)
  return c ? c.score : 0
}
```

Note: `numFaces` drops to `1` — the landmarker is no longer the count source, so detecting a second face here is wasted work (and it was unreliable anyway). This removes the now-misleading `numFaces: 2`.

- [ ] **Step 2: Type-check**

Run: `cd frontend/session && npm run type-check`
Expected: PASS (no type errors).

- [ ] **Step 3: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/vision/face-landmarker.ts
git commit -m "refactor(proctoring): memoized shared vision fileset; landmarker numFaces=1"
```

### Task 1.3: FaceDetector factory + result summarizer

**Files:**
- Create: `frontend/session/components/interview/proctoring/vision/face-detector.ts`
- Test: `frontend/session/tests/components/interview/proctoring/vision-face-detector.test.ts`

- [ ] **Step 1: Write the failing test**

Create `tests/components/interview/proctoring/vision-face-detector.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { summarizeDetections } from '@/components/interview/proctoring/vision/face-detector'

describe('summarizeDetections', () => {
  it('returns 0 / 0 confidence for no detections', () => {
    expect(summarizeDetections({ detections: [] })).toEqual({ faceCount: 0, topConfidence: 0 })
    expect(summarizeDetections({})).toEqual({ faceCount: 0, topConfidence: 0 })
  })

  it('counts faces and reports the highest confidence', () => {
    const r = {
      detections: [
        { categories: [{ score: 0.62 }] },
        { categories: [{ score: 0.91 }] },
      ],
    }
    expect(summarizeDetections(r)).toEqual({ faceCount: 2, topConfidence: 0.91 })
  })

  it('treats a detection with no category score as confidence 0', () => {
    expect(summarizeDetections({ detections: [{}] })).toEqual({ faceCount: 1, topConfidence: 0 })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/vision-face-detector.test.ts`
Expected: FAIL — cannot resolve `face-detector` / `summarizeDetections` is not a function.

- [ ] **Step 3: Create the implementation**

Create `components/interview/proctoring/vision/face-detector.ts`:
```ts
import type { FaceDetector } from '@mediapipe/tasks-vision'
import { visionFileset } from './face-landmarker'

/** Distilled face-count signal from a FaceDetector VIDEO result. */
export interface FaceCountSummary {
  faceCount: number
  /** Highest per-face detection confidence in [0,1] (0 when no face). */
  topConfidence: number
}

/**
 * Lazily create a MediaPipe FaceDetector for live multi-face counting. Uses the
 * officially-supported SHORT-RANGE BlazeFace model (the Tasks API ships no
 * full-range model; far/background faces are the server RetinaFace plane's job —
 * see spec §5). A dedicated detector returns ALL faces in range with boxes,
 * unlike the FaceLandmarker which returns only the dominant face. SAME-ORIGIN.
 */
export async function createFaceDetector(): Promise<FaceDetector> {
  const { FaceDetector } = await import('@mediapipe/tasks-vision')
  const fileset = await visionFileset()
  return FaceDetector.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/blaze_face_short_range.tflite',
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    minDetectionConfidence: 0.3, // lowered from 0.5 default to stretch effective range
  })
}

/** Distil a FaceDetector VIDEO result into a count + top confidence. */
export function summarizeDetections(result: {
  detections?: Array<{ categories?: Array<{ score: number }> }>
}): FaceCountSummary {
  const detections = result.detections ?? []
  let topConfidence = 0
  for (const d of detections) {
    const score = d.categories?.[0]?.score ?? 0
    if (score > topConfidence) topConfidence = score
  }
  return { faceCount: detections.length, topConfidence }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/vision-face-detector.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/vision/face-detector.ts tests/components/interview/proctoring/vision-face-detector.test.ts
git commit -m "feat(proctoring): short-range FaceDetector factory + result summarizer"
```

### Task 1.4: Wire the detector into use-vision-guard (throttled count source)

**Files:**
- Modify: `frontend/session/components/interview/proctoring/use-vision-guard.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-vision-guard.test.tsx`

- [ ] **Step 1: Rewrite the hook test for the detector-sourced count**

Replace the entire contents of `tests/components/interview/proctoring/use-vision-guard.test.tsx` with:
```tsx
import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { lmDetect, detDetect } = vi.hoisted(() => ({ lmDetect: vi.fn(), detDetect: vi.fn() }))

vi.mock('@/components/interview/proctoring/vision/face-landmarker', async () => {
  const actual = await vi.importActual<
    typeof import('@/components/interview/proctoring/vision/face-landmarker')
  >('@/components/interview/proctoring/vision/face-landmarker')
  return { ...actual, createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo: lmDetect, close: vi.fn() }) }
})
vi.mock('@/components/interview/proctoring/vision/face-detector', async () => {
  const actual = await vi.importActual<
    typeof import('@/components/interview/proctoring/vision/face-detector')
  >('@/components/interview/proctoring/vision/face-detector')
  return { ...actual, createFaceDetector: vi.fn().mockResolvedValue({ detectForVideo: detDetect, close: vi.fn() }) }
})

const fakeTrack = { attach: vi.fn(), detach: vi.fn() }
vi.mock('@livekit/components-react', () => ({
  useLocalParticipant: () => ({
    localParticipant: { getTrackPublication: () => ({ track: fakeTrack, isMuted: false }) },
  }),
}))

import { useVisionGuard } from '@/components/interview/proctoring/use-vision-guard'

const IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
const c = Math.cos(Math.PI / 6)
const s = Math.sin(Math.PI / 6)
// Ry(+30deg) -> yaw +30 -> 'right'; Ry(-30deg) -> yaw -30 -> 'left' (both off-screen)
const RIGHT = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]
const LEFT = [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, 0, 0, 0, 1]

function lmFrame(matrices: number[][]) {
  return {
    faceLandmarks: matrices.map(() => []),
    faceBlendshapes: matrices.map(() => ({ categories: [] })),
    facialTransformationMatrixes: matrices.map((data) => ({ data })),
  }
}
function detFrame(n: number) {
  return { detections: Array.from({ length: n }, () => ({ categories: [{ score: 0.9 }] })) }
}

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(HTMLMediaElement.prototype, 'readyState', { get: () => 4, configurable: true })
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 16) as unknown as number,
  )
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  // sensible defaults; individual tests override
  lmDetect.mockReturnValue(lmFrame([IDENT]))
  detDetect.mockReturnValue(detFrame(1))
})

afterEach(() => {
  delete (HTMLMediaElement.prototype as unknown as { readyState?: unknown }).readyState
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  lmDetect.mockReset()
  detDetect.mockReset()
})

describe('useVisionGuard', () => {
  it('does nothing when not armed', () => {
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: false, onViolation }))
    expect(lmDetect).not.toHaveBeenCalled()
    expect(detDetect).not.toHaveBeenCalled()
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('a single forward-facing face is center gaze with no violation', async () => {
    const onViolation = vi.fn()
    const { result } = renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(800) })
    expect(result.current.signals.gazeZone).toBe('center')
    expect(result.current.signals.faceCount).toBe(1)
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('fires multiple_faces from the DETECTOR count, not the landmarker', async () => {
    lmDetect.mockReturnValue(lmFrame([IDENT])) // landmarker still sees one
    detDetect.mockReturnValue(detFrame(2)) // detector sees two
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(onViolation).toHaveBeenCalledWith('multiple_faces')
  })

  it('fires face_not_visible when the detector sees zero faces', async () => {
    detDetect.mockReturnValue(detFrame(0))
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(onViolation).toHaveBeenCalledWith('face_not_visible')
  })

  it('fires looking_away_sustained when the head stays turned off-screen', async () => {
    lmDetect.mockReturnValue(lmFrame([RIGHT]))
    detDetect.mockReturnValue(detFrame(1))
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(1500) })
    expect(onViolation).toHaveBeenCalledWith('looking_away_sustained')
  })

  it('samples the detector far less often than the landmarker (throttle)', async () => {
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(2000) })
    // landmarker runs ~every 16ms; detector ~every 350ms.
    expect(detDetect.mock.calls.length).toBeLessThan(lmDetect.mock.calls.length)
    expect(detDetect.mock.calls.length).toBeLessThanOrEqual(8)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-vision-guard.test.tsx`
Expected: FAIL — the hook still derives faceCount from the landmarker and does not import the detector.

- [ ] **Step 3: Rewrite the hook to source the count from the throttled detector**

Replace the entire contents of `components/interview/proctoring/use-vision-guard.ts` with:
```ts
'use client'

import { useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { createFaceDetector, summarizeDetections, type FaceCountSummary } from './vision/face-detector'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, blinkScore, isBlinking, signalQuality, poseToGazePoint } from './vision/gaze'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'

const GAZE_TRAIL_MAX = 24 // recent gaze points kept for the dev fading trail
const DETECT_INTERVAL_MS = 350 // ~3fps face-count sampling (presence is slow-moving)

const EMPTY: VisionSignals = {
  faceCount: 0, pose: null, gazeZone: null, gazePoint: null, gazeTrail: [],
  blinking: false, earValue: null, quality: 'unscorable', fps: 0,
}

type Model = { detectForVideo: (v: HTMLVideoElement, t: number) => unknown; close: () => void } | null

export interface UseVisionGuardArgs {
  armed: boolean
  /** Fired once per sustained occurrence (rising edge), re-armed when the
   * condition clears. Wired to the proctoring controller's report() so vision
   * violations count toward the shared soft-violation limit. */
  onViolation: (kind: VisionNudgeKind) => void
}

export interface VisionGuardState {
  signals: VisionSignals
}

export function useVisionGuard({ armed, onViolation }: UseVisionGuardArgs): VisionGuardState {
  const { localParticipant } = useLocalParticipant()
  const participantRef = useRef(localParticipant)
  const onViolationRef = useRef(onViolation)
  useEffect(() => {
    participantRef.current = localParticipant
    onViolationRef.current = onViolation
  })

  const [signals, setSignals] = useState<VisionSignals>(EMPTY)

  useEffect(() => {
    if (!armed) return
    let cancelled = false
    let raf = 0
    let landmarker: Model = null
    let detector: Model = null
    let last = performance.now()
    let trail: { x: number; y: number }[] = []
    // Authoritative face COUNT comes from the throttled detector.
    let lastDetectAt = 0
    let faceSummary: FaceCountSummary = { faceCount: 0, topConfidence: 0 }
    const since: Partial<Record<VisionNudgeKind, number>> = {}
    const fired = new Set<VisionNudgeKind>()

    const maybeFire = (kind: VisionNudgeKind, active: boolean, now: number) => {
      if (!active) {
        delete since[kind]
        fired.delete(kind)
        return
      }
      since[kind] ??= now
      if (!fired.has(kind) && now - since[kind]! >= NUDGE_SUSTAIN_MS[kind]) {
        fired.add(kind)
        onViolationRef.current(kind)
      }
    }

    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true

    const pub = participantRef.current.getTrackPublication(Track.Source.Camera)
    const track = pub?.track
    if (track) track.attach(video)

    const tick = () => {
      if (cancelled || !landmarker) return
      if (video.readyState < 2) { raf = requestAnimationFrame(tick); return } // wait for a decoded frame
      const now = performance.now()
      const fps = 1000 / Math.max(1, now - last)
      last = now

      // Landmarker every frame: head pose + blink of the PRIMARY face only.
      const lm = landmarker.detectForVideo(video, now) as {
        faceBlendshapes?: Array<{ categories: Array<{ categoryName: string; score: number }> }>
        facialTransformationMatrixes?: Array<{ data: number[] }>
      }
      const cats = lm.faceBlendshapes?.[0]?.categories
      const mtx = lm.facialTransformationMatrixes?.[0]?.data

      // Detector throttled: authoritative multi/zero-face COUNT.
      if (detector && now - lastDetectAt >= DETECT_INTERVAL_MS) {
        lastDetectAt = now
        faceSummary = summarizeDetections(
          detector.detectForVideo(video, now) as { detections?: Array<{ categories?: Array<{ score: number }> }> },
        )
      }
      const faceCount = faceSummary.faceCount

      // HEAD-POSE-ONLY gaze: live plane is a coarse DETERRENT (accurate gaze is
      // the server model). Pose derives from the landmarker matrix, independent
      // of the detector count.
      const pose = mtx ? matrixToHeadPose(mtx) : null
      const ear = cats ? blinkScore(blendshape(cats, 'eyeBlinkLeft'), blendshape(cats, 'eyeBlinkRight')) : null
      const quality = signalQuality({
        faceConfidence: faceSummary.topConfidence,
        brightness: 0.5, // brightness/glare proxies refined in a later plan
        eyeGlare: 0,
      })
      const zone = pose ? classifyGazeZone(pose) : null
      const gazePoint = pose ? poseToGazePoint(pose) : null
      if (gazePoint) trail = [...trail, gazePoint].slice(-GAZE_TRAIL_MAX)

      setSignals({
        faceCount, pose, gazeZone: zone, gazePoint, gazeTrail: trail,
        blinking: ear !== null && isBlinking(ear), earValue: ear, quality, fps,
      })

      maybeFire('multiple_faces', faceCount >= 2, now)
      maybeFire('face_not_visible', faceCount === 0, now)
      maybeFire('looking_away_sustained', zone !== null && zone !== 'center', now)

      raf = requestAnimationFrame(tick)
    }

    Promise.all([createFaceLandmarker(), createFaceDetector()]).then(([lm, det]) => {
      if (cancelled) { lm.close?.(); det.close?.(); return }
      landmarker = lm as Model
      detector = det as Model
      void video.play()?.catch(() => {})
      raf = requestAnimationFrame(tick)
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      if (track) track.detach(video)
      landmarker?.close()
      detector?.close()
      setSignals(EMPTY)
    }
  }, [armed])

  return { signals }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-vision-guard.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/use-vision-guard.ts tests/components/interview/proctoring/use-vision-guard.test.tsx
git commit -m "feat(proctoring): detector-sourced face count with ~3fps throttle"
```

### Task 1.5: Keep the ProctoringGuard composition test green (mock the detector)

**Files:**
- Modify: `frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`

- [ ] **Step 1: Run the composition test to see it break**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`
Expected: FAIL — `useVisionGuard` now imports `createFaceDetector`, which is unmocked and tries to load MediaPipe in jsdom.

- [ ] **Step 2: Add a face-detector mock alongside the existing landmarker mock**

In `proctoring-guard.composition.test.tsx`, immediately after the existing `vi.mock('@/components/interview/proctoring/vision/face-landmarker', …)` block, add:
```ts
vi.mock('@/components/interview/proctoring/vision/face-detector', () => ({
  createFaceDetector: vi.fn().mockResolvedValue({
    detectForVideo: vi.fn(() => ({ detections: [] })),
    close: vi.fn(),
  }),
  summarizeDetections: (r: { detections?: unknown[] }) => ({
    faceCount: r.detections?.length ?? 0,
    topConfidence: 0,
  }),
}))
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 4: Run the whole proctoring suite as a Phase-1 checkpoint**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring`
Expected: PASS (all proctoring tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add tests/components/interview/proctoring/proctoring-guard.composition.test.tsx
git commit -m "test(proctoring): mock FaceDetector in guard composition test"
```

---

## Phase 2 — Soft-violation notice popup

Replace the easy-to-miss soft-violation toast with a modal notice styled like the grace overlays. `ViolationBorder` stays as a brief accent (already rendered). Hard violations are unchanged (terminate → `ProctoringEndedScreen`).

### Task 2.1: ViolationNoticeOverlay component

**Files:**
- Create: `frontend/session/components/interview/proctoring/ViolationNoticeOverlay.tsx`
- Test: `frontend/session/tests/components/interview/proctoring/violation-notice-overlay.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `tests/components/interview/proctoring/violation-notice-overlay.test.tsx`:
```tsx
import { render, screen, fireEvent, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ViolationNoticeOverlay } from '@/components/interview/proctoring/ViolationNoticeOverlay'

afterEach(() => vi.useRealTimers())

describe('ViolationNoticeOverlay', () => {
  it('shows the violation label and the warning count', () => {
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={2} limit={3} onAcknowledge={vi.fn()} />,
    )
    expect(screen.getByText(/keyboard activity/i)).toBeInTheDocument()
    expect(screen.getByText(/warning 2 of 3/i)).toBeInTheDocument()
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

  it('calls onAcknowledge when the button is clicked', () => {
    const onAck = vi.fn()
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={1} limit={3} onAcknowledge={onAck} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /i understand/i }))
    expect(onAck).toHaveBeenCalledTimes(1)
  })

  it('auto-dismisses after the timeout', () => {
    vi.useFakeTimers()
    const onAck = vi.fn()
    render(
      <ViolationNoticeOverlay kind="keyboard" softCount={1} limit={3} onAcknowledge={onAck} />,
    )
    act(() => { vi.advanceTimersByTime(6000) })
    expect(onAck).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/violation-notice-overlay.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the component**

Create `components/interview/proctoring/ViolationNoticeOverlay.tsx`:
```tsx
'use client'

import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import type { ProctoringKind } from '@/lib/api/candidate-session'
import { VIOLATION_LABEL } from './violation-kinds'

const NOTICE_AUTO_MS = 6000 // notices self-dismiss so a live interview is never blocked

/**
 * Modal notice for a SOFT proctoring violation — styled like the grace overlays
 * (FullscreenGraceOverlay/FocusGraceOverlay) so the warning is unmissable. The
 * scrim is visual only; LiveKit audio + the agent keep running underneath. Hard
 * violations do NOT use this (they terminate → ProctoringEndedScreen).
 */
export function ViolationNoticeOverlay({
  kind,
  softCount,
  limit,
  onAcknowledge,
}: {
  kind: ProctoringKind
  softCount: number
  limit: number
  onAcknowledge: () => void
}) {
  useEffect(() => {
    const t = window.setTimeout(onAcknowledge, NOTICE_AUTO_MS)
    return () => window.clearTimeout(t)
  }, [onAcknowledge])

  return (
    <div
      role="alertdialog"
      aria-live="assertive"
      aria-label="Proctoring warning"
      className="fixed inset-0 z-[70] grid place-items-center bg-black/60 backdrop-blur-xl"
    >
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10 text-center">
        <h2 className="font-serif text-2xl text-px-fg">Please keep to the interview rules</h2>
        <p className="mt-3 text-sm text-px-fg-3">
          We noticed <span className="font-semibold text-px-caution">{VIOLATION_LABEL[kind]}</span>. This is{' '}
          <span className="font-mono font-bold text-px-caution">
            warning {softCount} of {limit}
          </span>
          . Repeated warnings will end your interview.
        </p>
        <Button
          size="lg"
          onClick={onAcknowledge}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          I understand
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/violation-notice-overlay.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/ViolationNoticeOverlay.tsx tests/components/interview/proctoring/violation-notice-overlay.test.tsx
git commit -m "feat(proctoring): soft-violation notice modal overlay"
```

### Task 2.2: Controller emits a `notice` and drops the soft toast

**Files:**
- Modify: `frontend/session/components/interview/proctoring/use-proctoring-controller.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-proctoring-controller.test.tsx`

- [ ] **Step 1: Add the failing assertions to the controller test**

Append this `describe` block to `tests/components/interview/proctoring/use-proctoring-controller.test.tsx` (after the existing `describe`):
```tsx
import { toast } from 'sonner'

describe('useProctoringController — soft notice', () => {
  it('sets a notice and does NOT toast.warning on a soft violation', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 1,
    })
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated: vi.fn() }),
    )
    await act(async () => {
      await result.current.report('keyboard')
    })
    expect(result.current.notice).toMatchObject({ kind: 'keyboard', softCount: 1, limit: 3 })
    expect(toast.warning).not.toHaveBeenCalled()
  })

  it('dismissNotice clears the notice', async () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: false, violation_count: 1, soft_violation_count: 1,
    })
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: cfg, onTerminated: vi.fn() }),
    )
    await act(async () => { await result.current.report('keyboard') })
    act(() => { result.current.dismissNotice() })
    expect(result.current.notice).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-proctoring-controller.test.tsx`
Expected: FAIL — `notice` / `dismissNotice` are undefined; `toast.warning` is still called.

- [ ] **Step 3: Implement the notice state and remove the soft toast**

In `components/interview/proctoring/use-proctoring-controller.ts`:

(a) Add the `ViolationNotice` interface and extend `ProctoringController` (after the `BorderFlash` interface):
```ts
export interface ViolationNotice {
  kind: ProctoringKind
  softCount: number
  limit: number
  key: number
}
```
and change the `ProctoringController` interface to:
```ts
export interface ProctoringController {
  report: (kind: ProctoringKind) => Promise<void>
  flash: BorderFlash | null
  notice: ViolationNotice | null
  dismissNotice: () => void
}
```

(b) Add state inside the hook (after the existing `const softCount = useRef(0)` line):
```ts
  const [notice, setNotice] = useState<ViolationNotice | null>(null)
  const noticeKey = useRef(0)
  const dismissNotice = useCallback(() => setNotice(null), [])
```

(c) Replace the soft-violation toast block:
```ts
      // Soft: warn, then let the backend decide the threshold.
      softCount.current += 1
      toast.warning(
        `Warning ${softCount.current} of ${config.soft_violation_limit}: please avoid ${VIOLATION_LABEL[kind]}.`,
      )
```
with:
```ts
      // Soft: surface a modal notice, then let the backend decide the threshold.
      softCount.current += 1
      noticeKey.current += 1
      setNotice({
        kind,
        softCount: softCount.current,
        limit: config.soft_violation_limit,
        key: noticeKey.current,
      })
```

(d) Update the dependency array of the `report` callback and the return value:
```ts
    [token, config.soft_violation_limit, terminate],
  )

  return { report, flash, notice, dismissNotice }
}
```
Note: `VIOLATION_LABEL` is still imported and used by the hard-violation `toast.error` line — keep the import. `useState` is already imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-proctoring-controller.test.tsx`
Expected: PASS (all tests, old + new).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/use-proctoring-controller.ts tests/components/interview/proctoring/use-proctoring-controller.test.tsx
git commit -m "feat(proctoring): controller emits soft-violation notice; drop soft toast"
```

### Task 2.3: Render the notice in ProctoringGuard

**Files:**
- Modify: `frontend/session/components/interview/proctoring/ProctoringGuard.tsx`

- [ ] **Step 1: Import and render the overlay**

In `ProctoringGuard.tsx`, add the import (next to the other overlay imports):
```ts
import { ViolationNoticeOverlay } from './ViolationNoticeOverlay'
```
Then, in the returned JSX, immediately after the `{cfg.enabled && <ViolationBorder flash={controller.flash} />}` line, add:
```tsx
      {cfg.enabled && controller.notice && (
        <ViolationNoticeOverlay
          key={controller.notice.key}
          kind={controller.notice.kind}
          softCount={controller.notice.softCount}
          limit={controller.notice.limit}
          onAcknowledge={controller.dismissNotice}
        />
      )}
```

- [ ] **Step 2: Verify the proctoring suite + type-check**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring && npm run type-check`
Expected: PASS (suite green, no type errors).

- [ ] **Step 3: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/ProctoringGuard.tsx
git commit -m "feat(proctoring): render soft-violation notice overlay in guard"
```

---

## Phase 3 — Two-tier arming (close the pre-start fullscreen gap)

Environment guards (fullscreen/focus/visibility/keyboard/devtools) arm at LiveKit **connect**; only the vision guard keeps the post-speech settle.

### Task 3.1: Split arming into env-armed vs vision-armed

**Files:**
- Modify: `frontend/session/components/interview/proctoring/ProctoringGuard.tsx`
- Test: `frontend/session/tests/components/interview/proctoring/proctoring-arming.test.tsx`

- [ ] **Step 1: Write the failing test (env guard arms at connect, before any speech)**

Create `tests/components/interview/proctoring/proctoring-arming.test.tsx`:
```tsx
import { render, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { candidateSessionApi } from '@/lib/api/candidate-session'
import { ProctoringGuard } from '@/components/interview/proctoring/ProctoringGuard'

// Agent has NOT started speaking ('initializing'); the room IS connected.
const lkState = { voiceState: 'initializing', isConnected: true }
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: lkState.voiceState }),
  useSessionContext: () => ({ end: vi.fn(), isConnected: lkState.isConnected }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
}))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', () => ({
  createFaceLandmarker: vi.fn().mockResolvedValue({
    detectForVideo: vi.fn(() => ({ facialTransformationMatrixes: [], faceBlendshapes: [] })),
    close: vi.fn(),
  }),
  blendshape: () => 0,
}))
vi.mock('@/components/interview/proctoring/vision/face-detector', () => ({
  createFaceDetector: vi.fn().mockResolvedValue({
    detectForVideo: vi.fn(() => ({ detections: [] })),
    close: vi.fn(),
  }),
  summarizeDetections: () => ({ faceCount: 0, topConfidence: 0 }),
}))
vi.mock('sonner', () => ({ toast: { warning: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/env', () => ({
  env: { NEXT_PUBLIC_API_URL: 'http://localhost:8000', NEXT_PUBLIC_PROCTORING_DEBUG: false },
}))

afterEach(() => {
  vi.restoreAllMocks()
  lkState.voiceState = 'initializing'
  lkState.isConnected = true
})

const cfg = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('ProctoringGuard two-tier arming', () => {
  it('arms the visibility (env) guard at connect even before the agent speaks', () => {
    vi.spyOn(candidateSessionApi, 'proctoringEvent').mockResolvedValue({
      terminated: true, violation_count: 1, soft_violation_count: 0,
    })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live</div>
      </ProctoringGuard>,
    )
    // No timer advance, agent never spoke — env guard is armed purely on connect.
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).toHaveBeenCalledWith('tab_switch')
  })

  it('does NOT arm env guards before the room is connected', () => {
    lkState.isConnected = false
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    const onTerminated = vi.fn()
    render(
      <ProctoringGuard token="t" config={cfg} onTerminated={onTerminated}>
        <div>live</div>
      </ProctoringGuard>,
    )
    act(() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(onTerminated).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-arming.test.tsx`
Expected: FAIL — the guard currently waits for agent speech + an 800ms settle, so a connect-only state does not arm the visibility guard.

- [ ] **Step 3: Implement two-tier arming**

In `ProctoringGuard.tsx`:

(a) Change the LiveKit import:
```ts
import { useVoiceAssistant } from '@livekit/components-react'
```
to:
```ts
import { useVoiceAssistant, useSessionContext } from '@livekit/components-react'
```

(b) Replace the arming block:
```ts
  const cfg = config ?? DISABLED
  const { state } = useVoiceAssistant()
  const [armed, setArmed] = useState(false)

  // Arm only once the agent is live + a short settle window, so the LiveKit
  // connect, media publish, and the start-gesture fullscreen entry all settle
  // before enforcement begins (prevents self-inflicted terminations).
  useEffect(() => {
    if (armed || !cfg.enabled) return
    if (state === 'listening' || state === 'thinking' || state === 'speaking') {
      const t = setTimeout(() => setArmed(true), ARM_SETTLE_MS)
      return () => clearTimeout(t)
    }
  }, [state, armed, cfg.enabled])

  const controller = useProctoringController({ token, config: cfg, onTerminated })
  const enforce = armed && cfg.enabled

  useVisibilityGuard({ armed: enforce, onViolation: controller.report })
  useKeyboardGuard({ armed: enforce, onViolation: controller.report })
  useDevtoolsGuard({ armed: enforce, onViolation: controller.report })
  const focus = useFocusGuard({
    armed: enforce,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  const fs = useFullscreenGuard({
    armed: enforce,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  // Vision violations report through the SAME controller as the behavioral
  // guards: soft, counted toward the shared limit, backend-terminated on
  // escalation (same toast + border flash as tab-switch etc.).
  const vision = useVisionGuard({ armed: enforce, onViolation: controller.report })
```
with:
```ts
  const cfg = config ?? DISABLED
  const { state } = useVoiceAssistant()
  const ctx = useSessionContext() as unknown as { isConnected?: boolean }
  const connected = !!ctx?.isConnected
  const [visionSettled, setVisionSettled] = useState(false)

  // Two-tier arming. ENV guards (fullscreen/focus/visibility/keyboard/devtools)
  // arm as soon as the room is CONNECTED — they don't need the camera, so the
  // pre-conversation window is monitored identically to mid-interview (closes
  // the pre-start fullscreen-exit gap). The VISION guard waits an extra settle
  // after the agent goes live so the candidate getting seated doesn't
  // self-trigger a "looking away" nudge.
  useEffect(() => {
    if (visionSettled || !cfg.enabled) return
    if (state === 'listening' || state === 'thinking' || state === 'speaking') {
      const t = setTimeout(() => setVisionSettled(true), ARM_SETTLE_MS)
      return () => clearTimeout(t)
    }
  }, [state, visionSettled, cfg.enabled])

  const controller = useProctoringController({ token, config: cfg, onTerminated })
  const envArmed = cfg.enabled && connected
  const visionArmed = envArmed && visionSettled

  useVisibilityGuard({ armed: envArmed, onViolation: controller.report })
  useKeyboardGuard({ armed: envArmed, onViolation: controller.report })
  useDevtoolsGuard({ armed: envArmed, onViolation: controller.report })
  const focus = useFocusGuard({
    armed: envArmed,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  const fs = useFullscreenGuard({
    armed: envArmed,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  // Vision reports through the SAME controller (soft, counted). Armed only after
  // the agent-speech settle so seating movement doesn't self-trigger a nudge.
  const vision = useVisionGuard({ armed: visionArmed, onViolation: controller.report })
```

- [ ] **Step 4: Run the new arming test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-arming.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/ProctoringGuard.tsx tests/components/interview/proctoring/proctoring-arming.test.tsx
git commit -m "feat(proctoring): two-tier arming — env guards arm at connect"
```

### Task 3.2: Fix the composition test for the connect-gated arming

**Files:**
- Modify: `frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`

- [ ] **Step 1: Run the composition test to see the regression**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`
Expected: FAIL — its `useSessionContext` mock returns `{ end }` with no `isConnected`, so `connected` is false and env guards never arm.

- [ ] **Step 2: Add `isConnected: true` to the session-context mock**

In `proctoring-guard.composition.test.tsx`, change:
```ts
  useSessionContext: () => ({ end: endMock }),
```
to:
```ts
  useSessionContext: () => ({ end: endMock, isConnected: true }),
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`
Expected: PASS (3 tests). The `vi.advanceTimersByTime(900)` in the first test is now harmless (env guard already armed at connect).

- [ ] **Step 4: Commit**

```bash
cd frontend/session
git add tests/components/interview/proctoring/proctoring-guard.composition.test.tsx
git commit -m "test(proctoring): composition test connects the session for env arming"
```

---

## Phase 4 — Second-screen handling

A new soft `multiple_displays` kind, a permission-free multi-monitor pre-check gate, an in-session display guard, and the wired-up reading detector.

### Task 4.1: Add the `multiple_displays` kind (backend, TDD)

**Files:**
- Modify: `backend/nexus/app/modules/session/proctoring.py`
- Modify: `backend/nexus/app/modules/session/schemas.py:86`
- Test: `backend/nexus/tests/test_session_proctoring.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/test_session_proctoring.py`:
```python
def test_multiple_displays_is_soft():
    assert classify_severity("multiple_displays") == "soft"


def test_multiple_displays_over_limit_terminates_via_shared_counter():
    terminal, outcome = decide_termination(
        kind="multiple_displays", soft_count_including_new=4, soft_limit=3
    )
    assert terminal is True
    assert outcome == "soft_threshold_exceeded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_session_proctoring.py -q`
Expected: FAIL — `KeyError: 'multiple_displays'` from `classify_severity`.

- [ ] **Step 3: Implement the severity + schema literal**

In `app/modules/session/proctoring.py`, add to the `VIOLATION_SEVERITY` dict (after the vision block):
```python
    # Second-screen: a multi-display setup detected mid-session. SOFT — could be
    # accidental; backend stays authoritative on the threshold. Pre-check also
    # gates multi-display before the interview starts (client). See spec §8.
    "multiple_displays": "soft",
```

In `app/modules/session/schemas.py`, add `"multiple_displays"` to the `ProctoringKind` literal (line 86 block), after `"looking_away_sustained"`:
```python
    "multiple_displays",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_session_proctoring.py tests/test_session_proctoring_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd backend/nexus
git add app/modules/session/proctoring.py app/modules/session/schemas.py tests/test_session_proctoring.py
git commit -m "feat(proctoring): accept multiple_displays soft violation kind"
```

### Task 4.2: Add `multiple_displays` to the frontend kind + label

**Files:**
- Modify: `frontend/session/lib/api/candidate-session.ts:55-67`
- Modify: `frontend/session/components/interview/proctoring/violation-kinds.ts`

- [ ] **Step 1: Add the union member**

In `lib/api/candidate-session.ts`, add to the `ProctoringKind` union after `| 'looking_away_sustained'`:
```ts
  // Second-screen: a multi-display setup detected mid-session (soft, counted).
  | 'multiple_displays'
```

- [ ] **Step 2: Add the label (TS will fail to compile until you do — the Record is exhaustive)**

In `components/interview/proctoring/violation-kinds.ts`, add to `VIOLATION_LABEL` after the `looking_away_sustained` entry:
```ts
  multiple_displays: 'using more than one display',
```
Leave `HARD_KINDS` unchanged — `multiple_displays` is soft.

- [ ] **Step 3: Type-check to verify exhaustiveness is satisfied**

Run: `cd frontend/session && npm run type-check`
Expected: PASS (the `Record<ProctoringKind, string>` now covers the new member).

- [ ] **Step 4: Commit**

```bash
cd frontend/session
git add lib/api/candidate-session.ts components/interview/proctoring/violation-kinds.ts
git commit -m "feat(proctoring): multiple_displays kind + label (frontend)"
```

### Task 4.3: Display-topology helpers

**Files:**
- Create: `frontend/session/lib/proctoring/displays.ts`
- Test: `frontend/session/tests/lib/proctoring/displays.test.ts`

- [ ] **Step 1: Write the failing test**

Create `tests/lib/proctoring/displays.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'

function setScreen(props: Partial<{ isExtended: boolean; addEventListener: unknown; removeEventListener: unknown }>) {
  Object.defineProperty(window, 'screen', { value: { ...props }, configurable: true })
}

afterEach(() => {
  // restore a benign screen object
  Object.defineProperty(window, 'screen', { value: {}, configurable: true })
})

describe('isMultiDisplay', () => {
  it('returns true when screen.isExtended is true', () => {
    setScreen({ isExtended: true })
    expect(isMultiDisplay()).toBe(true)
  })
  it('returns false when screen.isExtended is false', () => {
    setScreen({ isExtended: false })
    expect(isMultiDisplay()).toBe(false)
  })
  it('returns null when the API is unavailable', () => {
    setScreen({})
    expect(isMultiDisplay()).toBeNull()
  })
})

describe('subscribeDisplayChange', () => {
  it('adds and removes a change listener when supported', () => {
    const add = vi.fn()
    const remove = vi.fn()
    setScreen({ isExtended: false, addEventListener: add, removeEventListener: remove })
    const cb = vi.fn()
    const unsub = subscribeDisplayChange(cb)
    expect(add).toHaveBeenCalledWith('change', cb)
    unsub()
    expect(remove).toHaveBeenCalledWith('change', cb)
  })
  it('is a no-op when the API is unavailable', () => {
    setScreen({})
    const unsub = subscribeDisplayChange(vi.fn())
    expect(() => unsub()).not.toThrow()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/lib/proctoring/displays.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the helper**

Create `lib/proctoring/displays.ts`:
```ts
'use client'

/**
 * Multi-display detection via the permission-free Window Management API
 * (`window.screen.isExtended`). Returns `null` when unsupported (Firefox/Safari) —
 * callers MUST treat null as "cannot determine" and NOT block. We deliberately
 * avoid `getScreenDetails()` (it triggers a permission prompt). See spec §8.
 */
export function isMultiDisplay(): boolean | null {
  if (typeof window === 'undefined' || !window.screen) return null
  const ext = (window.screen as Screen & { isExtended?: boolean }).isExtended
  return typeof ext === 'boolean' ? ext : null
}

type ScreenWithEvents = Screen & {
  addEventListener?: (type: 'change', cb: () => void) => void
  removeEventListener?: (type: 'change', cb: () => void) => void
}

/**
 * Subscribe to display-topology changes (a monitor plugged/unplugged). Returns
 * an unsubscribe fn. No-op (returns a noop) when the API is unavailable.
 */
export function subscribeDisplayChange(onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.screen) return () => {}
  const screen = window.screen as ScreenWithEvents
  if (!screen.addEventListener) return () => {}
  screen.addEventListener('change', onChange)
  return () => screen.removeEventListener?.('change', onChange)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/lib/proctoring/displays.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add lib/proctoring/displays.ts tests/lib/proctoring/displays.test.ts
git commit -m "feat(proctoring): permission-free display-topology helpers"
```

### Task 4.4: In-session display guard

**Files:**
- Create: `frontend/session/components/interview/proctoring/use-display-guard.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-display-guard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `tests/components/interview/proctoring/use-display-guard.test.tsx`:
```tsx
import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { isMultiDisplay, subscribeDisplayChange } = vi.hoisted(() => ({
  isMultiDisplay: vi.fn(),
  subscribeDisplayChange: vi.fn(() => () => {}),
}))
vi.mock('@/lib/proctoring/displays', () => ({ isMultiDisplay, subscribeDisplayChange }))

import { useDisplayGuard } from '@/components/interview/proctoring/use-display-guard'

afterEach(() => {
  vi.restoreAllMocks()
  isMultiDisplay.mockReset()
  subscribeDisplayChange.mockReset()
  subscribeDisplayChange.mockReturnValue(() => {})
})

describe('useDisplayGuard', () => {
  it('does nothing when not armed', () => {
    isMultiDisplay.mockReturnValue(true)
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: false, onViolation }))
    expect(onViolation).not.toHaveBeenCalled()
  })

  it('fires multiple_displays when already extended at arm time', () => {
    isMultiDisplay.mockReturnValue(true)
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    expect(onViolation).toHaveBeenCalledWith('multiple_displays')
  })

  it('does not fire when single-display, then fires on a change to extended', () => {
    isMultiDisplay.mockReturnValue(false)
    let changeCb = () => {}
    subscribeDisplayChange.mockImplementation((cb: () => void) => {
      changeCb = cb
      return () => {}
    })
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    expect(onViolation).not.toHaveBeenCalled()
    isMultiDisplay.mockReturnValue(true)
    act(() => { changeCb() })
    expect(onViolation).toHaveBeenCalledWith('multiple_displays')
  })

  it('fires only once while extended (no spam)', () => {
    isMultiDisplay.mockReturnValue(true)
    let changeCb = () => {}
    subscribeDisplayChange.mockImplementation((cb: () => void) => { changeCb = cb; return () => {} })
    const onViolation = vi.fn()
    renderHook(() => useDisplayGuard({ armed: true, onViolation }))
    act(() => { changeCb() })
    act(() => { changeCb() })
    expect(onViolation).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-display-guard.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create the guard**

Create `components/interview/proctoring/use-display-guard.ts`:
```ts
'use client'

import { useEffect, useRef } from 'react'
import type { ProctoringKind } from '@/lib/api/candidate-session'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'

export interface DisplayGuardArgs {
  armed: boolean
  onViolation: (kind: ProctoringKind) => void
}

/**
 * In-session second-screen guard. Fires `multiple_displays` (soft) when a
 * multi-display setup is present at arm time or appears mid-interview. Re-arms
 * when the candidate drops back to a single display. `null` (API unsupported)
 * is treated as single-display — the camera/gaze plane is the backstop there.
 */
export function useDisplayGuard({ armed, onViolation }: DisplayGuardArgs): void {
  const fired = useRef(false)
  const onViolationRef = useRef(onViolation)
  useEffect(() => { onViolationRef.current = onViolation })

  useEffect(() => {
    if (!armed) return
    const check = () => {
      if (isMultiDisplay() === true) {
        if (!fired.current) {
          fired.current = true
          onViolationRef.current('multiple_displays')
        }
      } else {
        fired.current = false // re-arm once the extra display is gone
      }
    }
    check() // catch an already-extended setup at arm time
    return subscribeDisplayChange(check)
  }, [armed])
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-display-guard.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire it into ProctoringGuard (env-armed)**

In `ProctoringGuard.tsx`, add the import:
```ts
import { useDisplayGuard } from './use-display-guard'
```
and add the guard call next to the other env guards (after `useDevtoolsGuard(...)`):
```ts
  useDisplayGuard({ armed: envArmed, onViolation: controller.report })
```

- [ ] **Step 6: Run the proctoring suite + type-check**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring && npm run type-check`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/use-display-guard.ts components/interview/proctoring/ProctoringGuard.tsx tests/components/interview/proctoring/use-display-guard.test.tsx
git commit -m "feat(proctoring): in-session multi-display guard (multiple_displays)"
```

### Task 4.5: Pre-check multi-monitor gate in CameraMicStep

**Files:**
- Modify: `frontend/session/app/interview/[token]/CameraMicStep.tsx`
- Modify: `frontend/session/app/interview/[token]/WizardShell.tsx:141`
- Test: `frontend/session/tests/components/interview/CameraMicStep.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `tests/components/interview/CameraMicStep.test.tsx` a new `describe` (it reuses the file's existing `getUserMediaMock`/`sampleNoiseFloorDbfs` setup; mock the displays helper at the top of the file alongside the existing mocks):

First, add this mock near the other `vi.mock(...)` calls at the top of the file:
```ts
const { isMultiDisplay, subscribeDisplayChange } = vi.hoisted(() => ({
  isMultiDisplay: vi.fn(() => null),
  subscribeDisplayChange: vi.fn(() => () => {}),
}))
vi.mock('@/lib/proctoring/displays', () => ({ isMultiDisplay, subscribeDisplayChange }))
```

Then append this `describe` block at the end of the file:
```tsx
describe('CameraMicStep — multi-display gate', () => {
  it('blocks Continue when proctored and a second display is detected', async () => {
    isMultiDisplay.mockReturnValue(true)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    render(<CameraMicStep onPass={vi.fn()} proctored />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByText(/disconnect additional displays/i)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /continue/i })).toBeNull()
  })

  it('allows Continue when proctored but single-display', async () => {
    isMultiDisplay.mockReturnValue(false)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    render(<CameraMicStep onPass={vi.fn()} proctored />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /continue/i })).toBeInTheDocument())
  })

  it('does not gate when not proctored even if extended', async () => {
    isMultiDisplay.mockReturnValue(true)
    mockSampleNoiseFloorDbfs.mockResolvedValue(-45)
    render(<CameraMicStep onPass={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /test camera/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /continue/i })).toBeInTheDocument())
  })
})
```
Note: if the existing test file already imports `fireEvent`/`waitFor`/`screen`, do not re-import. If `mockSampleNoiseFloorDbfs` is named differently in the file, match the existing name. The existing tests reach `status === 'ready'` via the file's `getUserMediaMock` — reuse that path (the new tests rely on the same `beforeEach` that wires `navigator.mediaDevices.getUserMedia`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/CameraMicStep.test.tsx`
Expected: FAIL — `CameraMicStep` has no `proctored` prop and no display gate.

- [ ] **Step 3: Implement the gate in CameraMicStep**

In `app/interview/[token]/CameraMicStep.tsx`:

(a) Add the import (with the other imports):
```ts
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'
```

(b) Change the `Props` interface and the signature:
```ts
interface Props {
  onPass: () => void
  /** When true, gate Continue on a single-display setup (proctored sessions). */
  proctored?: boolean
}
```
and
```ts
export function CameraMicStep({ onPass, proctored = false }: Props) {
```

(c) Add display state right after the existing `const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)` line:
```ts
  const [multiDisplay, setMultiDisplay] = useState<boolean | null>(null)
  useEffect(() => {
    if (!proctored) return
    const refresh = () => setMultiDisplay(isMultiDisplay())
    refresh()
    return subscribeDisplayChange(refresh)
  }, [proctored])
  const displayBlocked = proctored && multiDisplay === true
```

(d) Replace the `status === 'ready'` block:
```tsx
          {status === 'ready' && (
            <>
              <span
                className="text-sm font-medium"
                style={{ color: 'var(--px-ok)' }}
              >
                Camera and mic are working ✓
              </span>
              <Button onClick={onPass}>Continue →</Button>
            </>
          )}
```
with:
```tsx
          {status === 'ready' && (
            <>
              <span
                className="text-sm font-medium"
                style={{ color: 'var(--px-ok)' }}
              >
                Camera and mic are working ✓
              </span>
              {displayBlocked ? (
                <>
                  <span className="text-sm" style={{ color: 'var(--px-danger)' }} role="status">
                    Please disconnect additional displays to continue.
                  </span>
                  <Button variant="outline" onClick={() => setMultiDisplay(isMultiDisplay())}>
                    Re-check
                  </Button>
                </>
              ) : (
                <Button onClick={onPass}>Continue →</Button>
              )}
            </>
          )}
```

(e) Ensure `useEffect` is imported — the file already imports `{ useEffect, useRef, useState }`, so no change needed.

- [ ] **Step 4: Plumb `proctored` from the wizard**

In `app/interview/[token]/WizardShell.tsx` line 141, change:
```tsx
        <CameraMicStep onPass={() => setCamMicPassed(true)} />
```
to:
```tsx
        <CameraMicStep onPass={() => setCamMicPassed(true)} proctored={data.proctoring_enabled} />
```
(`data` is the loaded pre-check response already used at lines 108/113 as `preCheck={data}`; `proctoring_enabled` is on `PreCheckResponse`.)

- [ ] **Step 5: Run test + type-check to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/CameraMicStep.test.tsx && npm run type-check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd frontend/session
git add app/interview/[token]/CameraMicStep.tsx app/interview/[token]/WizardShell.tsx tests/components/interview/CameraMicStep.test.tsx
git commit -m "feat(proctoring): pre-check multi-display gate in CameraMicStep"
```

### Task 4.6: Wire the ReadingAccumulator into use-vision-guard (strengthen gaze)

**Files:**
- Modify: `frontend/session/components/interview/proctoring/use-vision-guard.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-vision-guard.test.tsx`

- [ ] **Step 1: Add the failing scanning test**

Append this test inside the `describe('useVisionGuard', …)` block in `tests/components/interview/proctoring/use-vision-guard.test.tsx`:
```tsx
  it('fires looking_away_sustained from the reading pattern (no single 1s glance)', async () => {
    // Short off-screen glances left/right with centre returns — no single glance
    // is sustained 1s, but the scanning rhythm is. Proves the ReadingAccumulator path.
    const seq = [RIGHT, RIGHT, IDENT, LEFT, LEFT, IDENT]
    let k = 0
    lmDetect.mockImplementation(() => lmFrame([seq[k++ % seq.length]]))
    detDetect.mockReturnValue(detFrame(1))
    const onViolation = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onViolation }))
    await act(async () => { await Promise.resolve() })
    await act(async () => { vi.advanceTimersByTime(6000) })
    expect(onViolation).toHaveBeenCalledWith('looking_away_sustained')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-vision-guard.test.tsx -t "reading pattern"`
Expected: FAIL — the simple off-center sustain never reaches 1s in this sequence and the reader isn't wired, so `looking_away_sustained` is not fired.

- [ ] **Step 3: Wire the ReadingAccumulator**

In `components/interview/proctoring/use-vision-guard.ts`:

(a) Add the import (with the other vision imports):
```ts
import { ReadingAccumulator } from './vision/reading'
```

(b) Inside the `useEffect`, after `const fired = new Set<VisionNudgeKind>()`, add:
```ts
    const reader = new ReadingAccumulator()
```

(c) Replace the `looking_away_sustained` fire line:
```ts
      maybeFire('looking_away_sustained', zone !== null && zone !== 'center', now)
```
with:
```ts
      // Strengthen gaze: a single sustained off-center glance OR a scanning
      // rhythm (reading an off-screen surface) — the latter catches a second
      // screen even when window focus never changes.
      reader.push(zone ?? 'center', now)
      const offCenter = zone !== null && zone !== 'center'
      maybeFire('looking_away_sustained', offCenter || reader.isReading(), now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/session && npx vitest run tests/components/interview/proctoring/use-vision-guard.test.tsx`
Expected: PASS (all 7 tests — the new scanning test plus the original 6).

- [ ] **Step 5: Commit**

```bash
cd frontend/session
git add components/interview/proctoring/use-vision-guard.ts tests/components/interview/proctoring/use-vision-guard.test.tsx
git commit -m "feat(proctoring): wire ReadingAccumulator into the looking-away signal"
```

---

## Final verification

- [ ] **Frontend: full proctoring + interview suites + type-check**

Run:
```bash
cd frontend/session
npx vitest run tests/components/interview tests/lib/proctoring
npm run type-check
npm run lint
```
Expected: all PASS.

- [ ] **Backend: proctoring tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_session_proctoring.py tests/test_session_proctoring_endpoint.py tests/test_session_proctoring_service.py -q
```
Expected: all PASS.

- [ ] **Manual smoke (talk-test, per the user's testing style)**

1. Start a proctored interview from `frontend/session` on a single display; confirm a second person stepping into frame raises a `multiple_faces` modal notice (not just a toast).
2. With a second monitor attached, confirm the pre-check `CameraMicStep` blocks Continue until disconnected.
3. Click Start, then exit fullscreen DURING the connect/pre-speech window — confirm the grace overlay now appears (gap closed).
4. Plug a second monitor mid-interview — confirm a `multiple_displays` notice.

---

## Self-Review (completed during authoring)

- **Spec coverage:** §5 vision→Phase 1; §6 popup→Phase 2; §7 arming→Phase 3; §8.1 gate→Task 4.5, §8.2 kind→Tasks 4.1–4.2, §8.3 reading→Task 4.6, in-session display signal→Task 4.4. All §9 change-inventory entries map to a task.
- **Placeholders:** none — every code/test step has complete content.
- **Type consistency:** `summarizeDetections`/`FaceCountSummary` (Task 1.3) used verbatim in Task 1.4; `ViolationNotice`/`notice`/`dismissNotice` (Task 2.2) used verbatim in Task 2.3; `envArmed`/`visionArmed` (Task 3.1) consistent; `isMultiDisplay`/`subscribeDisplayChange` (Task 4.3) consistent across Tasks 4.4–4.5; `multiple_displays` literal consistent frontend↔backend.
- **Cross-phase file edits:** `ProctoringGuard.tsx` is edited in Tasks 2.3, 3.1, 4.4 in non-overlapping regions (return-block / arming-block / env-guard-list). `use-vision-guard.ts` is rewritten in 1.4 then a targeted edit in 4.6 against that known content. `use-vision-guard.test.tsx` is rewritten in 1.4 then appended in 4.6.
