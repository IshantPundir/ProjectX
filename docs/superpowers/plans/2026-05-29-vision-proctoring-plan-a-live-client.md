# Vision Proctoring — Plan A: Live Client Vision + Debug Overlay

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-browser MediaPipe face/gaze tracking layer to the candidate interview surface that produces live (non-authoritative) signals + advisory nudges, plus a dev-only debug overlay to watch tracking quality — de-risking the computer-vision approach before the server-side authoritative pipeline (Plan B) is built.

**Architecture:** A new `components/interview/proctoring/vision/` subtree holds (a) pure, unit-tested helpers that turn MediaPipe output into gaze zones / blink / signal-quality / reading signals, (b) a thin lazy loader for the MediaPipe `FaceLandmarker`, and (c) a `useVisionGuard` hook that attaches to the LiveKit local camera track, runs a detection loop, exposes signals, and fires display-only advisory nudges. A dev-gated `VisionDebugOverlay` renders the signals over the candidate's self-view. Nothing is sent to the backend in Plan A (the backend contract is unchanged); nudges are client-side UX only.

**Tech Stack:** Next.js 16 / React 19 / TypeScript strict, `@mediapipe/tasks-vision` (self-hosted WASM + model, no CDN), `@livekit/components-react` (local track), Vitest + Testing Library (jsdom), Tailwind v4.

**Source spec:** `docs/superpowers/specs/2026-05-29-vision-proctoring-design.md` (§5 live plane, §7 detectors, §11 testing).

---

## Scope

**In scope (Plan A):**
- `@mediapipe/tasks-vision` dependency + self-hosted WASM/model assets + CSP note.
- `NEXT_PUBLIC_PROCTORING_DEBUG` env flag.
- Pure helpers: matrix→head-pose, gaze-zone classification, blink/EAR, signal-quality (glasses/low-light), reading-pattern accumulator.
- `FaceLandmarker` lazy loader.
- `useVisionGuard` hook (local-track detection loop → signals + advisory nudges).
- `VisionDebugOverlay` (dev-gated).
- Advisory nudge kinds + display-only controller path + `ProctoringGuard` wiring.
- Welcome-screen disclosure text extension (camera-based monitoring).

**Out of scope (later plans / follow-ups):**
- Backend reporting of vision signals + heartbeats (Plan B — needs backend endpoint changes).
- Server-side authoritative re-analysis, risk scoring, tamper reconciliation (Plan B).
- Report-page surfacing + heatmap (Plan C).
- Full BIPA consent-string versioning + block-on-decline gate (compliance task with backend pre-check changes).
- **Behavioral-guard hardening (fullscreen re-entry / devtools).** Tracked as a separate focused follow-up: needs live repro investigation, orthogonal to Plan A's de-risk goal. Do NOT bundle here.

**Thresholds note:** All numeric thresholds in this plan (gaze-zone angles, EAR blink cutoff, glare/low-light cutoffs, sustained-look-away duration) are **empirical starting values, tuned later via the debug overlay** per spec §11 (shadow mode). They are intentional starting constants, not placeholders.

---

## File Structure

```
frontend/session/
  package.json                                          (modify) add @mediapipe/tasks-vision
  public/mediapipe/                                     (create) self-hosted assets
    wasm/                                                 vision_wasm_internal.{js,wasm} + nosimd variants
    face_landmarker.task                                  the model
  proxy.ts                                              (modify, HUMAN REVIEW) add 'wasm-unsafe-eval' to script-src
  lib/env.ts                                            (modify) NEXT_PUBLIC_PROCTORING_DEBUG
  components/interview/proctoring/
    vision/
      types.ts            (create) VisionSignals, GazeZone, SignalQuality, FacePose, NudgeKind
      head-pose.ts        (create, pure) matrixToHeadPose()
      gaze.ts             (create, pure) classifyGazeZone(), eyeAspectRatio(), isBlinking(), signalQuality()
      reading.ts          (create, pure) ReadingAccumulator
      face-landmarker.ts  (create) createFaceLandmarker() lazy loader
    nudge-kinds.ts        (create) VisionNudgeKind + NUDGE_LABEL + sustained-duration policy
    use-vision-guard.ts   (create) the detection-loop hook
    VisionDebugOverlay.tsx (create) dev-gated overlay
    use-proctoring-controller.ts (modify) add display-only nudge() path
    ProctoringGuard.tsx   (modify) mount useVisionGuard + overlay
  components/interview/app/welcome-view.tsx (modify, HUMAN REVIEW) disclosure text
  tests/components/interview/proctoring/
    vision-head-pose.test.ts   (create)
    vision-gaze.test.ts        (create)
    vision-reading.test.ts     (create)
    use-vision-guard.test.tsx  (create)
    vision-debug-overlay.test.tsx (create)
    vision-nudge-controller.test.tsx (create)
  tests/lib/env.test.ts        (modify) debug-flag cases
```

---

## Task 1: Add MediaPipe dependency + self-host assets

**Files:**
- Modify: `frontend/session/package.json`
- Create: `frontend/session/public/mediapipe/wasm/*`, `frontend/session/public/mediapipe/face_landmarker.task`
- Modify: `frontend/session/proxy.ts` (**HUMAN REVIEW** — CSP)

- [ ] **Step 1: Install the package**

Run (from `frontend/session/`):
```bash
npm install @mediapipe/tasks-vision@^0.10.0
```
Expected: `@mediapipe/tasks-vision` added to `dependencies` in `package.json`, `package-lock.json` updated. (Not on the forbidden-deps list; justify in the PR description: "client-side face/gaze proctoring per vision-proctoring spec.")

- [ ] **Step 2: Self-host the WASM runtime (no CDN — CSP/no-third-party rule)**

