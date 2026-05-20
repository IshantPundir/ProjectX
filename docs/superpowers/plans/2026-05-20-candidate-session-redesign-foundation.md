# Candidate Session Redesign — Plan 1: Foundation (Theme + Liquid Aura)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the dark "cinematic glass" visual foundation for `frontend/session` — a new `dark-cinematic` theme, reusable glass utilities, per-tenant accent injection, and the bespoke audio-reactive `LiquidAura` visualizer that becomes the AI's on-screen presence.

**Architecture:** Additive theme — a new `[data-px-theme="dark-cinematic"]` token block is added alongside the existing `warm-light` block in `app/globals.css`; the `@theme inline` Tailwind v4 bridge and all `.px-*` utilities are unchanged. The session root layout switches its `data-px-theme` and injects the tenant accent as a CSS variable. `LiquidAura` is a self-contained CSS-driven component fed by `useVoiceAssistant()` state + `useMultibandTrackVolume()` amplitude; it replaces the stock shader in the visualizer router's `aura` case so the existing live view immediately renders the new aura. Plans 2 and 3 build the new layout on top.

**Tech Stack:** Next.js 16, React 19, Tailwind v4 (CSS-variable tokens), `@livekit/components-react` 2.9.20 (`useVoiceAssistant`, `useMultibandTrackVolume`), Vitest + Testing Library + jsdom.

**Spec:** `docs/superpowers/specs/2026-05-20-candidate-session-redesign-design.md`

---

## Conventions for this plan

- All paths are relative to `frontend/session/`.
- Run commands from `frontend/session/`.
- This surface is theme-only at this stage — no behavior change to wizard/API/security. Do not touch `proxy.ts`, `next.config.ts`, `lib/api/candidate-session.ts`, or `lib/env.ts`.
- Existing tests must stay green; run `npm run test` before each commit.

---

## File structure (Plan 1)

| File | Responsibility | Action |
|---|---|---|
| `app/globals.css` | Add `[data-px-theme="dark-cinematic"]` token block + `.px-glass*` utilities + `.liquid-aura*` styles | Modify |
| `app/layout.tsx` | Switch session root to `dark-cinematic`; inject tenant accent CSS var | Modify |
| `app-config.ts` | Default `audioVisualizerType: 'aura'` | Modify |
| `components/agents-ui/liquid-aura.tsx` | The bespoke audio-reactive aura component | Create |
| `components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx` | Point the `aura` case at `LiquidAura` | Modify |
| `tests/components/agents-ui/liquid-aura.test.tsx` | Render + state + amplitude + no-track tests | Create |
| `tests/app/dark-cinematic-theme.test.ts` | Assert theme tokens present in globals.css | Create |

---

### Task 1: Add the `dark-cinematic` theme token block

The `warm-light` block at `app/globals.css:253-360` defines every `--px-*` token and the shadcn mapping. We add a sibling block with dark values plus new glass + accent-bright tokens. The `@theme inline` bridge (lines 3-251) already references `var(--px-*)`, so no change is needed there.

**Files:**
- Modify: `app/globals.css` (insert a new block immediately after the `warm-light` block closes at line 360)

- [ ] **Step 1: Add the theme block**

Insert the following immediately after line 360 (`}` closing `[data-px-theme="warm-light"]`) and before the `/* Density modifiers */` comment:

```css
[data-px-theme="dark-cinematic"] {
  /* Surfaces — deep cinematic near-black */
  --px-bg:        #07090d;
  --px-bg-2:      #0d1117;
  --px-surface:   #141a22;
  --px-surface-2: #1a2330;
  --px-surface-3: #232d3a;
  --px-hairline:        rgba(255, 255, 255, 0.10);
  --px-hairline-strong: rgba(255, 255, 255, 0.18);
  --px-divider:         rgba(255, 255, 255, 0.06);

  /* Ink — light on dark */
  --px-fg:   #ECEAE4;
  --px-fg-2: #C2BFB7;
  --px-fg-3: #9A978F;
  --px-fg-4: #6E6B64;
  --px-fg-5: #4A4842;

  /* Accent — teal (–-px-accent is overridden per-tenant at runtime by layout.tsx) */
  --px-accent:        #0E6F63;
  --px-accent-2:      #0A564D;
  --px-accent-soft:   #4FA99C;
  --px-accent-bright: #7FE6D6;
  --px-accent-glow:   rgba(79, 169, 156, 0.30);
  --px-accent-tint:   rgba(79, 169, 156, 0.12);
  --px-accent-line:   rgba(79, 169, 156, 0.24);

  /* Cinematic backdrop (radial, glow centered behind the aura) */
  --px-cine-backdrop: radial-gradient(120% 100% at 50% 36%, #18212e 0%, #0d1117 56%, #07090d 100%);

  /* Glass surfaces (restrained — accents over the backdrop, not glass-everywhere) */
  --px-glass-bg:        rgba(16, 21, 28, 0.55);
  --px-glass-bg-strong: rgba(14, 18, 24, 0.62);
  --px-glass-border:    rgba(255, 255, 255, 0.10);
  --px-glass-blur:      18px;
  --px-glass-shadow:    0 12px 40px rgba(0, 0, 0, 0.45);

  /* Semantic — hues kept, alphas tuned for dark */
  --px-ai:       #5AA0E6;
  --px-ai-bg:    rgba(90, 160, 230, 0.12);
  --px-ai-line:  rgba(90, 160, 230, 0.26);

  --px-caution:     #E0A35A;
  --px-caution-bg:  rgba(224, 163, 90, 0.12);
  --px-caution-line:rgba(224, 163, 90, 0.26);

  --px-ok:       #34C778;
  --px-ok-bg:    rgba(52, 199, 120, 0.13);
  --px-ok-line:  rgba(52, 199, 120, 0.28);

  --px-danger:      #FF5A5A;
  --px-danger-bg:   rgba(255, 90, 90, 0.12);
  --px-danger-line: rgba(255, 90, 90, 0.26);

  --px-human:      #B388D9;
  --px-human-bg:   rgba(179, 136, 217, 0.12);
  --px-human-line: rgba(179, 136, 217, 0.26);

  /* Density rhythm (comfortable default) */
  --px-row-h: 34px;
  --px-row-py: 10px;
  --px-group-gap: 14px;
  --px-topbar-h: 48px;

  /* Radii (px) */
  --px-r-xs: 4px;
  --px-r-sm: 6px;
  --px-r-md: 8px;
  --px-r-lg: 10px;
  --px-r-xl: 14px;

  /* Shadows — deep on dark */
  --px-shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.40);
  --px-shadow-md: 0 8px 24px rgba(0, 0, 0, 0.45), 0 2px 4px rgba(0, 0, 0, 0.30);
  --px-shadow-lg: 0 24px 60px rgba(0, 0, 0, 0.60), 0 6px 16px rgba(0, 0, 0, 0.40);

  /* Motion */
  --px-ease: cubic-bezier(0.2, 0.7, 0.2, 1);
  --px-d1: 120ms;
  --px-d2: 200ms;
  --px-d3: 320ms;

  /* ─── shadcn semantic tokens mapped to the dark palette ─── */
  --background: var(--px-bg);
  --foreground: var(--px-fg);
  --card: var(--px-surface);
  --card-foreground: var(--px-fg);
  --popover: var(--px-surface);
  --popover-foreground: var(--px-fg);
  --primary: var(--px-accent);
  --primary-foreground: #FFFFFF;
  --secondary: var(--px-surface-2);
  --secondary-foreground: var(--px-fg-2);
  --muted: var(--px-bg-2);
  --muted-foreground: var(--px-fg-3);
  --accent: var(--px-surface-2);
  --accent-foreground: var(--px-fg);
  --destructive: var(--px-danger);
  --border: var(--px-hairline);
  --input: var(--px-surface-3);
  --ring: var(--px-accent);
  --chart-1: var(--px-accent);
  --chart-2: var(--px-ai);
  --chart-3: var(--px-caution);
  --chart-4: var(--px-human);
  --chart-5: var(--px-accent-soft);
  --radius: 0.5rem;
  --sidebar: var(--px-bg-2);
  --sidebar-foreground: var(--px-fg);
  --sidebar-primary: var(--px-accent);
  --sidebar-primary-foreground: #FFFFFF;
  --sidebar-accent: var(--px-surface-2);
  --sidebar-accent-foreground: var(--px-fg);
  --sidebar-border: var(--px-hairline);
  --sidebar-ring: var(--px-accent);
}
```

