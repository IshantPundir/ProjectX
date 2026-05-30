# Review Theater — Glassmorphic Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the report Review Theater as an immersive, near-full dark-glassmorphic playback theater — full-bleed video with dark frosted panels floating over it, fully custom video controls (no native `<video controls>`), and a legible 3-layer session timeline.

**Architecture:** The theater shell becomes a single positioned root (`theater-root`) where the `<video>` is the full-bleed background (`absolute inset-0 object-cover`) and every UI surface (top bar, This-Moment, control bar, timeline dock) is an absolutely-positioned dark-glass overlay. A `useVideoController` hook owns transport state (play/seek/volume/rate/buffered) consumed by a presentational `VideoControls` bar. Legibility on dark glass is achieved by overriding foreground CSS tokens (`--px-fg`, `--px-fg-3`, `--px-fg-4`, `--px-hairline`) scoped to `.theater-root`, so existing `var(--px-fg)` references flip light-on-dark with no per-component rewrites. Tone tokens (`--px-ok/caution/danger`) stay vivid.

**Tech Stack:** Next.js 16 (App Router), React 19, TypeScript strict, Tailwind v4 + scoped CSS in `theater.css`, lucide-react icons, Vitest + @testing-library/react.

**Scope:** 100% frontend, all under `frontend/app/components/dashboard/reports/theater/`. No backend/API change — the API already serves `questions[].asked_at_ms`, `questions[].thumbnail_url`, and `flagged_intervals[].thumbnail_url`.

**Conventions**
- All paths below are relative to `frontend/app/`.
- Run tests: `npm run test` (vitest run). Type-check: `npm run type-check`. Lint: `npm run lint`. Build: `npm run build`.
- Visual changes are verified manually against the running dev server (`npm run dev`, port 3000) per repo norm — pure logic is unit-tested.
- The test report URL for manual verification:
  `http://localhost:3000/reports/session/ee1e6683-f878-405c-a53c-48c973f786f4?candidateId=93527cde-a44b-4ad5-80aa-a265ed1cd381&candidateName=&title=Jr.%20Forward%20Deployed%20Engineer%20(4%2Byrs)&subtitle=New%20Stage`
  Note: this is a **legacy session** — its questions have null `asked_at_ms` and no question thumbnails (degrade-gracefully path). Its proctoring is rich: 64 flagged intervals (42 down_glance + 22 off_screen_sustained), `risk=high`, 6 flag thumbnails.

---

## File Structure

**Create**
- `components/dashboard/reports/theater/useVideoController.ts` — transport-state hook + `clockFromSec` helper.
- `components/dashboard/reports/theater/VideoControls.tsx` — presentational glass control bar (icons from `lucide-react`, already installed at `^1.14.0` and used throughout the app).
- `tests/components/theater/video-controls.test.tsx` — control-bar + `clockFromSec` behavior.
- `tests/components/theater/integrity-lane.test.tsx` — integrity-lane rendering.
- `tests/components/theater/filmstrip.test.tsx` — placeholder + non-seekable rendering.

**Existing tests that must keep passing (do NOT rewrite):**
- `tests/components/theater/timeline-model.test.ts` — APPEND new model tests here (Task 1); existing cases stay green (helpers are additive).
- `tests/components/theater/ReviewTheater.test.tsx` — asserts preserved aria-labels/text (`"Interview session recording"`, the `Experience` filmstrip card, `Not Recommended`, flag pre-select → `Looked off-screen` / `65% confidence`). The redesign preserves all of these; the test must still pass unchanged (verified in Task 8).
- `tests/components/theater/TheaterTopBar.test.tsx` — checks verdict chip, candidate name, `high integrity risk`, `Overall` gauge img, close button. The Task 8 topbar edit only drops a margin class — test stays green.

**Modify**
- `components/dashboard/reports/theater/timeline-model.ts` — add `clamp01`, `gamma`, `densityBucketsForKinds`.
- `components/dashboard/reports/theater/theater.css` — full rewrite (dark glass + scoped token overrides + sizing + scrims + controls + scrubber + integrity + flag tips + auto-hide).
- `components/dashboard/reports/theater/TheaterStage.tsx` — presentational full-bleed video layer (remove native controls).
- `components/dashboard/reports/theater/IntegrityLane.tsx` — two sub-lanes + gamma density + all clickable ticks + hover thumbnails + bold caption.
- `components/dashboard/reports/theater/Filmstrip.tsx` — dark-glass cards + tone-gradient placeholder + non-seekable affordance.
- `components/dashboard/reports/theater/SessionTimeline.tsx` — prop change (`downBuckets`/`offBuckets` replace `buckets`/`riskBand`).
- `components/dashboard/reports/theater/ReviewTheater.tsx` — layered overlay layout, controller, auto-hide, keyboard, fullscreen.
- `components/dashboard/reports/theater/TheaterTopBar.tsx` — drop self-margin (now positioned by a slot).
- `components/dashboard/reports/theater/ThisMomentPanel.tsx` — `h-full` → `max-h-full` for the centered slot.
- `components/dashboard/reports/theater/NodeTrack.tsx` — flip the hardcoded light track background for dark glass.

---

## Task 1: Timeline model — gamma curve + per-kind density buckets

**Files:**
- Modify: `components/dashboard/reports/theater/timeline-model.ts`
- Test: `tests/components/theater/timeline-model.test.ts` (APPEND to the existing file)

- [ ] **Step 1: Extend the existing test file**

First, extend the existing import block at the top of `tests/components/theater/timeline-model.test.ts`. Change:

```typescript
import {
  activeQuestionId,
  activeSegmentIndex,
  buildFlagMarkers,
  buildQuestionMarkers,
  densityBuckets,
} from '@/components/dashboard/reports/theater/timeline-model'
```

to (add the three new helpers — keep one import statement to satisfy `import/no-duplicates`):

```typescript
import {
  activeQuestionId,
  activeSegmentIndex,
  buildFlagMarkers,
  buildQuestionMarkers,
  clamp01,
  densityBuckets,
  densityBucketsForKinds,
  gamma,
} from '@/components/dashboard/reports/theater/timeline-model'
```

Then APPEND these `describe` blocks to the end of the same file (`ProctoringFlaggedInterval` is already imported there):