Run (from `frontend/session/`):
```bash
mkdir -p public/mediapipe/wasm
cp node_modules/@mediapipe/tasks-vision/wasm/* public/mediapipe/wasm/
```
Expected: `public/mediapipe/wasm/` contains `vision_wasm_internal.js`, `vision_wasm_internal.wasm`, and the `*_nosimd_*` variants.

- [ ] **Step 3: Self-host the model**

Run (from `frontend/session/`):
```bash
curl -L -o public/mediapipe/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```
Expected: `public/mediapipe/face_landmarker.task` is ~3.7 MB. Verify: `ls -la public/mediapipe/face_landmarker.task`.

- [ ] **Step 4: Allow WASM compilation in CSP (HUMAN REVIEW)**

In `proxy.ts`, find the `script-src` directive of the Content-Security-Policy. Add `'wasm-unsafe-eval'` to it (both dev and prod branches — WASM compilation needs it in all modes; this is narrower than `'unsafe-eval'` and does not re-enable JS eval). Add an inline comment:
```
// 'wasm-unsafe-eval' enables MediaPipe tasks-vision WASM compilation
// (same-origin /mediapipe/wasm). Narrower than 'unsafe-eval'. See
// docs/superpowers/specs/2026-05-29-vision-proctoring-design.md §5.
```
This change requires a threat-model note (`docs/security/threat-model.md`) per the session CLAUDE.md — add a one-line entry that the candidate surface now compiles same-origin WASM for proctoring.

- [ ] **Step 5: Commit**
```bash
git add package.json package-lock.json public/mediapipe proxy.ts docs/security/threat-model.md
git commit -m "build(session): add self-hosted MediaPipe tasks-vision + CSP wasm-unsafe-eval"
```

---

## Task 2: Add the `NEXT_PUBLIC_PROCTORING_DEBUG` env flag

**Files:**
- Modify: `frontend/session/lib/env.ts`
- Test: `frontend/session/tests/lib/env.test.ts`

- [ ] **Step 1: Write the failing test**

Add to `tests/lib/env.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { envSchema } from '@/lib/env'

describe('NEXT_PUBLIC_PROCTORING_DEBUG', () => {
  it("defaults to false when unset", () => {
    const parsed = envSchema.parse({ NEXT_PUBLIC_API_URL: 'https://x.test' })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(false)
  })

  it("is true only for the literal '1'", () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://x.test',
      NEXT_PUBLIC_PROCTORING_DEBUG: '1',
    })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(true)
  })

  it("is false for any other string", () => {
    const parsed = envSchema.parse({
      NEXT_PUBLIC_API_URL: 'https://x.test',
      NEXT_PUBLIC_PROCTORING_DEBUG: 'true',
    })
    expect(parsed.NEXT_PUBLIC_PROCTORING_DEBUG).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/lib/env.test.ts`
Expected: FAIL — `NEXT_PUBLIC_PROCTORING_DEBUG` is undefined on the parsed object.

- [ ] **Step 3: Implement**

In `lib/env.ts`, extend the schema and the parse call:
```typescript
export const envSchema = z.object({
  NEXT_PUBLIC_API_URL: z.string().url(),
  // Dev-only flag. The VisionDebugOverlay renders only when this is true.
  // Treated as a strict opt-in: only the literal "1" enables it.
  NEXT_PUBLIC_PROCTORING_DEBUG: z
    .string()
    .optional()
    .transform((v) => v === '1'),
})

export const env: Env = envSchema.parse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_PROCTORING_DEBUG: process.env.NEXT_PUBLIC_PROCTORING_DEBUG,
})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/lib/env.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add lib/env.ts tests/lib/env.test.ts
git commit -m "feat(session): add NEXT_PUBLIC_PROCTORING_DEBUG env flag"
```

---

## Task 3: Vision types + pure head-pose / gaze helpers

**Files:**
- Create: `frontend/session/components/interview/proctoring/vision/types.ts`
- Create: `frontend/session/components/interview/proctoring/vision/head-pose.ts`
- Create: `frontend/session/components/interview/proctoring/vision/gaze.ts`
- Test: `frontend/session/tests/components/interview/proctoring/vision-head-pose.test.ts`
- Test: `frontend/session/tests/components/interview/proctoring/vision-gaze.test.ts`

- [ ] **Step 1: Create the types module**

`vision/types.ts`:
```typescript
/** Coarse gaze zones (spec §7②). 'center' = on-screen. */
export type GazeZone = 'center' | 'left' | 'right' | 'up' | 'down_away'

/** Per-session trust level for the gaze signal (spec §7 robustness). */
export type SignalQuality = 'good' | 'glasses_degraded' | 'low_light' | 'unscorable'

/** Head orientation in degrees (yaw=left/right, pitch=up/down, roll=tilt). */
export interface HeadPose {
  yaw: number
  pitch: number
  roll: number
}

/** Iris look-direction, each 0..1, from MediaPipe blendshapes. */
export interface IrisOffset {
  in: number
  out: number
  up: number
  down: number
}

/** One detection tick's distilled signals (what the hook exposes). */
export interface VisionSignals {
  faceCount: number
  pose: HeadPose | null
  gazeZone: GazeZone | null
  blinking: boolean
  earValue: number | null
  quality: SignalQuality
  fps: number
}
```

- [ ] **Step 2: Write the failing head-pose test**