- [ ] **Step 2: Verify the build still compiles**

Run: `npm run build`
Expected: build succeeds (Tailwind v4 parses the new block; no "unexpected token" errors).

- [ ] **Step 3: Commit**

```bash
git add app/globals.css
git commit -m "feat(session): add dark-cinematic theme token block"
```

---

### Task 2: Add glass utilities + cinematic backdrop + aura styles to globals.css

These are plain `.px-*` classes (matching the existing `.px-btn`/`.px-input` convention, declared outside `@layer`). Add at the end of `app/globals.css`.

**Files:**
- Modify: `app/globals.css` (append at end of file, after line 985)

- [ ] **Step 1: Append the utility + aura styles**

```css

/* ───────────────────────── Cinematic glass (dark-cinematic theme) ───────────────────────── */

.px-cine-bg {
  background: var(--px-cine-backdrop);
}

.px-glass {
  background: var(--px-glass-bg);
  border: 1px solid var(--px-glass-border);
  -webkit-backdrop-filter: blur(var(--px-glass-blur));
  backdrop-filter: blur(var(--px-glass-blur));
  box-shadow: var(--px-glass-shadow);
}
.px-glass-strong { background: var(--px-glass-bg-strong); }

/* Glass pill — used by progress chip, Live pill, control chips */
.px-glass-pill {
  background: var(--px-glass-bg);
  border: 1px solid var(--px-glass-border);
  -webkit-backdrop-filter: blur(var(--px-glass-blur));
  backdrop-filter: blur(var(--px-glass-blur));
  border-radius: 999px;
}

/* ───────────────────────── Liquid aurora visualizer ───────────────────────── */

.liquid-aura {
  --amp: 0;                       /* 0..1 audio amplitude, set inline by the component */
  position: relative;
  display: grid;
  place-items: center;
  color: var(--px-accent);        /* tenant accent flows in here */
  pointer-events: none;
}
.liquid-aura__glow,
.liquid-aura__body,
.liquid-aura__sheen { position: absolute; border-radius: 50%; }

.liquid-aura__glow {
  inset: -22%;
  background: radial-gradient(circle,
    color-mix(in srgb, var(--px-accent-soft) 55%, transparent),
    transparent 62%);
  filter: blur(26px);
  opacity: calc(0.55 + var(--amp) * 0.45);
  animation: liquid-aura-breathe 5s var(--px-ease) infinite;
}
.liquid-aura__body {
  inset: 12%;
  filter: blur(8px);
  transform: scale(calc(1 + var(--amp) * 0.10));
  animation: liquid-aura-morph 8s var(--px-ease) infinite;
}
.liquid-aura__body::before {
  content: "";
  position: absolute; inset: 0; border-radius: inherit;
  background: conic-gradient(from 0deg,
    var(--px-accent),
    var(--px-accent-soft),
    var(--px-accent-bright),
    var(--px-accent-soft),
    var(--px-accent));
  animation: liquid-aura-spin 9s linear infinite;
}
.liquid-aura__sheen {
  inset: 26%;
  background: radial-gradient(circle at 38% 32%, rgba(255,255,255,0.5), transparent 46%);
  filter: blur(4px);
  mix-blend-mode: screen;
  animation: liquid-aura-breathe 4s var(--px-ease) infinite;
}

/* State modulation (data-lk-state mirrors the LiveKit AgentState) */
.liquid-aura[data-lk-state="thinking"] .liquid-aura__body::before { animation-duration: 4.5s; }
.liquid-aura[data-lk-state="thinking"] .liquid-aura__glow { opacity: calc(0.4 + var(--amp) * 0.3); }
.liquid-aura[data-lk-state="listening"] .liquid-aura__body::before { animation-duration: 12s; }
.liquid-aura[data-lk-state="speaking"] .liquid-aura__glow { opacity: calc(0.7 + var(--amp) * 0.3); }
.liquid-aura[data-lk-state="connecting"] .liquid-aura__glow,
.liquid-aura[data-lk-state="initializing"] .liquid-aura__glow { opacity: 0.35; }

@keyframes liquid-aura-spin { to { transform: rotate(360deg); } }
@keyframes liquid-aura-breathe {
  0%, 100% { transform: scale(0.93); }
  50%      { transform: scale(1.07); }
}
@keyframes liquid-aura-morph {
  0%, 100% { border-radius: 50% 50% 50% 50% / 55% 55% 45% 45%; }
  33%      { border-radius: 58% 42% 55% 45% / 45% 55% 45% 55%; }
  66%      { border-radius: 45% 55% 48% 52% / 52% 44% 56% 48%; }
}

@media (prefers-reduced-motion: reduce) {
  .liquid-aura__glow,
  .liquid-aura__body,
  .liquid-aura__body::before,
  .liquid-aura__sheen {
    animation: none !important;
    transform: none !important;
  }
  .liquid-aura__glow { opacity: 0.5; }
}
```