```typescript
describe('clamp01', () => {
  it('clamps below 0 and above 1', () => {
    expect(clamp01(-0.5)).toBe(0)
    expect(clamp01(1.5)).toBe(1)
    expect(clamp01(0.3)).toBe(0.3)
  })
})

describe('gamma', () => {
  it('keeps 0 and 1 fixed and brightens mid values', () => {
    expect(gamma(0)).toBe(0)
    expect(gamma(1)).toBe(1)
    // gamma < 1 raises small inputs (0.25 ** 0.45 ≈ 0.53)
    expect(gamma(0.25)).toBeGreaterThan(0.25)
  })
})

describe('densityBucketsForKinds', () => {
  const flagged: ProctoringFlaggedInterval[] = [
    { kind: 'down_glance', start_ms: 0, end_ms: 1000, confidence: 0.6 },
    { kind: 'off_screen_sustained', start_ms: 5000, end_ms: 6000, confidence: 0.65 },
  ]

  it('includes only the requested kinds', () => {
    const out = densityBucketsForKinds(flagged, 10_000, 10, ['down_glance'])
    expect(out).toHaveLength(10)
    // bucket 0 (0–1000ms) is the only down_glance hit → normalized to 1
    expect(out[0]).toBe(1)
    // the off_screen bucket (≈5) is excluded → 0
    expect(out[5]).toBe(0)
  })

  it('returns all-zero buckets when no kind matches', () => {
    const out = densityBucketsForKinds(flagged, 10_000, 4, ['multiple_faces'])
    expect(out).toEqual([0, 0, 0, 0])
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- tests/components/theater/timeline-model.test.ts`
Expected: FAIL — `clamp01`/`gamma`/`densityBucketsForKinds` are not exported.

- [ ] **Step 3: Add the helpers to `timeline-model.ts`**

Append to `components/dashboard/reports/theater/timeline-model.ts` (after `densityBuckets`, before `activeQuestionId`):

```typescript
export function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}

/** Perceptual brightening of a normalized density so a single hit stays visible
 * on dark glass. g < 1 lifts small values; 0 and 1 are fixed points. */
export function gamma(v: number, g = 0.45): number {
  return Math.pow(clamp01(v), g)
}

/** densityBuckets restricted to a set of flag kinds (one proctoring sub-lane). */
export function densityBucketsForKinds(
  flagged: ProctoringFlaggedInterval[],
  durationMs: number,
  buckets: number,
  kinds: string[],
): number[] {
  const set = new Set(kinds)
  return densityBuckets(
    flagged.filter((f) => set.has(f.kind)),
    durationMs,
    buckets,
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- tests/components/theater/timeline-model.test.ts`
Expected: PASS (existing cases + 3 new describe blocks).

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/timeline-model.ts tests/components/theater/timeline-model.test.ts
git commit -m "feat(theater): gamma curve + per-kind density buckets for integrity sub-lanes"
```

---

## Task 2: `useVideoController` hook + `clockFromSec`

**Files:**
- Create: `components/dashboard/reports/theater/useVideoController.ts`
- Test: `tests/components/theater/video-controls.test.tsx` (create here; the `clockFromSec` block goes in first, the component block is appended in Task 3).

> The hook's DOM-event wiring is exercised manually (jsdom does not implement media playback). Only the pure `clockFromSec` helper is unit-tested; `VideoControls` (Task 3) covers the interactive surface with a mocked controller.

- [ ] **Step 1: Write the failing test**

```typescript
// tests/components/theater/video-controls.test.tsx  (clock helper portion first)
import { describe, it, expect } from 'vitest'

import { clockFromSec } from '@/components/dashboard/reports/theater/useVideoController'