`tests/components/interview/proctoring/vision-head-pose.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { matrixToHeadPose } from '@/components/interview/proctoring/vision/head-pose'

// MediaPipe facialTransformationMatrixes[].data is a length-16,
// column-major 4x4 matrix.
const IDENTITY = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

describe('matrixToHeadPose', () => {
  it('returns ~zero angles for the identity matrix', () => {
    const p = matrixToHeadPose(IDENTITY)
    expect(Math.abs(p.yaw)).toBeLessThan(1)
    expect(Math.abs(p.pitch)).toBeLessThan(1)
    expect(Math.abs(p.roll)).toBeLessThan(1)
  })

  it('returns null-safe zero for a malformed matrix', () => {
    const p = matrixToHeadPose([1, 2, 3])
    expect(p).toEqual({ yaw: 0, pitch: 0, roll: 0 })
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/vision-head-pose.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement head-pose**

`vision/head-pose.ts`:
```typescript
import type { HeadPose } from './types'

const RAD2DEG = 180 / Math.PI

/**
 * Extract yaw/pitch/roll (degrees) from a MediaPipe facial transformation
 * matrix (length-16, column-major). Element (row,col) = data[col*4 + row].
 *
 * NOTE: axis signs depend on the model's coordinate convention — verify
 * direction live via the VisionDebugOverlay during tuning (spec §11) and
 * flip a sign here if needed. Defaults assume +yaw = head turned to the
 * candidate's right, +pitch = head tilted down.
 */
export function matrixToHeadPose(data: number[] | Float32Array): HeadPose {
  if (!data || data.length < 16) return { yaw: 0, pitch: 0, roll: 0 }
  const m = (row: number, col: number) => data[col * 4 + row]
  // Rotation submatrix R (3x3). Standard ZYX Euler extraction.
  const r00 = m(0, 0), r10 = m(1, 0), r20 = m(2, 0)
  const r21 = m(2, 1), r22 = m(2, 2)
  const yaw = Math.atan2(-r20, Math.hypot(r21, r22)) * RAD2DEG
  const pitch = Math.atan2(r21, r22) * RAD2DEG
  const roll = Math.atan2(r10, r00) * RAD2DEG
  return { yaw, pitch, roll }
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/vision-head-pose.test.ts`
Expected: PASS.

- [ ] **Step 6: Write the failing gaze test**

`tests/components/interview/proctoring/vision-gaze.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import {
  classifyGazeZone,
  eyeAspectRatio,
  isBlinking,
  signalQuality,
} from '@/components/interview/proctoring/vision/gaze'

const ZERO_IRIS = { in: 0, out: 0, up: 0, down: 0 }

describe('classifyGazeZone (head-pose-primary)', () => {
  it('is center when looking straight at the screen', () => {
    expect(classifyGazeZone({ yaw: 2, pitch: 1, roll: 0 }, ZERO_IRIS)).toBe('center')
  })
  it('is left when yaw is strongly negative', () => {
    expect(classifyGazeZone({ yaw: -30, pitch: 0, roll: 0 }, ZERO_IRIS)).toBe('left')
  })
  it('is right when yaw is strongly positive', () => {
    expect(classifyGazeZone({ yaw: 30, pitch: 0, roll: 0 }, ZERO_IRIS)).toBe('right')
  })
  it('is down_away when pitch is strongly down (phone/notes tell)', () => {
    expect(classifyGazeZone({ yaw: 0, pitch: 28, roll: 0 }, ZERO_IRIS)).toBe('down_away')
  })
  it('escalates a borderline head pose to off-screen when iris agrees', () => {
    // Small head turn + strong iris-out → off-screen left/right.
    expect(classifyGazeZone({ yaw: 12, pitch: 0, roll: 0 }, { ...ZERO_IRIS, out: 0.8 })).toBe('right')
  })
})

describe('eyeAspectRatio / isBlinking', () => {
  it('eyeAspectRatio averages the two eye blink blendshapes', () => {
    expect(eyeAspectRatio(0.9, 0.7)).toBeCloseTo(0.8)
  })
  it('isBlinking is true above the blink threshold', () => {
    expect(isBlinking(0.6)).toBe(true)
    expect(isBlinking(0.1)).toBe(false)
  })
})

describe('signalQuality', () => {
  it('is unscorable when no face is detected', () => {
    expect(signalQuality({ faceConfidence: 0, brightness: 0.5, eyeGlare: 0 })).toBe('unscorable')
  })
  it('is low_light when the frame is too dark', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.05, eyeGlare: 0 })).toBe('low_light')
  })
  it('is glasses_degraded under strong eye-region glare', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.5, eyeGlare: 0.9 })).toBe('glasses_degraded')
  })
  it('is good in clean conditions', () => {
    expect(signalQuality({ faceConfidence: 0.9, brightness: 0.5, eyeGlare: 0.1 })).toBe('good')
  })
})
```

- [ ] **Step 7: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/vision-gaze.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 8: Implement gaze**

`vision/gaze.ts`:
```typescript
import type { GazeZone, HeadPose, IrisOffset, SignalQuality } from './types'

// --- Starting thresholds (tune via debug overlay, spec §11) ---
const YAW_OFF = 22 // deg: head clearly turned left/right
const PITCH_DOWN = 22 // deg: head clearly tilted down (phone/notes)
const PITCH_UP = -20 // deg: head clearly tilted up
const YAW_BORDERLINE = 10 // deg: small turn; let iris break the tie
const IRIS_STRONG = 0.6 // blendshape score: clear eye deviation
const BLINK_CUTOFF = 0.4 // averaged eyeBlink blendshape
const DARK_CUTOFF = 0.12 // normalized eye-region brightness
const GLARE_CUTOFF = 0.7 // normalized specular brightness in eye region

/**
 * Head-pose-PRIMARY gaze zone with iris as a tie-breaker (spec §7②, D5).
 * Iris is only consulted when the head pose is borderline, so glasses-
 * corrupted iris cannot, on its own, manufacture an off-screen verdict.
 */
