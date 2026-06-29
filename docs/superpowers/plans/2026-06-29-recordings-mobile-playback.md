# Mobile-Optimized `/recordings` Video Playback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public `/recordings/<token>` video theaters usable on phones in both portrait and landscape, without changing the desktop layout.

**Architecture:** CSS-media-query-first — most fixes are additive `@media` blocks appended to `theater.css` targeting existing selectors (desktop rules untouched). Plus: a new mobile-only `TheaterMobileSheet` (the side panels' content in a tap-to-open bottom sheet / landscape right-drawer), robust cross-platform fullscreen, and touch-friendly flag detail. All session-app `frontend/session` copy only.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind v4 (incl. `max-[640px]:` variants), plain CSS `@media`, Vitest + Testing Library.

## Global Constraints

- Changes are confined to `frontend/session/components/recordings/**` (+ its tests). Do NOT touch `frontend/app`.
- `frontend/session` MUST NOT import `@supabase/*`; add no new runtime dependencies.
- Desktop (`> 640px`) must render exactly as before — every change is gated behind a mobile/touch media query or a `max-[640px]:`/`landscape`/`hover:none` variant.
- Breakpoints (use these exact queries):
  - Compact: `@media (max-width: 640px)`
  - Landscape-compact: `@media (orientation: landscape) and (max-height: 480px)`
  - Touch: `@media (hover: none) and (pointer: coarse)`
- Tap targets ≥ 44px under the Touch query.
- No video remount on rotation (layout is CSS-driven); sheet/drawer open-state persists across rotation.
- Commit after every task. Run `npm run lint`, `npm run type-check`, `npm run build` from `frontend/session`. Note: `npm run type-check` has 4 KNOWN pre-existing errors in `tests/components/interview/*` — ignore those; your gate is "no NEW errors and none in `components/recordings`".

---

## File Structure

**Modify:**
- `frontend/session/components/recordings/theater/theater.css` — append all responsive `@media` blocks (the bulk).
- `frontend/session/components/recordings/theater/TheaterStage.tsx` — video `object-contain` on mobile.
- `frontend/session/components/recordings/theater/TheaterTopBar.tsx` — class hook to hide the gauges cluster on mobile.
- `frontend/session/components/recordings/theater/VideoControls.tsx` — two-row classes, touch flag tap + clamp, hide fullscreen when unsupported.
- `frontend/session/components/recordings/theater/useVideoController.ts` — expose `fullscreenSupported` + robust toggle helper (or compute in theaters; see Task 3).
- `frontend/session/components/recordings/theater/ReviewTheater.tsx` — robust fullscreen, mount the sheet + trigger.
- `frontend/session/components/recordings/theater/ReelTheater.tsx` — robust fullscreen.
- `frontend/session/components/recordings/PublicRecordingsView.tsx` — toggle touch sizing.

**Create:**
- `frontend/session/components/recordings/theater/TheaterMobileSheet.tsx` — mobile panel sheet/drawer.
- `frontend/session/tests/components/theater-mobile-sheet.test.tsx` — unit test.

---

## Task 1: Responsive layout core (stop the overflow)

Fix the structural breakage: full-bleed shell on mobile, hide the fixed side panels, letterbox the video, collapse the top-bar gauges.

**Files:**
- Modify: `frontend/session/components/recordings/theater/theater.css` (append at end)
- Modify: `frontend/session/components/recordings/theater/TheaterStage.tsx:50`
- Modify: `frontend/session/components/recordings/theater/TheaterTopBar.tsx:114`

**Interfaces:**
- Produces: CSS class `theater-topbar-gauges` (added to the top-bar center cluster) that Task 5's verification and the sheet rely on being hidden on mobile.

- [ ] **Step 1: Letterbox the video on mobile**

In `TheaterStage.tsx` change the `<video>` className (line ~50) from:
```tsx
        className="absolute inset-0 h-full w-full bg-black object-cover"
```
to:
```tsx
        className="absolute inset-0 h-full w-full bg-black object-cover max-[640px]:object-contain"
```

- [ ] **Step 2: Tag the top-bar gauges cluster so CSS can hide it on mobile**

In `TheaterTopBar.tsx`, the center gauges container (line ~114) currently:
```tsx
        <div className="flex items-center gap-2.5 justify-self-center">
```
becomes:
```tsx
        <div className="theater-topbar-gauges flex items-center gap-2.5 justify-self-center">
```

- [ ] **Step 3: Append the responsive layout block to theater.css**

Append to the END of `frontend/session/components/recordings/theater/theater.css`:

```css
/* ============================================================
   MOBILE / TOUCH RESPONSIVE LAYOUT  (added 2026-06-29)
   Desktop rules above are untouched; these only apply on phones.
   ============================================================ */

/* --- Compact: phone portrait + small viewports --- */
@media (max-width: 640px) {
  /* full-bleed theater (dvh accounts for mobile browser chrome) */
  .px-dialog-content.theater-shell {
    width: 100vw;
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border-radius: 0;
  }

  /* the fixed desktop side panels can't coexist with the video on a phone;
     their content moves into TheaterMobileSheet (Task 5) */
  .theater-moment-slot,
  .theater-questions-slot {
    display: none;
  }

  /* the inline score gauges move into the sheet; identity + verdict stay */
  .theater-topbar-gauges {
    display: none;
  }

  /* give the top bar + bottom controls a little less inset on small screens */
  .theater-topbar-slot { top: 8px; left: 8px; right: 8px; }
  .theater-bottom { left: 8px; right: 8px; bottom: 8px; }
}

/* --- Landscape-compact: phone landscape (short height) --- */
@media (orientation: landscape) and (max-height: 480px) {
  .px-dialog-content.theater-shell {
    width: 100vw;
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border-radius: 0;
  }
  .theater-moment-slot,
  .theater-questions-slot,
  .theater-topbar-gauges {
    display: none;
  }
  /* reclaim vertical space: tighter chrome insets */
  .theater-topbar-slot { top: 6px; left: 6px; right: 6px; }
  .theater-bottom { left: 6px; right: 6px; bottom: 6px; }
}
```

- [ ] **Step 4: Verify build + no desktop regression**

Run: `cd frontend/session && npm run build`
Expected: build succeeds. (Desktop unaffected — the new rules are inside `@media` blocks.)

Run: `cd frontend/session && npm run lint`
Expected: clean for the touched files.

- [ ] **Step 5: Commit**

```bash
git add frontend/session/components/recordings/theater/theater.css \
        frontend/session/components/recordings/theater/TheaterStage.tsx \
        frontend/session/components/recordings/theater/TheaterTopBar.tsx
git commit -m "feat(session): responsive theater shell — letterbox + hide desktop side panels on mobile

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Touch-friendly control bar

Two-row control layout on mobile (scrubber its own full-width row), ≥44px tap targets, hide the volume slider.

**Files:**
- Modify: `frontend/session/components/recordings/theater/theater.css` (append)

**Interfaces:**
- Consumes: existing control classes `.theater-controls`, `.theater-scrub`, `.theater-vol`, `.theater-ctrlbtn`, `.theater-playbtn` (from VideoControls.tsx — already present).

- [ ] **Step 1: Append the control-bar responsive block to theater.css**

Append to the END of `theater.css`:

```css
/* --- Touch: bigger targets regardless of orientation --- */
@media (hover: none) and (pointer: coarse) {
  .theater-playbtn { width: 44px; height: 44px; }
  .theater-ctrlbtn { min-width: 44px; min-height: 44px; }
}

/* --- Compact: two-row control bar so the scrubber gets full width --- */
@media (max-width: 640px) {
  .theater-controls {
    flex-wrap: wrap;
    row-gap: 6px;
    column-gap: 8px;
    padding: 10px 12px;
  }
  /* scrubber onto its own first row, full width */
  .theater-scrub {
    order: -1;
    flex: 1 0 100%;
  }
  /* hardware volume on phones — drop the slider, keep the mute button */
  .theater-vol { display: none; }
}

/* --- Landscape-compact: keep controls single-row but compact --- */
@media (orientation: landscape) and (max-height: 480px) {
  .theater-vol { display: none; }
  .theater-controls { padding: 6px 10px; }
}
```

- [ ] **Step 2: Verify**

Run: `cd frontend/session && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/session/components/recordings/theater/theater.css
git commit -m "feat(session): touch-friendly two-row theater controls on mobile

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Robust cross-platform fullscreen (incl. iOS)

`element.requestFullscreen` doesn't exist on iOS Safari; the `<video>` exposes `webkitEnterFullscreen`. Make the toggle fall back to it, and hide the button when neither is supported.

**Files:**
- Modify: `frontend/session/components/recordings/theater/ReviewTheater.tsx:118-123` + the VideoControls call (~261)
- Modify: `frontend/session/components/recordings/theater/ReelTheater.tsx:84-89` + the VideoControls call (~191)
- Modify: `frontend/session/components/recordings/theater/VideoControls.tsx` (props + fullscreen button)

**Interfaces:**
- Produces: `VideoControls` gains an optional prop `fullscreenSupported?: boolean` (default `true`); when `false` the fullscreen button is not rendered.

- [ ] **Step 1: Add the `fullscreenSupported` prop to VideoControls and gate the button**

In `VideoControls.tsx`, add to the props type (after `onSeekMs?`):
```tsx
  fullscreenSupported?: boolean
```
Destructure it with a default in the function signature params:
```tsx
  onSeekMs,
  fullscreenSupported = true,
```
Wrap the fullscreen `<button>` (the one with `aria-label={c.isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}`, ~lines 172-179) so it only renders when supported:
```tsx
      {fullscreenSupported && (
        <button
          type="button"
          onClick={onToggleFullscreen}
          aria-label={c.isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          className="theater-ctrlbtn grid h-7 w-7 flex-none place-items-center"
        >
          <Maximize className="h-4 w-4" />
        </button>
      )}
```

- [ ] **Step 2: Make ReviewTheater's toggle iOS-aware + compute support**

In `ReviewTheater.tsx`, replace the `toggleFullscreen` callback (lines ~118-123) with:
```tsx
  // fullscreen targets the theater root; iOS Safari has no element.requestFullscreen
  // (only the <video> supports webkitEnterFullscreen), so fall back to that.
  const shellRef = useRef<HTMLDivElement>(null)
  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen?.()
      return
    }
    const el = shellRef.current
    if (el?.requestFullscreen) {
      void el.requestFullscreen()
      return
    }
    const v = videoEl as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null
    v?.webkitEnterFullscreen?.()
  }, [videoEl])

  const fullscreenSupported =
    typeof document !== 'undefined' &&
    (document.fullscreenEnabled ||
      typeof (videoEl as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null)
        ?.webkitEnterFullscreen === 'function')
```
(Keep the existing `shellRef` declaration — if it already exists above, do not duplicate it; move the comment only. The `videoEl` state already exists.)

Then pass the flag to `VideoControls` (~line 261 call): add `fullscreenSupported={fullscreenSupported}` to its props.

- [ ] **Step 3: Mirror the same in ReelTheater**

In `ReelTheater.tsx`, replace `toggleFullscreen` (lines ~84-89) with the same body (using its `shellRef` + `videoEl`):
```tsx
  const shellRef = useRef<HTMLDivElement>(null)
  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen?.()
      return
    }
    const el = shellRef.current
    if (el?.requestFullscreen) {
      void el.requestFullscreen()
      return
    }
    const v = videoEl as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null
    v?.webkitEnterFullscreen?.()
  }, [videoEl])

  const fullscreenSupported =
    typeof document !== 'undefined' &&
    (document.fullscreenEnabled ||
      typeof (videoEl as (HTMLVideoElement & { webkitEnterFullscreen?: () => void }) | null)
        ?.webkitEnterFullscreen === 'function')
```
(Do not duplicate the existing `shellRef`.) Pass `fullscreenSupported={fullscreenSupported}` to its `VideoControls` call (~line 191).

- [ ] **Step 4: Verify**

Run: `cd frontend/session && npm run type-check 2>&1 | grep "components/recordings" || echo "OK: no recordings type errors"`
Expected: `OK: no recordings type errors`.

Run: `cd frontend/session && npm run build`
Expected: succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/session/components/recordings/theater/VideoControls.tsx \
        frontend/session/components/recordings/theater/ReviewTheater.tsx \
        frontend/session/components/recordings/theater/ReelTheater.tsx
git commit -m "feat(session): iOS-aware fullscreen + hide control when unsupported

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Touch-accessible proctoring flag detail

Hover doesn't exist on touch. Make a tap on a flag band reveal the detail card, and clamp the card on-screen (it currently positions at `clientX` and can render off the right edge).

**Files:**
- Modify: `frontend/session/components/recordings/theater/VideoControls.tsx`

**Interfaces:**
- No new exported interface. Internal: the existing `hover` state now also set on `pointerdown` hit-test.

- [ ] **Step 1: Add tap-to-reveal + viewport clamp**

In `VideoControls.tsx`, add a pointer-down handler alongside `onScrubMove` and clamp the card x. Replace the `onScrubMove` definition block (lines ~50-65) with:
```tsx
  const scrubRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<{ flag: FlagMarker; x: number; y: number } | null>(null)
  const hitFlagAt = (e: React.PointerEvent<HTMLDivElement>): { flag: FlagMarker; x: number; y: number } | null => {
    const el = scrubRef.current
    if (!el || flags.length === 0) return null
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const f = flags.find((fl) => {
      const left = (fl.positionPct / 100) * rect.width
      const width = Math.max((fl.widthPct / 100) * rect.width, 4)
      return x >= left && x <= left + width
    })
    if (!f) return null
    // clamp the card center so a 158px-wide card stays fully on-screen
    const half = 80
    const cx = Math.min(Math.max(e.clientX, half), window.innerWidth - half)
    return { flag: f, x: cx, y: rect.top }
  }
  const onScrubMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const hit = hitFlagAt(e)
    setHover((prev) => (hit ? hit : prev === null ? prev : null))
  }
  // touch has no hover: a tap on a band toggles the detail card; a tap elsewhere clears it
  const onScrubDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'touch') return
    const hit = hitFlagAt(e)
    setHover((prev) => (hit && prev?.flag !== hit.flag ? hit : null))
  }
  const clearHover = () => setHover((prev) => (prev === null ? prev : null))
```
Then add `onPointerDown={onScrubDown}` to the scrub `<div>` (the one with `ref={scrubRef}`, ~line 86), keeping `onPointerMove={onScrubMove}` and `onPointerLeave={clearHover}`.

- [ ] **Step 2: Verify**

Run: `cd frontend/session && npm run type-check 2>&1 | grep "components/recordings" || echo "OK"`
Expected: `OK`.

Run: `cd frontend/session && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/session/components/recordings/theater/VideoControls.tsx
git commit -m "feat(session): tap-to-reveal + on-screen-clamp for proctoring flag detail on touch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Mobile sheet — panels via bottom sheet (portrait) / right drawer (landscape)

The new mobile-only component carrying the gauges + question list + "this moment" detail, plus a trigger button, wired into ReviewTheater. Reuses `QuestionRail`, `ThisMomentPanel`, `ScoreGauge`.

**Files:**
- Create: `frontend/session/components/recordings/theater/TheaterMobileSheet.tsx`
- Create: `frontend/session/tests/components/theater-mobile-sheet.test.tsx`
- Modify: `frontend/session/components/recordings/theater/ReviewTheater.tsx`
- Modify: `frontend/session/components/recordings/theater/theater.css` (append sheet styles)

**Interfaces:**
- Produces: `TheaterMobileSheet({ open, onClose, report, railMarkers, activeQuestionId, selection, offScreenPct, onSelectQuestion, onJump })` — a mobile-only sheet (hidden `>640px` via CSS).
- Consumes: `QuestionRail`, `ThisMomentPanel` (`MomentSelection`), `ScoreGauge`, `report.scores`, `verdictMeta`/`tierTone` from `../report-format`.

- [ ] **Step 1: Write the failing unit test**

Create `frontend/session/tests/components/theater-mobile-sheet.test.tsx`:
```tsx
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

import { TheaterMobileSheet } from '@/components/recordings/theater/TheaterMobileSheet'
import type { ReportRead } from '@/components/recordings/api/reports'
import type { TimelineMarker } from '@/components/recordings/theater/timeline-model'

afterEach(cleanup)

const report = {
  session_id: 's1',
  verdict: 'advance',
  scores: { overall: { score: 8, tone: 'strong' }, technical: { score: 7, tone: 'solid' } },
  decision: null,
  questions: [],
} as unknown as ReportRead

const markers: TimelineMarker[] = [
  { seq: 1, questionId: 'q1', title: 'Tell me about X', statusBadge: 'passed', tone: 'ok', askedAtMs: 1000, thumbnailUrl: null, positionPct: 10 },
]

it('renders the question list when open and fires onSelectQuestion', () => {
  const onSelect = vi.fn()
  render(
    <TheaterMobileSheet
      open
      onClose={() => {}}
      report={report}
      railMarkers={markers}
      activeQuestionId={null}
      selection={null}
      offScreenPct={null}
      onSelectQuestion={onSelect}
      onJump={() => {}}
    />,
  )
  const q = screen.getByRole('button', { name: /Tell me about X/i })
  fireEvent.click(q)
  expect(onSelect).toHaveBeenCalledWith('q1')
})

it('does not render sheet content when closed', () => {
  render(
    <TheaterMobileSheet
      open={false}
      onClose={() => {}}
      report={report}
      railMarkers={markers}
      activeQuestionId={null}
      selection={null}
      offScreenPct={null}
      onSelectQuestion={() => {}}
      onJump={() => {}}
    />,
  )
  expect(screen.queryByRole('button', { name: /Tell me about X/i })).toBeNull()
})
```

- [ ] **Step 2: Run it, verify FAIL**

Run: `cd frontend/session && npm run test -- theater-mobile-sheet`
Expected: FAIL — module `TheaterMobileSheet` does not exist.

- [ ] **Step 3: Create the component**

Create `frontend/session/components/recordings/theater/TheaterMobileSheet.tsx`:
```tsx
'use client'

import type { ReportRead } from '@/components/recordings/api/reports'
import { ScoreGauge } from '../ScoreGauge'
import { tierTone, verdictMeta, TONE_BG, TONE_INK } from '../report-format'
import { QuestionRail } from './QuestionRail'
import { ThisMomentPanel } from './ThisMomentPanel'
import type { MomentSelection } from './ThisMomentPanel'
import type { TimelineMarker } from './timeline-model'

const DIMS: { key: string; label: string; short: string }[] = [
  { key: 'overall', label: 'Overall', short: 'Overall' },
  { key: 'technical', label: 'Technical', short: 'Tech' },
  { key: 'behavioral', label: 'Behavioral', short: 'Behav' },
  { key: 'communication', label: 'Comms', short: 'Comms' },
]

/**
 * Mobile-only panel surface for the full-session theater. The desktop side
 * panels (ThisMomentPanel + QuestionRail) and the top-bar gauges don't fit on a
 * phone, so their content lives here: a bottom sheet in portrait, a right-hand
 * drawer in landscape (driven entirely by theater.css). Hidden on desktop
 * (`min-width: 641px`) via `.theater-sheet-root`.
 */
export function TheaterMobileSheet({
  open,
  onClose,
  report,
  railMarkers,
  activeQuestionId,
  selection,
  offScreenPct,
  onSelectQuestion,
  onJump,
}: {
  open: boolean
  onClose: () => void
  report: ReportRead
  railMarkers: TimelineMarker[]
  activeQuestionId: string | null
  selection: MomentSelection
  offScreenPct: number | null
  onSelectQuestion: (questionId: string) => void
  onJump: (ms: number) => void
}) {
  const v = verdictMeta(report.verdict)
  const dims = DIMS.filter(({ key }) => report.scores[key]?.score != null)
  return (
    <div className="theater-sheet-root" data-open={open ? 'true' : 'false'} aria-hidden={!open}>
      <button
        type="button"
        className="theater-sheet-backdrop"
        aria-label="Close panel"
        tabIndex={open ? 0 : -1}
        onClick={onClose}
      />
      <div className="theater-sheet" role="dialog" aria-label="Questions and scores">
        <div className="theater-sheet-grip" aria-hidden="true" />
        <div className="theater-sheet-scroll">
          <div className="mb-3 flex items-center gap-2">
            <span
              className="rounded-full px-2.5 py-0.5 text-[11px] font-bold"
              style={{ background: TONE_BG[v.tone], color: TONE_INK[v.tone] }}
            >
              {v.label}
            </span>
            {offScreenPct != null && (
              <span className="text-[11px] font-semibold" style={{ color: 'var(--px-fg-3)' }}>
                {Math.round(offScreenPct * 100)}% off-screen
              </span>
            )}
          </div>

          <div className="mb-4 flex flex-wrap gap-3">
            {dims.map(({ key, label, short }) => {
              const s = report.scores[key]
              return (
                <div key={key} className="flex flex-col items-center gap-1">
                  <ScoreGauge
                    score={s.score}
                    label={label}
                    size={48}
                    hideLabel
                    toneOverride={key === 'overall' ? v.tone : tierTone(s.tone)}
                  />
                  <span className="text-[10px] font-extrabold uppercase tracking-wide" style={{ color: 'var(--px-fg-2)' }}>
                    {short}
                  </span>
                </div>
              )
            })}
          </div>

          {selection && (
            <div className="mb-4">
              <ThisMomentPanel selection={selection} decision={report.decision} onJump={onJump} />
            </div>
          )}

          <QuestionRail
            markers={railMarkers}
            activeQuestionId={activeQuestionId}
            onSelect={onSelectQuestion}
          />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the test, verify PASS**

Run: `cd frontend/session && npm run test -- theater-mobile-sheet`
Expected: PASS (2 tests).

- [ ] **Step 5: Append sheet styles to theater.css**

Append to the END of `theater.css`:
```css
/* --- Mobile panel sheet/drawer (TheaterMobileSheet) --- */
.theater-sheet-root { display: none; }

.theater-mobile-trigger { display: none; }

@media (max-width: 640px), (orientation: landscape) and (max-height: 480px) {
  /* trigger button shown only on mobile, in the bottom slot above controls */
  .theater-mobile-trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    align-self: center;
    margin-bottom: 8px;
    min-height: 44px;
    padding: 0 16px;
    border-radius: 9999px;
    font-size: 13px;
    font-weight: 700;
    color: var(--px-fg);
    background: var(--px-surface);
    border: 1px solid var(--px-hairline-strong);
    pointer-events: auto;
  }

  .theater-sheet-root { display: block; }
  .theater-sheet-root[data-open='false'] { pointer-events: none; }

  .theater-sheet-backdrop {
    position: fixed;
    inset: 0;
    z-index: 80;
    border: 0;
    background: rgba(10, 20, 30, 0.45);
    opacity: 0;
    transition: opacity 0.2s ease;
  }
  .theater-sheet-root[data-open='true'] .theater-sheet-backdrop { opacity: 1; }

  .theater-sheet {
    position: fixed;
    z-index: 81;
    background: var(--px-bg);
    box-shadow: 0 -8px 40px rgba(10, 20, 30, 0.35);
    transition: transform 0.24s cubic-bezier(0.2, 0.8, 0.2, 1);
    display: flex;
    flex-direction: column;
  }
  .theater-sheet-scroll {
    overflow-y: auto;
    padding: 8px 16px 24px;
    -webkit-overflow-scrolling: touch;
  }
  .theater-sheet-grip {
    flex: none;
    width: 40px;
    height: 4px;
    margin: 8px auto 4px;
    border-radius: 9999px;
    background: var(--px-surface-3);
  }
}

/* portrait: bottom sheet */
@media (max-width: 640px) {
  .theater-sheet {
    left: 0; right: 0; bottom: 0;
    max-height: 72dvh;
    border-radius: 18px 18px 0 0;
    transform: translateY(100%);
  }
  .theater-sheet-root[data-open='true'] .theater-sheet { transform: translateY(0); }
}

/* landscape: right-side drawer (short height → don't eat vertical space) */
@media (orientation: landscape) and (max-height: 480px) {
  .theater-sheet {
    top: 0; bottom: 0; right: 0;
    width: min(360px, 80vw);
    border-radius: 18px 0 0 18px;
    transform: translateX(100%);
  }
  .theater-sheet-grip { display: none; }
  .theater-sheet-root[data-open='true'] .theater-sheet { transform: translateX(0); }
}
```

- [ ] **Step 6: Wire the sheet + trigger into ReviewTheater**

In `ReviewTheater.tsx`:
- Add the import (with the other theater imports):
```tsx
import { TheaterMobileSheet } from './TheaterMobileSheet'
```
- Add open state near the other `useState` calls (e.g. after `controlsVisible`):
```tsx
  const [sheetOpen, setSheetOpen] = useState(false)
```
- In the `theater-bottom` block, add the trigger ABOVE `VideoControls` and the sheet after it. Replace the `<div className="theater-bottom">…</div>` block (lines ~257-271) with:
```tsx
          <div className="theater-bottom flex flex-col">
            <button
              type="button"
              className="theater-mobile-trigger"
              onClick={() => setSheetOpen(true)}
            >
              Questions &amp; scores
            </button>
            {signedUrl && (
              <VideoControls
                controller={ctrl}
                visible={controlsVisible}
                onToggleFullscreen={toggleFullscreen}
                fullscreenSupported={fullscreenSupported}
                markers={markers}
                flags={flags}
                activeQuestionId={st.activeId}
                onSeekMs={st.seekMs}
              />
            )}
          </div>

          <TheaterMobileSheet
            open={sheetOpen}
            onClose={() => setSheetOpen(false)}
            report={report}
            railMarkers={railMarkers}
            activeQuestionId={st.activeId}
            selection={st.selection}
            offScreenPct={offScreenPct}
            onSelectQuestion={(id) => { st.selectQuestion(id); setSheetOpen(false) }}
            onJump={(ms) => { st.seekMs(ms); setSheetOpen(false) }}
          />
```
(Note: `fullscreenSupported` is added in Task 3 — ensure it is present in this VideoControls call.)

- [ ] **Step 7: Verify**

Run: `cd frontend/session && npm run test -- theater-mobile-sheet recordings-route`
Expected: PASS.

Run: `cd frontend/session && npm run type-check 2>&1 | grep "components/recordings" || echo "OK"`
Expected: `OK`.

Run: `cd frontend/session && npm run build`
Expected: succeeds; `/recordings/[token]` route present.

- [ ] **Step 8: Commit**

```bash
git add frontend/session/components/recordings/theater/TheaterMobileSheet.tsx \
        frontend/session/tests/components/theater-mobile-sheet.test.tsx \
        frontend/session/components/recordings/theater/ReviewTheater.tsx \
        frontend/session/components/recordings/theater/theater.css
git commit -m "feat(session): mobile panel sheet (portrait) / drawer (landscape) for the recordings theater

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Toggle touch sizing + final verification

The reel/full toggle pill needs a ≥44px touch target on mobile; then verify the whole feature.

**Files:**
- Modify: `frontend/session/components/recordings/PublicRecordingsView.tsx` (the toggle buttons, ~lines 82-100)

**Interfaces:** none.

- [ ] **Step 1: Enlarge the toggle buttons on touch**

In `PublicRecordingsView.tsx`, the toggle `<button>` className `rounded-full px-3.5 py-1.5 text-xs font-semibold` becomes (add a touch min-height + bigger tap area on coarse pointers):
```tsx
className="rounded-full px-3.5 py-1.5 text-xs font-semibold max-[640px]:min-h-[44px] max-[640px]:px-5"
```
Apply to each toggle button that carries that class. (If the toggle is rendered in a `.map`, edit the single shared className.)

- [ ] **Step 2: Verify the full feature**

Run from `frontend/session`:
```bash
npm run lint
npm run type-check 2>&1 | grep "error TS" | grep -v "tests/components/interview" || echo "OK: no new type errors"
npm run test -- recordings theater-mobile-sheet
npm run build
```
Expected: lint clean for touched files; `OK: no new type errors`; tests pass; build succeeds with both `/recordings/[token]` and `/interview/[token]` routes.

- [ ] **Step 3: Manual mobile check (per project "verify served frontend")**

With the dev server running (`npm run dev`, port 3002), open a real `/recordings/<token>` (or device emulation) and confirm in BOTH orientations:
- Portrait phone: no horizontal overflow; video letterboxed (full frame); controls two-row with ≥44px buttons; "Questions & scores" opens a bottom sheet; tapping a question seeks + closes the sheet; flag tick tap shows detail on-screen.
- Landscape phone: chrome auto-hides; the sheet opens as a right-side drawer.
- Rotate mid-playback: layout reflows, video keeps playing, no remount.
- Fullscreen: works on Android/desktop; on iOS uses native video fullscreen or the button is hidden.

- [ ] **Step 4: Commit**

```bash
git add frontend/session/components/recordings/PublicRecordingsView.tsx
git commit -m "feat(session): touch-sized reel/full toggle on the public recordings page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §1 video surface (contain + playsInline) → Task 1 (contain; `playsInline` already present in TheaterStage — confirmed, no task needed). ✔
- §2 touch controls (two-row, ≥44px, hide volume, fullscreen, flag tap) → Tasks 2 (two-row/volume/size), 3 (fullscreen/iOS), 4 (flag tap+clamp). ✔
- §3 ReviewTheater panels → sheet/drawer → Task 5. ✔
- §4 top bar collapse → Task 1 (gauges hidden; identity/verdict/close remain; landscape slim via Task 1/2 insets + existing auto-hide). ✔
- §5 ReelTheater control/fit/touch → inherits Tasks 1–4 (contain, two-row, fullscreen); chapters preserved (untouched). ✔
- §6 toggle sizing → Task 6. ✔
- Orientation (portrait + landscape, seamless rotation) → Tasks 1, 2, 5 (landscape-compact queries; CSS-driven, no remount). ✔
- Testing → Task 5 unit test + Task 6 manual matrix; existing tests kept green. ✔

**Placeholder scan:** No TBD/vague steps; every code step shows exact code or exact `@media` blocks; commands have expected output.

**Type consistency:** `fullscreenSupported?: boolean` defined in Task 3 (VideoControls) and consumed in Task 5's ReviewTheater VideoControls call (cross-referenced). `TheaterMobileSheet` prop names (`railMarkers`, `activeQuestionId`, `selection`, `offScreenPct`, `onSelectQuestion`, `onJump`) match between the test (Task 5 Step 1), the component (Step 3), and the ReviewTheater wiring (Step 6). `MomentSelection`, `TimelineMarker`, `ReportRead` imported from their real modules. `verdictMeta`/`tierTone`/`TONE_BG`/`TONE_INK` are the same names used in TheaterTopBar.

**Note:** `TheaterStage` already passes `playsInline` (verified at line 47-48 of the current file), so the spec's iOS `playsInline` requirement is already satisfied — no separate task. The iOS fullscreen fallback (Task 3) is the remaining iOS piece.