describe('clockFromSec', () => {
  it('formats seconds as m:ss and floors fractions', () => {
    expect(clockFromSec(0)).toBe('0:00')
    expect(clockFromSec(9.9)).toBe('0:09')
    expect(clockFromSec(75)).toBe('1:15')
  })
  it('guards NaN/negative to 0:00', () => {
    expect(clockFromSec(NaN)).toBe('0:00')
    expect(clockFromSec(-5)).toBe('0:00')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- tests/components/theater/video-controls.test.tsx`
Expected: FAIL — cannot import `clockFromSec` (module does not exist).

- [ ] **Step 3: Create the hook**

```typescript
// components/dashboard/reports/theater/useVideoController.ts
'use client'

import {
  useCallback,
  useEffect,
  useState,
  type MutableRefObject,
  type RefObject,
} from 'react'

import type { PlaybackSeekApi } from '../SessionPlayback'

const RATES = [1, 1.5, 2]

export function clockFromSec(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0
  const total = Math.floor(sec)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export interface VideoController {
  playing: boolean
  currentSec: number
  durationSec: number
  bufferedSec: number
  volume: number
  muted: boolean
  rate: number
  togglePlay: () => void
  seekToSec: (sec: number) => void
  setVolume: (v: number) => void
  toggleMute: () => void
  cycleRate: () => void
}

/** Owns transport state for the theater's custom controls. Attaches listeners to
 * the <video> once it exists (gated by `enabled`), exposes the ms-based seek API
 * the rest of the theater uses (questions/flags) via `seekApiRef`, and reports
 * the engine-relative playhead through `onCurrentMs`. */
export function useVideoController(
  videoRef: RefObject<HTMLVideoElement | null>,
  enabled: boolean,
  offsetMs: number,
  seekApiRef: MutableRefObject<PlaybackSeekApi | null>,
  onCurrentMs: (ms: number) => void,
): VideoController {
  const [playing, setPlaying] = useState(false)
  const [currentSec, setCurrentSec] = useState(0)
  const [durationSec, setDurationSec] = useState(0)
  const [bufferedSec, setBufferedSec] = useState(0)
  const [volume, setVolumeState] = useState(1)
  const [muted, setMuted] = useState(false)
  const [rate, setRate] = useState(1)

  useEffect(() => {
    const v = videoRef.current
    if (!v || !enabled) return
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onTime = () => {
      setCurrentSec(v.currentTime)
      onCurrentMs(v.currentTime * 1000 - offsetMs)
    }
    const onDur = () => setDurationSec(Number.isFinite(v.duration) ? v.duration : 0)
    const onProgress = () => {
      try {
        if (v.buffered.length) setBufferedSec(v.buffered.end(v.buffered.length - 1))
      } catch {
        /* buffered can throw before metadata; ignore */
      }
    }
    const onVol = () => {
      setVolumeState(v.volume)
      setMuted(v.muted)
    }
    const onRate = () => setRate(v.playbackRate)
    v.addEventListener('play', onPlay)
    v.addEventListener('pause', onPause)
    v.addEventListener('timeupdate', onTime)
    v.addEventListener('durationchange', onDur)
    v.addEventListener('progress', onProgress)
    v.addEventListener('volumechange', onVol)
    v.addEventListener('ratechange', onRate)
    // sync initial state
    onDur()
    onVol()
    return () => {
      v.removeEventListener('play', onPlay)
      v.removeEventListener('pause', onPause)
      v.removeEventListener('timeupdate', onTime)
      v.removeEventListener('durationchange', onDur)
      v.removeEventListener('progress', onProgress)
      v.removeEventListener('volumechange', onVol)
      v.removeEventListener('ratechange', onRate)
    }
  }, [videoRef, enabled, offsetMs, onCurrentMs])

  // ms-based seek used by question/flag jumps (kept identical to old TheaterStage)
  useEffect(() => {
    seekApiRef.current = {
      seekToMs: (ms: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = Math.max(0, (ms + offsetMs) / 1000)
        void v.play?.()
      },
    }
    return () => {
      seekApiRef.current = null
    }
  }, [videoRef, seekApiRef, offsetMs])

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play?.()
    else v.pause?.()
  }, [videoRef])

  const seekToSec = useCallback(
    (sec: number) => {
      const v = videoRef.current
      if (!v) return
      v.currentTime = Math.max(0, sec)
    },
    [videoRef],
  )

  const setVolume = useCallback(
    (val: number) => {
      const v = videoRef.current
      if (!v) return
      v.muted = false
      v.volume = Math.min(1, Math.max(0, val))
    },
    [videoRef],
  )

  const toggleMute = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    v.muted = !v.muted
  }, [videoRef])

  const cycleRate = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    const idx = RATES.indexOf(v.playbackRate)
    v.playbackRate = RATES[(idx + 1) % RATES.length] ?? 1
  }, [videoRef])

  return {
    playing,
    currentSec,
    durationSec,
    bufferedSec,
    volume,
    muted,
    rate,
    togglePlay,
    seekToSec,
    setVolume,
    toggleMute,
    cycleRate,
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- tests/components/theater/video-controls.test.tsx`
Expected: PASS (the `clockFromSec` describe block; the component block is added in Task 3).

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/useVideoController.ts tests/components/theater/video-controls.test.tsx
git commit -m "feat(theater): useVideoController transport hook + clockFromSec"
```

---

## Task 3: `VideoControls` presentational glass control bar

**Files:**
- Create: `components/dashboard/reports/theater/VideoControls.tsx`
- Test: `tests/components/theater/video-controls.test.tsx` (extend)

- [ ] **Step 1: Add the failing component test**

Append to `tests/components/theater/video-controls.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'

import { VideoControls } from '@/components/dashboard/reports/theater/VideoControls'
import type { VideoController } from '@/components/dashboard/reports/theater/useVideoController'

function makeController(over: Partial<VideoController> = {}): VideoController {
  return {
    playing: false,
    currentSec: 75,
    durationSec: 251,
    bufferedSec: 100,
    volume: 1,
    muted: false,
    rate: 1,
    togglePlay: vi.fn(),
    seekToSec: vi.fn(),
    setVolume: vi.fn(),
    toggleMute: vi.fn(),
    cycleRate: vi.fn(),
    ...over,
  }
}

describe('VideoControls', () => {
  it('renders current and total time', () => {
    render(<VideoControls controller={makeController()} visible onToggleFullscreen={vi.fn()} />)
    expect(screen.getByText('1:15')).toBeTruthy()
    expect(screen.getByText('4:11')).toBeTruthy()
  })

  it('calls togglePlay when the play button is clicked', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.click(screen.getByLabelText('Play'))
    expect(c.togglePlay).toHaveBeenCalledOnce()
  })

  it('calls seekToSec when the scrubber changes', () => {
    const c = makeController()
    render(<VideoControls controller={c} visible onToggleFullscreen={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Seek'), { target: { value: '120' } })
    expect(c.seekToSec).toHaveBeenCalledWith(120)
  })

  it('calls onToggleFullscreen', () => {
    const fs = vi.fn()
    render(<VideoControls controller={makeController()} visible onToggleFullscreen={fs} />)
    fireEvent.click(screen.getByLabelText('Fullscreen'))
    expect(fs).toHaveBeenCalledOnce()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- tests/components/theater/video-controls.test.tsx`
Expected: FAIL — cannot import `VideoControls`.

- [ ] **Step 3: Create the component**

```tsx
// components/dashboard/reports/theater/VideoControls.tsx
'use client'

import { Maximize, Pause, Play, Volume2, VolumeX } from 'lucide-react'

import { clockFromSec, type VideoController } from './useVideoController'
import './theater.css'

export function VideoControls({
  controller,
  visible,
  onToggleFullscreen,
}: {
  controller: VideoController
  visible: boolean
  onToggleFullscreen: () => void
}) {
  const c = controller
  const pct = c.durationSec > 0 ? (c.currentSec / c.durationSec) * 100 : 0
  const buf = c.durationSec > 0 ? (c.bufferedSec / c.durationSec) * 100 : 0
  const silent = c.muted || c.volume === 0
  return (
    <div
      className="theater-controls theater-glass flex items-center gap-3 rounded-2xl px-4 py-2"
      data-visible={visible ? 'true' : 'false'}
    >
      <button
        type="button"
        onClick={c.togglePlay}
        aria-label={c.playing ? 'Pause' : 'Play'}
        className="theater-playbtn grid h-9 w-9 flex-none place-items-center rounded-full"
      >
        {c.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </button>

      <span className="flex-none text-[11px] tabular-nums" style={{ color: 'var(--px-fg-3)' }}>
        {clockFromSec(c.currentSec)}
      </span>

      <div className="theater-scrub relative flex-1">
        <div className="theater-scrub-track">
          <div className="theater-scrub-buf" style={{ width: `${buf}%` }} />
          <div className="theater-scrub-fill" style={{ width: `${pct}%` }} />
        </div>
        <input
          type="range"
          min={0}
          max={Math.max(0, c.durationSec)}
          step={0.1}
          value={c.currentSec}
          aria-label="Seek"
          onChange={(e) => c.seekToSec(Number(e.target.value))}
          className="theater-scrub-input"
        />
      </div>

      <span className="flex-none text-[11px] tabular-nums" style={{ color: 'var(--px-fg-3)' }}>
        {clockFromSec(c.durationSec)}
      </span>

      <button
        type="button"
        onClick={c.cycleRate}
        aria-label="Playback speed"
        className="theater-ctrlbtn flex-none text-[11px] font-bold tabular-nums"
      >
        {c.rate}×
      </button>

      <button
        type="button"
        onClick={c.toggleMute}
        aria-label={silent ? 'Unmute' : 'Mute'}
        className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
      >
        {silent ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
      </button>

      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={silent ? 0 : c.volume}
        aria-label="Volume"
        onChange={(e) => c.setVolume(Number(e.target.value))}
        className="theater-vol flex-none"
      />

      <button
        type="button"
        onClick={onToggleFullscreen}
        aria-label="Fullscreen"
        className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
      >
        <Maximize className="h-4 w-4" />
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- tests/components/theater/video-controls.test.tsx`
Expected: PASS (all blocks).

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/VideoControls.tsx tests/components/theater/video-controls.test.tsx
git commit -m "feat(theater): custom VideoControls glass bar (scrubber/volume/speed/fullscreen)"
```

---

## Task 4: `theater.css` full rewrite (dark glass system)

**Files:**
- Modify: `components/dashboard/reports/theater/theater.css`

> No unit test — verified visually in Task 9. This task establishes the dark-glass tokens, near-full sizing, scrims, control/scrubber styling, integrity styling, flag tooltips, and auto-hide. Replace the **entire** file contents.

- [ ] **Step 1: Replace `theater.css` with the new stylesheet**

```css
/* components/dashboard/reports/theater/theater.css
   Dark, immersive frosted-glass styling for the Review Theater.
   Foreground tokens are overridden scoped to .theater-root so every
   var(--px-fg*) reference flips light-on-dark automatically. Tone tokens
   (--px-ok/caution/danger) are intentionally left vivid. */

/* Dimmed, blurred page behind the near-full overlay. */
.px-dialog-backdrop:has(.theater-shell) {
  background: rgba(8, 14, 20, 0.62);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.theater-shell {
  width: 96vw;
  height: 94vh;
  max-width: 96vw;
  max-height: 94vh;
  padding: 0;
  overflow: hidden;
  border-radius: 22px;
  background: #000;
  box-shadow: 0 30px 90px rgba(0, 0, 0, 0.6);
}

/* The single positioned root that holds the video + all overlays. */
.theater-root {
  position: relative;
  height: 100%;
  width: 100%;
  overflow: hidden;
  border-radius: inherit;
  background: #000;

  /* light-on-dark foreground tokens, scoped to the theater only */
  --px-fg: #eef4f8;
  --px-fg-3: rgba(224, 235, 242, 0.74);
  --px-fg-4: rgba(224, 235, 242, 0.5);
  --px-hairline: rgba(255, 255, 255, 0.16);
}

/* Dark frosted panel (was light in the old theater). */
.theater-glass {
  background: rgba(16, 23, 30, 0.55);
  backdrop-filter: blur(18px) saturate(120%);
  -webkit-backdrop-filter: blur(18px) saturate(120%);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.45);
}

/* --- overlay slots --- */
.theater-topbar-slot {
  position: absolute;
  top: 14px;
  left: 14px;
  right: 14px;
  z-index: 30;
}
.theater-moment-slot {
  position: absolute;
  top: 50%;
  right: 16px;
  transform: translateY(-50%);
  width: 300px;
  max-height: 56%;
  z-index: 30;
}
.theater-bottom {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 14px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* --- video scrims (legibility over any frame) --- */
.theater-scrim-top {
  position: absolute;
  inset: 0 0 auto 0;
  height: 150px;
  z-index: 10;
  pointer-events: none;
  background: linear-gradient(to bottom, rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0));
}
.theater-scrim-bottom {
  position: absolute;
  inset: auto 0 0 0;
  height: 320px;
  z-index: 10;
  pointer-events: none;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.7), rgba(0, 0, 0, 0));
}