export function classifyGazeZone(pose: HeadPose, iris: IrisOffset): GazeZone {
  if (pose.pitch >= PITCH_DOWN) return 'down_away'
  if (pose.pitch <= PITCH_UP) return 'up'
  if (pose.yaw <= -YAW_OFF) return 'left'
  if (pose.yaw >= YAW_OFF) return 'right'
  // Borderline head turn: let a strong iris deviation decide direction.
  if (Math.abs(pose.yaw) >= YAW_BORDERLINE) {
    if (iris.out >= IRIS_STRONG || iris.in >= IRIS_STRONG) {
      return pose.yaw > 0 ? 'right' : 'left'
    }
  }
  return 'center'
}

export function eyeAspectRatio(blinkLeft: number, blinkRight: number): number {
  return (blinkLeft + blinkRight) / 2
}

export function isBlinking(ear: number): boolean {
  return ear >= BLINK_CUTOFF
}

export function signalQuality(args: {
  faceConfidence: number
  brightness: number
  eyeGlare: number
}): SignalQuality {
  if (args.faceConfidence <= 0) return 'unscorable'
  if (args.brightness < DARK_CUTOFF) return 'low_light'
  if (args.eyeGlare >= GLARE_CUTOFF) return 'glasses_degraded'
  return 'good'
}
```

- [ ] **Step 9: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/vision-gaze.test.ts`
Expected: PASS.

- [ ] **Step 10: Commit**
```bash
git add components/interview/proctoring/vision/types.ts components/interview/proctoring/vision/head-pose.ts components/interview/proctoring/vision/gaze.ts tests/components/interview/proctoring/vision-head-pose.test.ts tests/components/interview/proctoring/vision-gaze.test.ts
git commit -m "feat(session): pure head-pose + gaze-zone proctoring helpers"
```

---

## Task 4: Pure reading-pattern accumulator

**Files:**
- Create: `frontend/session/components/interview/proctoring/vision/reading.ts`
- Test: `frontend/session/tests/components/interview/proctoring/vision-reading.test.ts`

The reading detector (spec §7②) flags *sustained* off-screen attention with a left↔right scanning rhythm, and requires head-pose corroboration (never iris-only). Plan A computes the signal; Plan B's authoritative version refines it.

- [ ] **Step 1: Write the failing test**

`tests/components/interview/proctoring/vision-reading.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { ReadingAccumulator } from '@/components/interview/proctoring/vision/reading'

describe('ReadingAccumulator', () => {
  it('does not flag when gaze stays on-screen', () => {
    const acc = new ReadingAccumulator()
    for (let t = 0; t < 6000; t += 200) acc.push('center', t)
    expect(acc.isReading()).toBe(false)
    expect(acc.offScreenRatio()).toBe(0)
  })

  it('flags reading when off-screen with left-right scanning over the window', () => {
    const acc = new ReadingAccumulator()
    // Alternate down_away / left / right for >3s — scanning while off-screen.
    const zones = ['down_away', 'left', 'down_away', 'right'] as const
    for (let i = 0, t = 0; t < 4000; i++, t += 200) acc.push(zones[i % zones.length], t)
    expect(acc.isReading()).toBe(true)
    expect(acc.offScreenRatio()).toBeGreaterThan(0.8)
  })

  it('does not flag a single brief glance away', () => {
    const acc = new ReadingAccumulator()
    acc.push('center', 0)
    acc.push('down_away', 200)
    acc.push('center', 400)
    expect(acc.isReading()).toBe(false)
  })

  it('prunes samples older than the window', () => {
    const acc = new ReadingAccumulator()
    acc.push('down_away', 0)
    acc.push('center', 10000) // 10s later — old sample pruned
    expect(acc.offScreenRatio()).toBe(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/vision-reading.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`vision/reading.ts`:
```typescript
import type { GazeZone } from './types'

const WINDOW_MS = 5000 // rolling analysis window
const MIN_OFF_RATIO = 0.6 // fraction of window spent off-screen
const MIN_DIRECTION_CHANGES = 3 // left<->right alternations = scanning

const OFF_SCREEN: ReadonlySet<GazeZone> = new Set(['left', 'right', 'down_away', 'up'])

interface Sample {
  zone: GazeZone
  t: number
}

/**
 * Rolling-window reading detector (spec §7②). Flags sustained off-screen
 * attention WITH horizontal scanning rhythm. Head-pose-derived zones only
 * (the caller passes pose-based zones), so this never fires on iris alone.
 */
export class ReadingAccumulator {
  private samples: Sample[] = []

  push(zone: GazeZone, t: number): void {
    this.samples.push({ zone, t })
    const cutoff = t - WINDOW_MS
    while (this.samples.length && this.samples[0].t < cutoff) this.samples.shift()
  }

  offScreenRatio(): number {
    if (this.samples.length === 0) return 0
    const off = this.samples.filter((s) => OFF_SCREEN.has(s.zone)).length
    return off / this.samples.length
  }

  private directionChanges(): number {
    let changes = 0
    let last: 'left' | 'right' | null = null
    for (const s of this.samples) {
      const dir = s.zone === 'left' ? 'left' : s.zone === 'right' ? 'right' : null
      if (dir && last && dir !== last) changes++
      if (dir) last = dir
    }
    return changes
  }

  isReading(): boolean {
    if (this.samples.length < 2) return false
    const span = this.samples[this.samples.length - 1].t - this.samples[0].t
    if (span < WINDOW_MS * 0.6) return false // need a sustained window
    return this.offScreenRatio() >= MIN_OFF_RATIO && this.directionChanges() >= MIN_DIRECTION_CHANGES
  }