- [ ] **Step 2: Verify build**

Run: `npm run build`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add app/globals.css
git commit -m "feat(session): add glass utilities + liquid-aura styles"
```

---

### Task 3: Theme presence regression test

A lightweight guard so a future edit can't silently drop the dark-cinematic tokens or the aura styles.

**Files:**
- Create: `tests/app/dark-cinematic-theme.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../app/globals.css'), 'utf8')

describe('dark-cinematic theme', () => {
  it('declares the dark-cinematic theme block', () => {
    expect(css).toContain('[data-px-theme="dark-cinematic"]')
  })

  it('defines the glass + accent-bright tokens used by the cinematic UI', () => {
    for (const token of ['--px-glass-bg', '--px-glass-border', '--px-accent-bright', '--px-cine-backdrop']) {
      expect(css).toContain(token)
    }
  })

  it('ships the liquid-aura styles and a reduced-motion guard', () => {
    expect(css).toContain('.liquid-aura')
    expect(css).toContain('@keyframes liquid-aura-morph')
    expect(css).toContain('prefers-reduced-motion: reduce')
  })
})
```

- [ ] **Step 2: Run to verify it passes** (tokens already added in Tasks 1-2)

Run: `npm run test -- dark-cinematic-theme`
Expected: PASS (3 tests). If it fails, the tokens from Tasks 1-2 are missing — fix globals.css.

- [ ] **Step 3: Commit**

```bash
git add tests/app/dark-cinematic-theme.test.ts
git commit -m "test(session): guard dark-cinematic theme tokens"
```

---

### Task 4: Build the `LiquidAura` component (failing test first)

A self-contained, audio-reactive aura. Props: `state` (AgentState), `audioTrack`, `size` (`'hero' | 'mark'`), optional `color`, `className`. Internally reads a single-band amplitude via `useMultibandTrackVolume` and writes it to the `--amp` CSS variable; all visual motion lives in CSS (Task 2). Renders an `aria-label` and respects reduced motion via CSS.

**Files:**
- Create: `components/agents-ui/liquid-aura.tsx`
- Test: `tests/components/agents-ui/liquid-aura.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Mock the LiveKit hook so the component renders without a real room.
const multibandMock = vi.fn(() => [0.6])
vi.mock('@livekit/components-react', () => ({
  useMultibandTrackVolume: (...args: unknown[]) => multibandMock(...args),
}))

import { LiquidAura } from '@/components/agents-ui/liquid-aura'