/* --- center play overlay --- */
.theater-centerplay {
  color: #fff;
  background: rgba(20, 28, 36, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.22);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  transition: transform 0.15s ease, background 0.15s ease;
}
.theater-centerplay:hover {
  transform: translate(-50%, -50%) scale(1.06);
  background: rgba(30, 40, 50, 0.62);
}

/* --- control bar --- */
.theater-controls {
  transition: opacity 0.25s ease;
}
.theater-controls[data-visible='false'] {
  opacity: 0;
  pointer-events: none;
}
.theater-playbtn {
  color: #0c1116;
  background: var(--px-accent);
  transition: transform 0.12s ease;
}
.theater-playbtn:hover {
  transform: scale(1.06);
}
.theater-ctrlbtn {
  color: var(--px-fg-3);
  border-radius: 8px;
  transition: color 0.12s ease, background 0.12s ease;
}
.theater-ctrlbtn:hover {
  color: var(--px-fg);
  background: rgba(255, 255, 255, 0.08);
}

/* scrubber: a styled track with buffered + played fills, a transparent
   range input on top capturing pointer/keyboard. */
.theater-scrub {
  height: 18px;
  display: flex;
  align-items: center;
}
.theater-scrub-track {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 4px;
  transform: translateY(-50%);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.18);
  overflow: hidden;
}
.theater-scrub-buf {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.28);
}
.theater-scrub-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: var(--px-accent);
}
.theater-scrub-input {
  position: absolute;
  inset: 0;
  width: 100%;
  margin: 0;
  background: transparent;
  -webkit-appearance: none;
  appearance: none;
  cursor: pointer;
}
.theater-scrub-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  height: 12px;
  width: 12px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.35);
}
.theater-scrub-input::-moz-range-thumb {
  height: 12px;
  width: 12px;
  border: none;
  border-radius: 50%;
  background: #fff;
}
.theater-vol {
  width: 72px;
  height: 4px;
  -webkit-appearance: none;
  appearance: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.22);
  cursor: pointer;
}
.theater-vol::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  height: 11px;
  width: 11px;
  border-radius: 50%;
  background: #fff;
}
.theater-vol::-moz-range-thumb {
  height: 11px;
  width: 11px;
  border: none;
  border-radius: 50%;
  background: #fff;
}

/* --- filmstrip cards --- */
.theater-card {
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.theater-card:hover {
  transform: translateY(-2px);
}
.theater-card[data-active='true'] {
  outline: 2px solid var(--px-accent);
  outline-offset: 1px;
}
.theater-card[data-seekable='false'] {
  opacity: 0.62;
  cursor: default;
}
.theater-card[data-seekable='false']:hover {
  transform: none;
}
.theater-strip {
  scrollbar-width: thin;
}

/* --- node track --- */
.theater-node {
  transition: width 0.12s ease, height 0.12s ease;
}
.theater-node[data-active='true'] {
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.22);
}

/* --- integrity flag ticks + hover thumbnail --- */
.theater-flagtick {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 3px;
  transform: translateX(-50%);
  cursor: pointer;
  border: none;
  padding: 0;
}
.theater-flagtip {
  position: absolute;
  bottom: 150%;
  left: 50%;
  transform: translateX(-50%);
  display: none;
  width: 132px;
  border-radius: 8px;
  overflow: hidden;
  z-index: 40;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.2);
}
.theater-flagtick:hover .theater-flagtip {
  display: block;
}
```

- [ ] **Step 2: Type-check + lint (CSS has no test; ensure nothing else broke)**

Run: `npm run lint`
Expected: clean (CSS isn't linted by ESLint; this confirms no import churn).

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/reports/theater/theater.css
git commit -m "feat(theater): dark-glass stylesheet (tokens, scrims, controls, scrubber, integrity)"
```

---

## Task 5: `TheaterStage` — presentational full-bleed video layer

**Files:**
- Modify: `components/dashboard/reports/theater/TheaterStage.tsx`