  reset(): void {
    this.samples = []
  }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/vision-reading.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add components/interview/proctoring/vision/reading.ts tests/components/interview/proctoring/vision-reading.test.ts
git commit -m "feat(session): pure reading-pattern accumulator"
```

---

## Task 5: FaceLandmarker lazy loader

**Files:**
- Create: `frontend/session/components/interview/proctoring/vision/face-landmarker.ts`

No test here (thin wrapper over the SDK; exercised via the mocked hook test in Task 6). Keep it minimal.

- [ ] **Step 1: Implement**

`vision/face-landmarker.ts`:
```typescript
import type { FaceLandmarker } from '@mediapipe/tasks-vision'

/**
 * Lazily create a MediaPipe FaceLandmarker configured for live proctoring.
 * WASM + model are SAME-ORIGIN (public/mediapipe/*) — no CDN, per the
 * candidate-surface no-third-party rule. Dynamic import keeps the ~heavy
 * SDK out of the pre-/start bundle (LiveKit-bearing route only).
 */
export async function createFaceLandmarker(): Promise<FaceLandmarker> {
  const { FaceLandmarker, FilesetResolver } = await import('@mediapipe/tasks-vision')
  const fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm')
  return FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/face_landmarker.task',
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    numFaces: 2, // multi-face: detect a second person (spec §7①)
    outputFaceBlendshapes: true, // iris look-direction + blink
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

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: PASS (no type errors; `@mediapipe/tasks-vision` types resolve).

- [ ] **Step 3: Commit**
```bash
git add components/interview/proctoring/vision/face-landmarker.ts
git commit -m "feat(session): MediaPipe FaceLandmarker lazy loader (self-hosted)"
```

---

## Task 6: `useVisionGuard` hook (detection loop → signals + advisory nudges)

**Files:**
- Create: `frontend/session/components/interview/proctoring/use-vision-guard.ts`
- Create: `frontend/session/components/interview/proctoring/nudge-kinds.ts`
- Test: `frontend/session/tests/components/interview/proctoring/use-vision-guard.test.tsx`

- [ ] **Step 1: Create the nudge-kinds module**

`nudge-kinds.ts`:
```typescript
/** Advisory, NON-terminating vision nudges (spec §5.2, D1). Display-only
 * in Plan A — distinct from backend ProctoringKind. */
export type VisionNudgeKind = 'face_not_visible' | 'multiple_faces' | 'looking_away_sustained'

export const NUDGE_LABEL: Record<VisionNudgeKind, string> = {
  face_not_visible: 'please stay in view of the camera',
  multiple_faces: 'only the candidate should be on camera',
  looking_away_sustained: 'please keep your eyes on the screen',
}

/** Sustained-condition duration before a nudge fires (ms). Tune via overlay. */
export const NUDGE_SUSTAIN_MS: Record<VisionNudgeKind, number> = {
  face_not_visible: 2500,
  multiple_faces: 2000,
  looking_away_sustained: 4000,
}
```

- [ ] **Step 2: Write the failing hook test**

`tests/components/interview/proctoring/use-vision-guard.test.tsx`:
```typescript
import { renderHook, act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// --- Mock the MediaPipe loader so no real WASM/model is needed ---
const detectForVideo = vi.fn()
vi.mock('@/components/interview/proctoring/vision/face-landmarker', async () => {
  const actual = await vi.importActual<typeof import('@/components/interview/proctoring/vision/face-landmarker')>(
    '@/components/interview/proctoring/vision/face-landmarker',
  )
  return {
    ...actual,
    createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo, close: vi.fn() }),
  }
})

// --- Mock the LiveKit local camera track ---
const fakeTrack = { attach: vi.fn((el: HTMLVideoElement) => el), detach: vi.fn() }
vi.mock('@livekit/components-react', () => ({
  useLocalParticipant: () => ({
    localParticipant: {
      getTrackPublication: () => ({ track: fakeTrack, isMuted: false }),
    },
  }),
}))

import { useVisionGuard } from '@/components/interview/proctoring/use-vision-guard'

// One face, looking straight ahead (identity matrix), eyes open.
function oneFaceCenter() {
  return {
    faceLandmarks: [[]],
    faceBlendshapes: [{ categories: [] }],
    facialTransformationMatrixes: [{ data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] }],
  }
}

beforeEach(() => {
  // rAF → immediate so the loop ticks under fake timers.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    return setTimeout(() => cb(performance.now()), 16) as unknown as number
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  detectForVideo.mockReset()
})

describe('useVisionGuard', () => {
  it('does nothing when not armed', () => {
    const onNudge = vi.fn()
    renderHook(() => useVisionGuard({ armed: false, onNudge }))
    expect(detectForVideo).not.toHaveBeenCalled()
  })

  it('produces center-gaze signals for a single forward-facing face', async () => {
    detectForVideo.mockReturnValue(oneFaceCenter())
    const onNudge = vi.fn()
    const { result } = renderHook(() => useVisionGuard({ armed: true, onNudge }))
    await waitFor(() => expect(result.current.signals.faceCount).toBe(1))
    expect(result.current.signals.gazeZone).toBe('center')
    expect(onNudge).not.toHaveBeenCalled()
  })

  it('fires multiple_faces when a second face persists past the sustain window', async () => {
    detectForVideo.mockReturnValue({
      faceLandmarks: [[], []],
      faceBlendshapes: [{ categories: [] }, { categories: [] }],
      facialTransformationMatrixes: [
        { data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] },
        { data: [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] },
      ],
    })
    const onNudge = vi.fn()
    renderHook(() => useVisionGuard({ armed: true, onNudge }))
    await waitFor(() => expect(onNudge).toHaveBeenCalledWith('multiple_faces'), { timeout: 4000 })
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/use-vision-guard.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the hook**

`use-vision-guard.ts`:
```typescript
'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'

import { createFaceLandmarker, blendshape } from './vision/face-landmarker'
import { matrixToHeadPose } from './vision/head-pose'
import { classifyGazeZone, eyeAspectRatio, isBlinking, signalQuality } from './vision/gaze'
import { ReadingAccumulator } from './vision/reading'
import type { VisionSignals } from './vision/types'
import { NUDGE_SUSTAIN_MS, type VisionNudgeKind } from './nudge-kinds'

const EMPTY: VisionSignals = {
  faceCount: 0, pose: null, gazeZone: null, blinking: false,
  earValue: null, quality: 'unscorable', fps: 0,
}

export interface UseVisionGuardArgs {
  armed: boolean
  onNudge: (kind: VisionNudgeKind) => void
}

export interface VisionGuardState {
  signals: VisionSignals
}

export function useVisionGuard({ armed, onNudge }: UseVisionGuardArgs): VisionGuardState {
  const { localParticipant } = useLocalParticipant()
  const [signals, setSignals] = useState<VisionSignals>(EMPTY)
  const reading = useRef(new ReadingAccumulator())
  // Tracks when each sustained condition first became true (for debounce).
  const since = useRef<Partial<Record<VisionNudgeKind, number>>>({})

  const maybeNudge = useCallback(
    (kind: VisionNudgeKind, active: boolean, now: number) => {
      if (!active) { delete since.current[kind]; return }
      const start = since.current[kind] ?? now
      since.current[kind] = start
      if (now - start >= NUDGE_SUSTAIN_MS[kind]) {
        onNudge(kind)
        // re-arm: require the condition to clear and re-trigger
        since.current[kind] = now + 1e9
      }
    },
    [onNudge],
  )

  useEffect(() => {
    if (!armed) return
    let cancelled = false
    let raf = 0
    let landmarker: { detectForVideo: (v: HTMLVideoElement, t: number) => unknown; close: () => void } | null = null
    let last = performance.now()

    const video = document.createElement('video')
    video.muted = true
    video.playsInline = true

    const pub = localParticipant.getTrackPublication(Track.Source.Camera)
    const track = pub?.track
    if (track) track.attach(video)

    const tick = () => {
      if (cancelled || !landmarker) return
      const now = performance.now()
      const fps = 1000 / Math.max(1, now - last)
      last = now
      if (video.readyState >= 2) {
        const res = landmarker.detectForVideo(video, now) as {
          faceBlendshapes?: Array<{ categories: Array<{ categoryName: string; score: number }> }>
          facialTransformationMatrixes?: Array<{ data: number[] }>
        }
        const faceCount = res.facialTransformationMatrixes?.length ?? 0
        const cats = res.faceBlendshapes?.[0]?.categories
        const mtx = res.facialTransformationMatrixes?.[0]?.data
        const pose = faceCount > 0 && mtx ? matrixToHeadPose(mtx) : null
        const iris = {
          in: Math.max(blendshape(cats, 'eyeLookInLeft'), blendshape(cats, 'eyeLookInRight')),
          out: Math.max(blendshape(cats, 'eyeLookOutLeft'), blendshape(cats, 'eyeLookOutRight')),
          up: Math.max(blendshape(cats, 'eyeLookUpLeft'), blendshape(cats, 'eyeLookUpRight')),
          down: Math.max(blendshape(cats, 'eyeLookDownLeft'), blendshape(cats, 'eyeLookDownRight')),
        }
        const ear = cats ? eyeAspectRatio(blendshape(cats, 'eyeBlinkLeft'), blendshape(cats, 'eyeBlinkRight')) : null
        const quality = signalQuality({
          faceConfidence: faceCount > 0 ? 1 : 0,
          brightness: 0.5, // Plan A: brightness/glare proxies refined in Plan B
          eyeGlare: 0,
        })
        const zone = pose ? classifyGazeZone(pose, iris) : null
        if (zone) reading.current.push(zone, now)

        setSignals({
          faceCount, pose, gazeZone: zone, blinking: ear !== null && isBlinking(ear),
          earValue: ear, quality, fps,
        })

        maybeNudge('face_not_visible', faceCount === 0, now)
        maybeNudge('multiple_faces', faceCount >= 2, now)
        maybeNudge('looking_away_sustained', reading.current.isReading(), now)
      }
      raf = requestAnimationFrame(tick)
    }

    createFaceLandmarker().then((lm) => {
      if (cancelled) { lm.close?.(); return }
      landmarker = lm as typeof landmarker
      void video.play().catch(() => {})
      raf = requestAnimationFrame(tick)
    })

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      if (track) track.detach(video)
      landmarker?.close()
      setSignals(EMPTY)
      reading.current.reset()
      since.current = {}
    }
  }, [armed, localParticipant, maybeNudge])

  return { signals }
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/use-vision-guard.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**
```bash
git add components/interview/proctoring/use-vision-guard.ts components/interview/proctoring/nudge-kinds.ts tests/components/interview/proctoring/use-vision-guard.test.tsx
git commit -m "feat(session): useVisionGuard detection loop + advisory nudges"
```

---

## Task 7: `VisionDebugOverlay` (dev-gated)

**Files:**
- Create: `frontend/session/components/interview/proctoring/VisionDebugOverlay.tsx`
- Test: `frontend/session/tests/components/interview/proctoring/vision-debug-overlay.test.tsx`

- [ ] **Step 1: Write the failing test**

`tests/components/interview/proctoring/vision-debug-overlay.test.tsx`:
```typescript
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VisionDebugOverlay } from '@/components/interview/proctoring/VisionDebugOverlay'
import type { VisionSignals } from '@/components/interview/proctoring/vision/types'

const SIGNALS: VisionSignals = {
  faceCount: 2, pose: { yaw: 12.3, pitch: -4.1, roll: 1.0 },
  gazeZone: 'right', blinking: false, earValue: 0.12, quality: 'glasses_degraded', fps: 24.5,
}

describe('VisionDebugOverlay', () => {
  it('renders the key tracking signals', () => {
    render(<VisionDebugOverlay signals={SIGNALS} />)
    expect(screen.getByText(/faces:\s*2/i)).toBeInTheDocument()
    expect(screen.getByText(/zone:\s*right/i)).toBeInTheDocument()
    expect(screen.getByText(/glasses_degraded/i)).toBeInTheDocument()
    expect(screen.getByText(/yaw/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/vision-debug-overlay.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

`VisionDebugOverlay.tsx`:
```typescript
'use client'

import type { VisionSignals } from './vision/types'

/**
 * DEV-ONLY tracking readout (spec §5.3). Mounted only when
 * env.NEXT_PUBLIC_PROCTORING_DEBUG is true — gating happens in
 * ProctoringGuard, NOT here, so this stays a pure render of signals.
 * MUST NEVER ship enabled (pre-prod action item, spec §10).
 */
export function VisionDebugOverlay({ signals }: { signals: VisionSignals }) {
  const p = signals.pose
  return (
    <div
      data-testid="vision-debug-overlay"
      className="pointer-events-none fixed bottom-2 right-2 z-[80] rounded-md bg-black/75 px-3 py-2 font-mono text-[11px] leading-tight text-green-300 backdrop-blur-sm"
    >
      <div>faces: {signals.faceCount}</div>
      <div>zone: {signals.gazeZone ?? '—'}</div>
      <div>
        yaw {p ? p.yaw.toFixed(1) : '—'} / pitch {p ? p.pitch.toFixed(1) : '—'} / roll{' '}
        {p ? p.roll.toFixed(1) : '—'}
      </div>
      <div>ear: {signals.earValue?.toFixed(2) ?? '—'} {signals.blinking ? '(blink)' : ''}</div>
      <div>quality: {signals.quality}</div>
      <div>fps: {signals.fps.toFixed(0)}</div>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/vision-debug-overlay.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add components/interview/proctoring/VisionDebugOverlay.tsx tests/components/interview/proctoring/vision-debug-overlay.test.tsx
git commit -m "feat(session): dev-gated VisionDebugOverlay"
```

---

## Task 8: Display-only nudge path on the controller

**Files:**
- Modify: `frontend/session/components/interview/proctoring/use-proctoring-controller.ts`
- Test: `frontend/session/tests/components/interview/proctoring/vision-nudge-controller.test.tsx`

Advisory nudges reuse the existing yellow `flash` + a `toast.warning`, but **never POST and never terminate** (D1). Add a `nudge()` method alongside `report()`.

- [ ] **Step 1: Write the failing test**

`tests/components/interview/proctoring/vision-nudge-controller.test.tsx`:
```typescript
import { renderHook, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const endMock = vi.fn()
vi.mock('@livekit/components-react', () => ({ useSessionContext: () => ({ end: endMock }) }))
const warning = vi.fn()
vi.mock('sonner', () => ({ toast: { warning, error: vi.fn() } }))
import { candidateSessionApi } from '@/lib/api/candidate-session'
import { useProctoringController } from '@/components/interview/proctoring/use-proctoring-controller'

afterEach(() => vi.restoreAllMocks())

const CFG = { enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }

describe('controller.nudge (advisory)', () => {
  it('flashes soft + toasts but never POSTs or terminates', () => {
    const post = vi.spyOn(candidateSessionApi, 'proctoringEvent')
    const onTerminated = vi.fn()
    const { result } = renderHook(() =>
      useProctoringController({ token: 't', config: CFG, onTerminated }),
    )
    act(() => result.current.nudge('multiple_faces'))
    expect(result.current.flash?.tone).toBe('soft')
    expect(warning).toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
    expect(onTerminated).not.toHaveBeenCalled()
    expect(endMock).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- tests/components/interview/proctoring/vision-nudge-controller.test.tsx`
Expected: FAIL — `result.current.nudge` is not a function.

- [ ] **Step 3: Implement**

In `use-proctoring-controller.ts`: import the nudge types and add a `nudge` method. Add near the top of the file:
```typescript
import { NUDGE_LABEL, type VisionNudgeKind } from './nudge-kinds'
```
Extend the `ProctoringController` interface:
```typescript
export interface ProctoringController {
  report: (kind: ProctoringKind) => Promise<void>
  nudge: (kind: VisionNudgeKind) => void
  flash: BorderFlash | null
}
```
Add inside the hook, before `return { ... }`:
```typescript
  const nudge = useCallback(
    (kind: VisionNudgeKind) => {
      if (terminatedRef.current) return
      // Advisory only (spec D1): soft flash + gentle toast, NO POST, NO terminate.
      flashKey.current += 1
      setFlash({ tone: 'soft', key: flashKey.current })
      toast.warning(`Reminder: ${NUDGE_LABEL[kind]}.`)
    },
    [],
  )
```
Update the return:
```typescript
  return { report, nudge, flash }
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- tests/components/interview/proctoring/vision-nudge-controller.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the full proctoring suite (no regressions)**

Run: `npm run test -- tests/components/interview/proctoring`
Expected: PASS (existing controller/guard tests + new ones).

- [ ] **Step 6: Commit**
```bash
git add components/interview/proctoring/use-proctoring-controller.ts tests/components/interview/proctoring/vision-nudge-controller.test.tsx
git commit -m "feat(session): display-only advisory nudge path on proctoring controller"
```

---

## Task 9: Wire vision into `ProctoringGuard`

**Files:**
- Modify: `frontend/session/components/interview/proctoring/ProctoringGuard.tsx`
- Test: `frontend/session/tests/components/interview/proctoring/proctoring-guard.composition.test.tsx` (extend)

- [ ] **Step 1: Implement the wiring**

In `ProctoringGuard.tsx`:

Add imports:
```typescript
import { useVisionGuard } from './use-vision-guard'
import { VisionDebugOverlay } from './VisionDebugOverlay'
import { env } from '@/lib/env'
```
After the existing guard hooks (`fs = useFullscreenGuard(...)`), add:
```typescript
  const vision = useVisionGuard({ armed: enforce, onNudge: controller.nudge })
```
In the returned JSX, after the `FocusGraceOverlay` line and before the closing `</>`, add:
```typescript
      {cfg.enabled && env.NEXT_PUBLIC_PROCTORING_DEBUG && (
        <VisionDebugOverlay signals={vision.signals} />
      )}
```

- [ ] **Step 2: Extend the composition test**

The existing `proctoring-guard.composition.test.tsx` mocks `@livekit/components-react` with only `useVoiceAssistant` + `useSessionContext`. Extend that mock to include `useLocalParticipant` (the new hook needs it) and mock the MediaPipe loader so the guard mounts cleanly. Add to the top mock block:
```typescript
vi.mock('@livekit/components-react', () => ({
  useVoiceAssistant: () => ({ state: 'listening' }),
  useSessionContext: () => ({ end: endMock }),
  useLocalParticipant: () => ({
    localParticipant: { getTrackPublication: () => undefined },
  }),
}))
vi.mock('@/components/interview/proctoring/vision/face-landmarker', () => ({
  createFaceLandmarker: vi.fn().mockResolvedValue({ detectForVideo: vi.fn(), close: vi.fn() }),
  blendshape: () => 0,
}))
```
Add a test asserting the debug overlay is absent by default (flag off):
```typescript
it('does not render the vision debug overlay when the debug flag is off', () => {
  render(
    <ProctoringGuard token="t" config={{ enabled: true, soft_violation_limit: 3, fullscreen_grace_seconds: 10 }} onTerminated={vi.fn()}>
      <div>child</div>
    </ProctoringGuard>,
  )
  expect(screen.queryByTestId('vision-debug-overlay')).toBeNull()
})
```

- [ ] **Step 3: Run the suite**

Run: `npm run test -- tests/components/interview/proctoring/proctoring-guard.composition.test.tsx`
Expected: PASS.

- [ ] **Step 4: Type-check + full test run**

Run: `npm run type-check && npm run test`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**
```bash
git add components/interview/proctoring/ProctoringGuard.tsx tests/components/interview/proctoring/proctoring-guard.composition.test.tsx
git commit -m "feat(session): mount vision guard + dev overlay in ProctoringGuard"
```

---

## Task 10: Extend the welcome disclosure (HUMAN REVIEW — consent flow)

**Files:**
- Modify: `frontend/session/components/interview/app/welcome-view.tsx:42-54`

This touches the consent-disclosure surface (session CLAUDE.md "Human Review Required For: consent step flow"). Keep it to disclosure text only — no flow/logic change. Full BIPA consent-string versioning + block-on-decline is a later backend-coupled task.

- [ ] **Step 1: Add the camera-monitoring disclosure line**

In the proctoring disclosure card bullet list (around lines 42–54), add one bullet consistent with the existing copy:
```tsx
<li>Your camera is monitored automatically (face and eye-position checks) during the interview.</li>
```

- [ ] **Step 2: Update the welcome-step test**

In `tests/components/interview/welcome-step.test.tsx`, add an assertion that the new disclosure line renders when proctoring is enabled:
```typescript
expect(screen.getByText(/camera is monitored automatically/i)).toBeInTheDocument()
```

- [ ] **Step 3: Run**

Run: `npm run test -- tests/components/interview/welcome-step.test.tsx`
Expected: PASS.

- [ ] **Step 4: Commit**
```bash
git add components/interview/app/welcome-view.tsx tests/components/interview/welcome-step.test.tsx
git commit -m "feat(session): disclose camera-based monitoring on the welcome screen"
```

---

## Final verification

- [ ] Run the full suite + type-check + lint:
```bash
npm run test && npm run type-check && npm run lint
```
Expected: all PASS.

- [ ] **Manual smoke test (the real acceptance gate, spec §11):**
  1. `NEXT_PUBLIC_PROCTORING_DEBUG=1 npm run dev` (port 3002).
  2. Start a real interview session through the wizard.
  3. Confirm the green debug overlay appears bottom-right once the agent is live.
  4. Verify live: `faces` goes to 2 when a second person enters frame; `zone` flips to `down_away` when you look at a phone, `left`/`right` when you turn; `quality` flips to `glasses_degraded` with glasses + glare and `low_light` in a dark room; `fps` is reasonable (≥15).
  5. Confirm advisory toasts fire on sustained multi-face / look-away, and that the session is **never terminated** by them.
  6. Confirm the overlay is **absent** when the flag is unset.
  7. Use the live readout to tune the threshold constants in `vision/gaze.ts`, `vision/reading.ts`, and `nudge-kinds.ts`.

---

## Self-Review notes (author)

- **Spec coverage:** §5.2 useVisionGuard (T6), §5.3 debug overlay (T7), §7① multi-face (T6), §7② gaze zones + reading + heatmap-inputs (T3/T4 — heatmap *rendering* is Plan C), §7 robustness incl. glasses head-pose-primary (T3 gaze), advisory non-terminating nudges/D1 (T8). Deferred-by-design: backend reporting/heartbeats, server analysis, report UI, behavioral hardening, full consent gate (all noted in Scope).
- **No backend contract change:** `lib/api/candidate-session.ts` is untouched; nudges are display-only. This is the deliberate Plan-A/Plan-B seam.
- **Type consistency:** `VisionSignals`, `GazeZone`, `SignalQuality`, `VisionNudgeKind` defined once (T3/T6) and reused by hook (T6), overlay (T7), controller (T8).
```