describe('LiquidAura', () => {
  it('renders with the current agent state as a data attribute', () => {
    render(<LiquidAura state="speaking" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    expect(el).toHaveAttribute('data-lk-state', 'speaking')
  })

  it('writes the smoothed amplitude to the --amp CSS variable', () => {
    multibandMock.mockReturnValueOnce([0.6])
    render(<LiquidAura state="speaking" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    // amplitude is clamped 0..1; with a single band of 0.6 it must be > 0.
    const amp = Number((el as HTMLElement).style.getPropertyValue('--amp'))
    expect(amp).toBeGreaterThan(0)
    expect(amp).toBeLessThanOrEqual(1)
  })

  it('does not crash and reports zero amplitude when there is no audio track', () => {
    multibandMock.mockReturnValueOnce([])
    render(<LiquidAura state="listening" audioTrack={undefined} />)
    const el = screen.getByRole('img', { name: /interviewer/i })
    expect(el).toHaveAttribute('data-lk-state', 'listening')
    expect((el as HTMLElement).style.getPropertyValue('--amp')).toBe('0')
  })

  it('applies the mark size class when size="mark"', () => {
    render(<LiquidAura state="listening" audioTrack={undefined} size="mark" />)
    expect(screen.getByRole('img', { name: /interviewer/i })).toHaveAttribute('data-aura-size', 'mark')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- liquid-aura`
Expected: FAIL with "Failed to resolve import '@/components/agents-ui/liquid-aura'".

- [ ] **Step 3: Implement the component**

Create `components/agents-ui/liquid-aura.tsx`:

```tsx
'use client'

import { type CSSProperties, useMemo } from 'react'
import { type LocalAudioTrack, type RemoteAudioTrack } from 'livekit-client'
import {
  type AgentState,
  type TrackReferenceOrPlaceholder,
  useMultibandTrackVolume,
} from '@livekit/components-react'

import { cn } from '@/lib/utils'

export interface LiquidAuraProps {
  /** Current agent state — drives the CSS state modulation. */
  state?: AgentState
  /** Agent audio track; amplitude is derived from it. */
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  /** Hero (full size) or mark (small avatar / minimized). */
  size?: 'hero' | 'mark'
  /** Optional accent override (defaults to the theme's --px-accent). */
  color?: `#${string}`
  className?: string
}

const SIZE_CLASS: Record<NonNullable<LiquidAuraProps['size']>, string> = {
  hero: 'size-[260px] sm:size-[340px] md:size-[420px]',
  mark: 'size-[22px]',
}

/**
 * Bespoke audio-reactive "Liquid aurora" — the AI interviewer's on-screen
 * presence. All motion is CSS (see .liquid-aura* in globals.css); this
 * component only maps audio amplitude to the --amp CSS variable and the
 * agent state to data-lk-state. Honors prefers-reduced-motion via CSS.
 */
export function LiquidAura({
  state = 'connecting',
  audioTrack,
  size = 'hero',
  color,
  className,
}: LiquidAuraProps) {
  // One band = overall loudness. loPass/hiPass mirror the bar visualizer.
  const bands = useMultibandTrackVolume(audioTrack, { bands: 1, loPass: 100, hiPass: 200 })

  const amp = useMemo(() => {
    const raw = bands.length > 0 ? bands[0] : 0
    if (!Number.isFinite(raw) || raw <= 0) return 0
    return Math.min(1, raw)
  }, [bands])

  const style = {
    '--amp': String(amp),
    ...(color ? { color } : {}),
  } as CSSProperties

  return (
    <div
      role="img"
      aria-label="AI interviewer"
      data-lk-state={state}
      data-aura-size={size}
      style={style}
      className={cn('liquid-aura', SIZE_CLASS[size], className)}
    >
      <div className="liquid-aura__glow" />
      <div className="liquid-aura__body">
        <div className="liquid-aura__sheen" />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- liquid-aura`
Expected: PASS (4 tests).

- [ ] **Step 5: Type-check**

Run: `npm run type-check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add components/agents-ui/liquid-aura.tsx tests/components/agents-ui/liquid-aura.test.tsx
git commit -m "feat(session): bespoke LiquidAura audio-reactive visualizer"
```

---

### Task 5: Point the visualizer router's `aura` case at `LiquidAura`

The router at `audio-visualizer.tsx:51-62` currently renders the stock `AgentAudioVisualizerAura` (GLSL shader). Swap it for `LiquidAura` so the existing live view renders the new aura immediately. Keep the other cases untouched.

**Files:**
- Modify: `components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx`

- [ ] **Step 1: Replace the aura import and case**

Change the import (line 8) from:

```tsx
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura';
```

to:

```tsx
import { LiquidAura } from '@/components/agents-ui/liquid-aura';
```

Delete the `MotionAgentAudioVisualizerAura` declaration (line 14):

```tsx
const MotionAgentAudioVisualizerAura = motion.create(AgentAudioVisualizerAura);
```

Replace the entire `case 'aura':` block (lines 51-62) with:

```tsx
    case 'aura': {
      return (
        <LiquidAura
          state={state}
          audioTrack={audioTrack}
          color={audioVisualizerColor}
          size="hero"
          className={className}
        />
      );
    }
```

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: no errors. (`audioVisualizerColorShift` is no longer read by the aura case — that is fine; it remains a prop for other cases.)

- [ ] **Step 3: Run the full test suite**

Run: `npm run test`
Expected: PASS — all existing tests plus the new ones. No regressions.

- [ ] **Step 4: Commit**

```bash
git add components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx
git commit -m "feat(session): render LiquidAura in the visualizer aura case"
```

---

### Task 6: Switch the session root to `dark-cinematic` + inject tenant accent

`app/layout.tsx:49` sets `data-px-theme="warm-light"`. Switch it to `dark-cinematic` and set the default tenant accent CSS variable on the wrapper div so per-tenant accent (Plan 2 reads `app-config.accent`) flows into the tokens. The body background already reads `var(--px-bg)` via the inline style on the wrapper div (lines 65-71), so the dark backdrop applies automatically.

**Files:**
- Modify: `app/layout.tsx:49` and the wrapper `<div>` style (lines 65-71)

- [ ] **Step 1: Switch the theme attribute**

Change line 49 from:

```tsx
      data-px-theme="warm-light"
```

to:

```tsx
      data-px-theme="dark-cinematic"
```

- [ ] **Step 2: Inject the default accent variable on the wrapper**

Replace the wrapper `<div>` (lines 65-73) with the following. The `as CSSProperties` cast is required because custom properties (`--px-accent`) are not in React's typed `CSSProperties` keys:

```tsx
          <div
            className="min-h-screen w-full"
            style={
              {
                // --px-accent default; Plan 2 overrides this per-tenant from app-config.accent.
                "--px-accent": "#0E6F63",
                background: "var(--px-bg)",
                color: "var(--px-fg)",
              } as CSSProperties
            }
          >
            {children}
          </div>
```

Add `CSSProperties` to the existing React type import at the top of the file. Change:

```tsx
import type { ReactNode } from "react";
```

to:

```tsx
import type { CSSProperties, ReactNode } from "react";
```

- [ ] **Step 3: Verify build + visual sanity**

Run: `npm run build`
Expected: success.

Run: `npm run dev`, open `http://localhost:3002/` — the root landing page should now render on the dark background (warm-light is gone). Stop the dev server.

- [ ] **Step 4: Commit**

```bash
git add app/layout.tsx
git commit -m "feat(session): switch candidate surface to dark-cinematic theme"
```

---

### Task 7: Default the visualizer to `aura`

**Files:**
- Modify: `app-config.ts:42`

- [ ] **Step 1: Change the default**

Change line 42 from:

```ts
  audioVisualizerType: 'bar',
```

to:

```ts
  audioVisualizerType: 'aura',
```

- [ ] **Step 2: Type-check + test**

Run: `npm run type-check && npm run test`
Expected: no type errors; all tests pass.

- [ ] **Step 3: Commit**

```bash
git add app-config.ts
git commit -m "feat(session): default audio visualizer to aura"
```

---

## Self-review notes (verified while writing)

- **Spec coverage:** dark-cinematic tokens (§Visual language), glass utilities (§Visual language), per-tenant accent via CSS var (§Visual language, default teal) and the bespoke Liquid aurora with state mapping + reduced-motion + two sizes (§The Liquid aurora visualizer) are all covered. Live-session layout, wizard, terminal states, and the End-wiring test are **Plan 2/Plan 3** scope — intentionally not here.
- **Type consistency:** `LiquidAura` props (`state`, `audioTrack`, `size`, `color`, `className`) match every call site (Task 5 router case). `useMultibandTrackVolume(audioTrack, { bands, loPass, hiPass })` matches the signature used by `agent-audio-visualizer-bar.tsx`.
- **No placeholders:** every step has concrete code and exact commands.
- **Constraints:** no Supabase, no security-header/proxy/API changes, no forbidden deps. The aura is CSS-driven (mobile + reduced-motion safe), not a heavy shader.

## Notes for Plan 2/3 authors

- `app/globals.css` now exposes `.px-glass`, `.px-glass-strong`, `.px-glass-pill`, `.px-cine-bg`, and the `LiquidAura` component (`size="hero"` and `size="mark"`).
- The stock `agent-audio-visualizer-aura.tsx` is now unused; Plan 2 may delete it (and `react-shader-toy.tsx` if no other consumer) after confirming no imports remain (`grep -rn "agent-audio-visualizer-aura\|react-shader-toy" .`).
- Per-tenant accent: override `--px-accent` on the session subtree from `app-config.accent` (Plan 2), and the aura + glass + buttons all follow automatically.