> The controller + seek wiring moves up to `ReviewTheater` (Task 8). `TheaterStage` becomes a dumb layer: full-bleed `<video>`, scrims, center-play overlay. No native `controls`.

- [ ] **Step 1: Replace `TheaterStage.tsx` contents**

```tsx
// components/dashboard/reports/theater/TheaterStage.tsx
'use client'

import { Play } from 'lucide-react'
import type { RefObject } from 'react'

export function TheaterStage({
  videoRef,
  signedUrl,
  playing,
  onTogglePlay,
}: {
  videoRef: RefObject<HTMLVideoElement | null>
  signedUrl: string | null
  playing: boolean
  onTogglePlay: () => void
}) {
  if (!signedUrl) {
    return (
      <div
        className="absolute inset-0 grid place-items-center text-[12px]"
        style={{ color: 'rgba(224,235,242,0.7)' }}
      >
        Recording unavailable.
      </div>
    )
  }
  return (
    <>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- interview recording, no caption track */}
      <video
        ref={videoRef}
        src={signedUrl}
        playsInline
        aria-label="Interview session recording"
        onClick={onTogglePlay}
        className="absolute inset-0 h-full w-full bg-black object-cover"
      />
      <div className="theater-scrim-top" aria-hidden="true" />
      <div className="theater-scrim-bottom" aria-hidden="true" />
      {!playing && (
        <button
          type="button"
          onClick={onTogglePlay}
          aria-label="Play"
          className="theater-centerplay absolute left-1/2 top-1/2 z-20 grid h-16 w-16 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full"
        >
          <Play className="h-7 w-7" />
        </button>
      )}
    </>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: errors ONLY in `ReviewTheater.tsx` (still passing the old `TheaterStage` props — fixed in Task 8). `TheaterStage.tsx` itself compiles. If you see errors in any other file, stop and re-read.

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/reports/theater/TheaterStage.tsx
git commit -m "feat(theater): TheaterStage becomes full-bleed video layer (no native controls)"
```

---

## Task 6: `IntegrityLane` rework + `SessionTimeline` prop change

**Files:**
- Modify: `components/dashboard/reports/theater/IntegrityLane.tsx`
- Modify: `components/dashboard/reports/theater/SessionTimeline.tsx`
- Test: `tests/components/theater/integrity-lane.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/theater/integrity-lane.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { IntegrityLane } from '@/components/dashboard/reports/theater/IntegrityLane'
import type { FlagMarker } from '@/components/dashboard/reports/theater/timeline-model'

function flag(over: Partial<FlagMarker>): FlagMarker {
  return {
    kind: 'down_glance',
    startMs: 1000,
    endMs: 2000,
    confidence: 0.6,
    thumbnailUrl: null,
    positionPct: 10,
    ...over,
  }
}

describe('IntegrityLane', () => {
  it('renders a clickable tick per flag and fires onSelectFlag', () => {
    const onSelect = vi.fn()
    const flags = [
      flag({ kind: 'down_glance', startMs: 1000, positionPct: 10 }),
      flag({ kind: 'off_screen_sustained', startMs: 5000, positionPct: 50 }),
      flag({ kind: 'down_glance', startMs: 8000, positionPct: 80 }),
    ]
    render(
      <IntegrityLane
        downBuckets={[0.2, 0.8]}
        offBuckets={[0, 0.5]}
        flags={flags}
        caption="⚠ HIGH RISK · 36% off-screen · 42 down-glances"
        onSelectFlag={onSelect}
      />,
    )
    const ticks = screen.getAllByRole('button')
    expect(ticks).toHaveLength(3)
    fireEvent.click(ticks[0])
    expect(onSelect).toHaveBeenCalledOnce()
  })

  it('renders the caption', () => {
    render(
      <IntegrityLane
        downBuckets={[0.2]}
        offBuckets={[0.1]}
        flags={[]}
        caption="⚠ HIGH RISK · 36% off-screen · 42 down-glances"
        onSelectFlag={vi.fn()}
      />,
    )
    expect(screen.getByText(/HIGH RISK/)).toBeTruthy()
  })

  it('renders nothing when there is no data', () => {
    const { container } = render(
      <IntegrityLane downBuckets={[]} offBuckets={[]} flags={[]} caption="" onSelectFlag={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- tests/components/theater/integrity-lane.test.tsx`
Expected: FAIL — current `IntegrityLane` expects `buckets`/`riskBand` props (type/render mismatch).

- [ ] **Step 3: Replace `IntegrityLane.tsx` contents**

```tsx
// components/dashboard/reports/theater/IntegrityLane.tsx
'use client'

import { formatTimestamp } from '../report-format'
import { gamma, type FlagMarker } from './timeline-model'
import './theater.css'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

const DOWN_KIND = 'down_glance'

function bucketAlpha(v: number): number {
  return 0.12 + gamma(v) * 0.85
}

function Lane({
  label,
  color,
  buckets,
  flags,
  onSelectFlag,
}: {
  label: string
  color: string
  buckets: number[]
  flags: FlagMarker[]
  onSelectFlag: (f: FlagMarker) => void
}) {
  return (
    <div>
      <div
        className="mb-0.5 text-[8.5px] font-bold uppercase tracking-wide"
        style={{ color: 'var(--px-fg-4)' }}
      >
        {label}
      </div>
      <div
        className="relative flex h-[14px] overflow-visible rounded"
        style={{ background: 'rgba(255,255,255,0.06)' }}
      >
        {buckets.map((v, i) => (
          <div key={i} style={{ flex: 1, height: '100%', background: color, opacity: bucketAlpha(v) }} />
        ))}
        {flags.map((f, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onSelectFlag(f)}
            aria-label={`${KIND_LABEL[f.kind] ?? f.kind} at ${formatTimestamp(f.startMs)}`}
            className="theater-flagtick"
            style={{ left: `${f.positionPct}%`, background: color }}
          >
            {f.thumbnailUrl && (
              <span className="theater-flagtip">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={f.thumbnailUrl} alt="" className="block w-full" />
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

export function IntegrityLane({
  downBuckets,
  offBuckets,
  flags,
  caption,
  onSelectFlag,
}: {
  downBuckets: number[]
  offBuckets: number[]
  flags: FlagMarker[]
  caption: string
  onSelectFlag: (flag: FlagMarker) => void
}) {
  if (downBuckets.length === 0 && offBuckets.length === 0 && flags.length === 0) return null
  const downFlags = flags.filter((f) => f.kind === DOWN_KIND)
  const offFlags = flags.filter((f) => f.kind !== DOWN_KIND)
  return (
    <div className="mt-2 space-y-1.5">
      <Lane
        label="Down-glances"
        color="var(--px-caution)"
        buckets={downBuckets}
        flags={downFlags}
        onSelectFlag={onSelectFlag}
      />
      <Lane
        label="Off-screen"
        color="var(--px-danger)"
        buckets={offBuckets}
        flags={offFlags}
        onSelectFlag={onSelectFlag}
      />
      {caption && (
        <div className="pt-0.5 text-[10px] font-bold" style={{ color: 'var(--px-danger)' }}>
          {caption}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Update `SessionTimeline.tsx` to the new props**

Replace `components/dashboard/reports/theater/SessionTimeline.tsx` contents:

```tsx
// components/dashboard/reports/theater/SessionTimeline.tsx
'use client'

import { Filmstrip } from './Filmstrip'
import { IntegrityLane } from './IntegrityLane'
import { NodeTrack } from './NodeTrack'
import type { FlagMarker, TimelineMarker } from './timeline-model'

export function SessionTimeline({
  markers,
  flags,
  downBuckets,
  offBuckets,
  integrityCaption,
  playheadPct,
  activeQuestionId,
  onSelectQuestion,
  onSeekMs,
  onSelectFlag,
}: {
  markers: TimelineMarker[]
  flags: FlagMarker[]
  downBuckets: number[]
  offBuckets: number[]
  integrityCaption: string
  playheadPct: number
  activeQuestionId: string | null
  onSelectQuestion: (questionId: string) => void
  onSeekMs: (ms: number) => void
  onSelectFlag: (flag: FlagMarker) => void
}) {
  return (
    <div className="theater-glass rounded-2xl p-3">
      <Filmstrip markers={markers} activeQuestionId={activeQuestionId} onSelect={onSelectQuestion} />
      <NodeTrack
        markers={markers}
        playheadPct={playheadPct}
        activeQuestionId={activeQuestionId}
        onSeekMs={onSeekMs}
      />
      <IntegrityLane
        downBuckets={downBuckets}
        offBuckets={offBuckets}
        flags={flags}
        caption={integrityCaption}
        onSelectFlag={onSelectFlag}
      />
    </div>
  )
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm run test -- tests/components/theater/integrity-lane.test.tsx`
Expected: PASS (3 tests).

> Type errors in `ReviewTheater.tsx` (still passing old `buckets`/`riskBand` to SessionTimeline) are expected here and fixed in Task 8.

- [ ] **Step 6: Commit**

```bash
git add components/dashboard/reports/theater/IntegrityLane.tsx components/dashboard/reports/theater/SessionTimeline.tsx tests/components/theater/integrity-lane.test.tsx
git commit -m "feat(theater): two-sub-lane integrity timeline with gamma density + hover thumbnails"
```

---

## Task 7: `Filmstrip` — dark glass + placeholder + non-seekable affordance

**Files:**
- Modify: `components/dashboard/reports/theater/Filmstrip.tsx`
- Test: `tests/components/theater/filmstrip.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/theater/filmstrip.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { Filmstrip } from '@/components/dashboard/reports/theater/Filmstrip'
import type { TimelineMarker } from '@/components/dashboard/reports/theater/timeline-model'

function marker(over: Partial<TimelineMarker>): TimelineMarker {
  return {
    seq: 1,
    questionId: 'q1',
    title: 'How many years of full-time experience?',
    statusBadge: 'passed',
    tone: 'ok',
    askedAtMs: 12_000,
    thumbnailUrl: null,
    positionPct: 20,
    ...over,
  }
}

describe('Filmstrip', () => {
  it('shows a thumbnail image when thumbnailUrl is set', () => {
    render(
      <Filmstrip
        markers={[marker({ thumbnailUrl: 'https://signed/q1.webp' })]}
        activeQuestionId={null}
        onSelect={vi.fn()}
      />,
    )
    const img = screen.getByRole('img')
    expect(img.getAttribute('src')).toBe('https://signed/q1.webp')
  })

  it('shows a tone placeholder (Q number, no img) when thumbnailUrl is null', () => {
    render(<Filmstrip markers={[marker({ thumbnailUrl: null })]} activeQuestionId={null} onSelect={vi.fn()} />)
    expect(screen.queryByRole('img')).toBeNull()
    // the placeholder renders the question number
    expect(screen.getAllByText('Q1').length).toBeGreaterThan(0)
  })

  it('marks a card non-seekable when askedAtMs is null', () => {
    render(
      <Filmstrip
        markers={[marker({ askedAtMs: null, positionPct: null })]}
        activeQuestionId={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByRole('button').getAttribute('data-seekable')).toBe('false')
  })

  it('still calls onSelect when a non-seekable card is clicked', () => {
    const onSelect = vi.fn()
    render(
      <Filmstrip
        markers={[marker({ askedAtMs: null, positionPct: null })]}
        activeQuestionId={null}
        onSelect={onSelect}
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('q1')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- tests/components/theater/filmstrip.test.tsx`
Expected: FAIL — current Filmstrip renders a tone emoji (not "Q1") in the placeholder and has no `data-seekable` attribute.

- [ ] **Step 3: Replace `Filmstrip.tsx` contents**

```tsx
// components/dashboard/reports/theater/Filmstrip.tsx
'use client'

import { formatTimestamp, statusBadgeMeta, TONE_BG, TONE_INK } from '../report-format'
import type { TimelineMarker } from './timeline-model'
import './theater.css'

export function Filmstrip({
  markers,
  activeQuestionId,
  onSelect,
}: {
  markers: TimelineMarker[]
  activeQuestionId: string | null
  onSelect: (questionId: string) => void
}) {
  return (
    <div className="theater-strip flex gap-2 overflow-x-auto pb-1" aria-label="Question timeline">
      {markers.map((m) => {
        const badge = statusBadgeMeta(m.statusBadge)
        const active = m.questionId === activeQuestionId
        const seekable = m.askedAtMs != null
        return (
          <button
            key={m.questionId}
            type="button"
            data-active={active ? 'true' : 'false'}
            data-seekable={seekable ? 'true' : 'false'}
            onClick={() => onSelect(m.questionId)}
            aria-label={`Q${m.seq} ${m.title} — ${badge.label}${seekable ? '' : ' (no timestamp)'}`}
            className="theater-card theater-glass flex w-[168px] flex-none flex-col overflow-hidden rounded-xl text-left"
          >
            <div className="relative h-[44px] w-full" style={{ background: TONE_BG[m.tone] }}>
              {m.thumbnailUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={m.thumbnailUrl} alt={`Q${m.seq} ${m.title}`} className="h-full w-full object-cover" />
              ) : (
                <span
                  className="absolute inset-0 grid place-items-center text-[13px] font-extrabold"
                  aria-hidden="true"
                  style={{ color: TONE_INK[m.tone] }}
                >
                  Q{m.seq}
                </span>
              )}
              {seekable && (
                <span
                  className="absolute bottom-1 right-1 rounded px-1 py-0.5 text-[8.5px] font-bold text-white"
                  style={{ background: 'rgba(8,12,16,0.6)' }}
                >
                  {formatTimestamp(m.askedAtMs)}
                </span>
              )}
            </div>
            <div className="px-2 py-1.5">
              <div className="text-[8px] font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
                Q{m.seq}
              </div>
              <div className="truncate text-[11px] font-semibold" style={{ color: 'var(--px-fg)' }} title={m.title}>
                {m.title}
              </div>
              <div className="mt-0.5 text-[9px] font-bold" style={{ color: TONE_INK[m.tone] }}>
                {badge.label}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- tests/components/theater/filmstrip.test.tsx`
Expected: PASS (4 tests). (`Q1` appears twice — placeholder + the label row — hence `getAllByText`.)

- [ ] **Step 5: Commit**

```bash
git add components/dashboard/reports/theater/Filmstrip.tsx tests/components/theater/filmstrip.test.tsx
git commit -m "feat(theater): filmstrip placeholder + non-seekable affordance for legacy sessions"
```

---

## Task 8: `ReviewTheater` layered layout + controller + auto-hide + keyboard

**Files:**
- Modify: `components/dashboard/reports/theater/ReviewTheater.tsx`
- Modify: `components/dashboard/reports/theater/TheaterTopBar.tsx`
- Modify: `components/dashboard/reports/theater/ThisMomentPanel.tsx`
- Modify: `components/dashboard/reports/theater/NodeTrack.tsx`

> This task wires everything together. After it, `npm run type-check` must be fully clean.

- [ ] **Step 1: Drop the self-margin from `TheaterTopBar`**

In `components/dashboard/reports/theater/TheaterTopBar.tsx`, change the outer wrapper class (line 27) from:

```tsx
    <div className="theater-glass m-3 mb-0 flex items-center gap-4 rounded-2xl px-4 py-2">
```

to (the slot positions it now):

```tsx
    <div className="theater-glass flex items-center gap-4 rounded-2xl px-4 py-2">
```

- [ ] **Step 2: Let `ThisMomentPanel` fit the centered slot**

In `components/dashboard/reports/theater/ThisMomentPanel.tsx`, change the outer wrapper class (line 29) from:

```tsx
    <div className="theater-glass flex h-full flex-col rounded-2xl p-4">
```

to:

```tsx
    <div className="theater-glass flex max-h-full flex-col overflow-y-auto rounded-2xl p-4">
```

- [ ] **Step 3: Flip the `NodeTrack` track background for dark glass**

In `components/dashboard/reports/theater/NodeTrack.tsx`, change line 19 from:

```tsx
    <div className="relative mx-1 mt-2 h-2 rounded" style={{ background: 'rgba(20,40,60,0.1)' }}>
```

to:

```tsx
    <div className="relative mx-1 mt-2 h-2 rounded" style={{ background: 'rgba(255,255,255,0.14)' }}>
```

- [ ] **Step 4: Replace `ReviewTheater.tsx` contents**

```tsx
// components/dashboard/reports/theater/ReviewTheater.tsx
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Dialog, DialogContent } from '@/components/px'
import type { ReportRead } from '@/lib/api/reports'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'
import { SessionTimeline } from './SessionTimeline'
import { TheaterStage } from './TheaterStage'
import { TheaterTopBar } from './TheaterTopBar'
import { ThisMomentPanel } from './ThisMomentPanel'
import { VideoControls } from './VideoControls'
import {
  buildFlagMarkers,
  buildQuestionMarkers,
  densityBucketsForKinds,
} from './timeline-model'
import { useTheaterState } from './useTheaterState'
import { useVideoController } from './useVideoController'
import './theater.css'

const DENSITY_BUCKETS = 48
const DOWN_KINDS = ['down_glance']
const OFF_KINDS = ['off_screen_sustained', 'reading_sweep', 'multiple_faces']
const HIDE_AFTER_MS = 2500

export function ReviewTheater({
  open,
  report,
  candidateName,
  subtitle,
  initialFlagStartMs = null,
  onClose,
}: {
  open: boolean
  report: ReportRead
  candidateName: string
  subtitle: string
  initialFlagStartMs?: number | null
  onClose: () => void
}) {
  const sessionId = report.session_id ?? ''
  const { data: rec } = useSessionRecording(open ? sessionId : '')
  const { data: proc } = useSessionProctoring(open ? sessionId : '')

  const durationMs = (rec?.duration_seconds ?? 0) * 1000
  const signedUrl = rec?.status === 'ready' ? rec.signed_url : null
  const offsetMs = rec?.offset_ms ?? 0
  const flaggedRaw = proc && proc.status === 'ready' ? proc.flagged_intervals : []
  const riskBand = proc && proc.status === 'ready' ? proc.risk_band : null

  const markers = useMemo(
    () => buildQuestionMarkers(report.questions, durationMs),
    [report.questions, durationMs],
  )
  // all flags are clickable ticks; only the top-N carry thumbnails from the API
  const flags = useMemo(
    () => buildFlagMarkers(flaggedRaw, durationMs, flaggedRaw.length),
    [flaggedRaw, durationMs],
  )
  const downBuckets = useMemo(
    () =>
      flaggedRaw.length
        ? densityBucketsForKinds(flaggedRaw, durationMs, DENSITY_BUCKETS, DOWN_KINDS)
        : [],
    [flaggedRaw, durationMs],
  )
  const offBuckets = useMemo(
    () =>
      flaggedRaw.length
        ? densityBucketsForKinds(flaggedRaw, durationMs, DENSITY_BUCKETS, OFF_KINDS)
        : [],
    [flaggedRaw, durationMs],
  )

  const st = useTheaterState({ markers, questions: report.questions, durationMs })

  // custom video transport (replaces native controls)
  const videoRef = useRef<HTMLVideoElement>(null)
  const ctrl = useVideoController(videoRef, !!signedUrl, offsetMs, st.seekRef, st.setCurrentMs)
  const ctrlRef = useRef(ctrl)
  ctrlRef.current = ctrl

  // fullscreen targets the theater root
  const shellRef = useRef<HTMLDivElement>(null)
  const toggleFullscreen = useCallback(() => {
    const el = shellRef.current
    if (!el) return
    if (document.fullscreenElement) void document.exitFullscreen?.()
    else void el.requestFullscreen?.()
  }, [])

  // auto-hide the control bar on pointer idle
  const [controlsVisible, setControlsVisible] = useState(true)
  useEffect(() => {
    if (!open) return
    const root = shellRef.current
    if (!root) return
    let timer = 0
    const show = () => {
      setControlsVisible(true)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setControlsVisible(false), HIDE_AFTER_MS)
    }
    root.addEventListener('pointermove', show)
    root.addEventListener('pointerdown', show)
    show()
    return () => {
      root.removeEventListener('pointermove', show)
      root.removeEventListener('pointerdown', show)
      window.clearTimeout(timer)
    }
  }, [open])

  // keyboard shortcuts (read ctrl through a ref so listeners don't rebind each tick)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const c = ctrlRef.current
      if (e.key === ' ') {
        e.preventDefault()
        c.togglePlay()
      } else if (e.key === 'ArrowRight') {
        c.seekToSec(c.currentSec + 5)
      } else if (e.key === 'ArrowLeft') {
        c.seekToSec(Math.max(0, c.currentSec - 5))
      } else if (e.key === 'f' || e.key === 'F') {
        toggleFullscreen()
      } else if (e.key === 'm' || e.key === 'M') {
        c.toggleMute()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, toggleFullscreen])

  // pre-select a flag when opened from a proctoring "jump to" row
  const { selectFlag } = st
  const appliedFlagRef = useRef(false)
  useEffect(() => {
    if (appliedFlagRef.current || initialFlagStartMs == null) return
    const f = flags.find((x) => x.startMs === initialFlagStartMs)
    if (f) {
      appliedFlagRef.current = true
      selectFlag(f)
    }
  }, [initialFlagStartMs, flags, selectFlag])

  const integrityCaption = useMemo(() => {
    const riskText =
      riskBand === 'high' ? '⚠ HIGH RISK' : riskBand === 'medium' ? '⚠ MEDIUM RISK' : '⚠ INTEGRITY'
    const s = proc && proc.status === 'ready' ? proc.detector_summary : null
    if (!s) return riskText
    return `${riskText} · ${Math.round(s.off_screen_pct * 100)}% off-screen · ${s.down_glance_count} down-glances`
  }, [proc, riskBand])

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent showCloseButton={false} widthClass="" className="theater-shell">
        <div ref={shellRef} className="theater-root">
          <TheaterStage
            videoRef={videoRef}
            signedUrl={signedUrl}
            playing={ctrl.playing}
            onTogglePlay={ctrl.togglePlay}
          />

          <div className="theater-topbar-slot">
            <TheaterTopBar
              report={report}
              candidateName={candidateName}
              subtitle={subtitle}
              riskBand={riskBand}
              onClose={onClose}
            />
          </div>

          <div className="theater-moment-slot">
            <ThisMomentPanel selection={st.selection} decision={report.decision} onJump={st.seekMs} />
          </div>

          <div className="theater-bottom">
            {signedUrl && (
              <VideoControls
                controller={ctrl}
                visible={controlsVisible}
                onToggleFullscreen={toggleFullscreen}
              />
            )}
            <SessionTimeline
              markers={markers}
              flags={flags}
              downBuckets={downBuckets}
              offBuckets={offBuckets}
              integrityCaption={integrityCaption}
              playheadPct={st.playheadPct}
              activeQuestionId={st.activeId}
              onSelectQuestion={st.selectQuestion}
              onSeekMs={st.seekMs}
              onSelectFlag={st.selectFlag}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 5: Type-check the whole app**

Run: `npm run type-check`
Expected: clean (zero errors). If `densityBuckets`/`buildFlagMarkers` import errors appear, confirm Task 1 added `densityBucketsForKinds` and that `buildFlagMarkers` still exists (it does — unchanged).

- [ ] **Step 6: Run the full theater test set**

Run: `npm run test -- tests/components/theater`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/reports/theater/ReviewTheater.tsx components/dashboard/reports/theater/TheaterTopBar.tsx components/dashboard/reports/theater/ThisMomentPanel.tsx components/dashboard/reports/theater/NodeTrack.tsx
git commit -m "feat(theater): layered glass overlay layout + custom controls wiring + keyboard/fullscreen"
```

---

## Task 9: Final verification (build, lint, manual)

**Files:** none (verification only)

- [ ] **Step 1: Full quality gate**

```bash
npm run lint
npm run type-check
npm run test
npm run build
```
Expected: lint clean, type-check clean, all tests pass, build succeeds.

- [ ] **Step 2: Manual visual verification (legacy session — degrade path)**

Start the dev server if not running: `npm run dev` (port 3000). Open the test URL from the Conventions section. Click the SessionPlayback poster to open the theater. Confirm:
- Near-full dark overlay (~96×94vh), page dimmed + blurred behind.
- Video is full-bleed (`object-cover`); top/bottom scrims present.
- **No native video controls.** Custom glass control bar: play/pause toggles, scrubber drags + seeks, time reads `m:ss / m:ss`, volume mute+slider, speed cycles 1×→1.5×→2×, fullscreen works. Control bar fades after ~2.5s idle and returns on mouse move.
- Center play overlay shows while paused; clicking the video toggles play.
- Keyboard: Space play/pause, ←/→ ±5s, `f` fullscreen, `m` mute.
- Top bar, This-Moment, and timeline dock are dark frosted glass floating over the video with legible light text.
- **Integrity lane is now legible**: two sub-lanes (Down-glances amber, Off-screen red) showing real density; many clickable ticks; the 6 flag thumbnails appear on tick hover; bold caption `⚠ HIGH RISK · NN% off-screen · 42 down-glances`.
- Question cards show **tone-gradient + Q-number placeholders** (no broken images) and are visibly **non-seekable** (dimmed); clicking one still opens its detail in This-Moment.
- Clicking a flag tick (or a proctoring jump row that opens the theater) seeks the video and shows the flag in This-Moment with its thumbnail.

- [ ] **Step 3: Manual verification (fresh session — full path), if available**

If a post-engine-tagging session exists (questions with non-null `asked_at_ms` and question thumbnails), open its report and confirm — with **no code change** — that question cards show real thumbnails + timestamps, the node track shows question nodes, and clicking a card seeks the video. If no such session exists yet, note this as deferred to the next live test run.

- [ ] **Step 4: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "chore(theater): verification fixups for glass redesign"
```

---

## Notes

- **Why scoped token overrides instead of rewriting inline styles:** every theater child uses `var(--px-fg*)`/`var(--px-hairline)`. Overriding those four variables on `.theater-root` flips all text light-on-dark in one place (DRY), leaving tone tokens (`--px-ok/caution/danger`) vivid for chips, badges, and lane colors. Only two genuinely hardcoded light-on-light values (`NodeTrack` track bg; old IntegrityLane bg) needed manual flips.
- **`offset_ms` is 0 today** for this session; the seek math (`(ms + offsetMs)/1000`) is preserved so it stays correct once the backend calibrates an offset.
- **No new dependencies** — `lucide-react` is already used by `components/px/Dialog.tsx`.
- **No API/contract change** — `buildFlagMarkers`/`buildQuestionMarkers`/`densityBuckets`/`activeQuestionId` and all `lib/api/reports.ts` types are unchanged; only additive model helpers were introduced.
