# Candidate Session — Light Glassmorphism Re-skin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-skin the already-built candidate session surface from dark-cinematic + custom Liquid aurora to a **cool-light glassmorphism** look: a soft cool-light theme, prominent frosted glass, a gently animated ambient background, and LiveKit's **stock `AgentAudioVisualizerAura`** shader (`colorShift: 2`, `themeMode="light"`) as the hero — replacing the custom `LiquidAura`.

**Architecture:** Layout/components/wiring are UNCHANGED (per spec Revision b). We (1) rewrite the `globals.css` theme block from `dark-cinematic` → `cool-light` and beef up the glass utilities, (2) add a reduced-motion-safe `AnimatedBackground` mounted once in the root layout, (3) restore LiveKit's stock aura files from git history, (4) introduce an `Aura` wrapper that renders the stock shader (with a static `.aura-mark` fallback under `prefers-reduced-motion`), and (5) swap every `LiquidAura` usage to `Aura` (heroes) or the `.aura-mark` CSS class (small panel marks), then delete `LiquidAura`.

**Tech Stack:** Next.js 16, React 19, Tailwind v4, `@livekit/components-react`, LiveKit stock `AgentAudioVisualizerAura` (WebGL via `ReactShaderToy`), Vitest + Testing Library + jsdom.

**Spec:** `docs/superpowers/specs/2026-05-20-candidate-session-redesign-design.md` (see "⚠️ Revision 2026-05-20b").
**Branch:** `feat/session-light-glass-reskin` (already checked out).

---

## Conventions

- All paths relative to `frontend/session/`. Run commands from `frontend/session/`.
- Baseline: `npm run type-check` has 4 PRE-EXISTING errors in `tests/components/interview/{outcome-watcher,session-outcome}` — ignore; introduce no NEW errors.
- **Git guardrails for every implementer:** do NOT run `git stash`/`pop`/`apply`/`merge`/`rebase` or `git checkout` of files outside the task; `git add` ONLY the listed files; before reporting run `git show --stat HEAD` and confirm scope (nothing under `frontend/app/`). An unrelated user stash exists — never touch it.
- Run `npm run test` before each commit.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `app/globals.css` | `cool-light` theme block, prominent `.px-glass*`, `.aura-mark`, animated-bg keyframe; remove `dark-cinematic` block + `.liquid-aura*` | Modify |
| `app/layout.tsx` | `data-px-theme="cool-light"`, light base bg, mount `<AnimatedBackground/>` | Modify |
| `hooks/use-prefers-reduced-motion.ts` | jsdom-safe reduced-motion hook | Create |
| `tests/setup.ts` | add `matchMedia` polyfill | Modify |
| `components/agents-ui/animated-background.tsx` | drifting ambient blobs (reduced-motion-safe) | Create |
| `components/agents-ui/agent-audio-visualizer-aura.tsx` | LiveKit stock aura | Restore (git) |
| `components/agents-ui/react-shader-toy.tsx` | WebGL shader host | Restore (git) |
| `hooks/agents-ui/use-agent-audio-visualizer-aura.ts` | stock aura hook | Restore (git) |
| `components/agents-ui/aura.tsx` | `Aura` wrapper (stock shader + reduced-motion fallback, colorShift 2) | Create |
| `components/interview/session/AuraStage.tsx` | use `Aura` | Modify |
| `components/interview/app/welcome-view.tsx` | use `Aura` | Modify |
| `app/interview/[token]/WizardFrame.tsx` | use `Aura` | Modify |
| `components/interview/session/InterviewSessionPanel.tsx` | use `.aura-mark` spans | Modify |
| `app-config.ts` | `audioVisualizerColorShift: 2` | Modify |
| `components/agents-ui/liquid-aura.tsx` + its test | delete | Delete |
| `tests/app/dark-cinematic-theme.test.ts` → `cool-light-theme.test.ts` | theme guard | Replace |
| `tests/components/interview/{app,session/LiveInterview}.test.tsx` | mock stock aura | Modify |

---

### Task 1: Cool-light theme + prominent glass + remove dark/liquid-aura CSS

**Files:**
- Modify: `app/globals.css` (replace the `[data-px-theme="dark-cinematic"]` block; replace the glass/aura CSS section)
- Modify: `app/layout.tsx`
- Create: `tests/app/cool-light-theme.test.ts`
- Delete: `tests/app/dark-cinematic-theme.test.ts`

- [ ] **Step 1: Replace the dark-cinematic token block with cool-light**

In `app/globals.css`, find the block that begins `[data-px-theme="dark-cinematic"] {` and ends at its closing `}` (it's between the `warm-light` block and the `/* Density modifiers */` comment). Replace that ENTIRE block with:

```css
[data-px-theme="cool-light"] {
  /* Surfaces — soft cool white */
  --px-bg:        #F4F6FB;
  --px-bg-2:      #EBEEF6;
  --px-surface:   #FFFFFF;
  --px-surface-2: #F2F4FA;
  --px-surface-3: #E2E7F1;
  --px-hairline:        rgba(38, 50, 76, 0.12);
  --px-hairline-strong: rgba(38, 50, 76, 0.20);
  --px-divider:         rgba(38, 50, 76, 0.07);

  /* Ink — cool slate */
  --px-fg:   #1F2733;
  --px-fg-2: #3D4654;
  --px-fg-3: #5B6573;
  --px-fg-4: #8A93A3;
  --px-fg-5: #BAC1CD;

  /* Accent — teal (overridable per tenant) */
  --px-accent:        #0E6F63;
  --px-accent-2:      #0A564D;
  --px-accent-soft:   #4FA99C;
  --px-accent-bright: #7FE6D6;
  --px-accent-glow:   rgba(14, 111, 99, 0.22);
  --px-accent-tint:   rgba(14, 111, 99, 0.10);
  --px-accent-line:   rgba(14, 111, 99, 0.22);

  /* App base wash (.px-cine-bg is transparent so the animated bg shows through) */
  --px-app-base: linear-gradient(160deg, #F6F8FD 0%, #EEF1F8 100%);

  /* Glass — prominent frosted white */
  --px-glass-bg:        rgba(255, 255, 255, 0.45);
  --px-glass-bg-strong: rgba(255, 255, 255, 0.62);
  --px-glass-border:    rgba(255, 255, 255, 0.65);
  --px-glass-blur:      22px;
  --px-glass-shadow:    0 8px 30px rgba(40, 50, 70, 0.12);

  /* Semantic — tuned for light */
  --px-ai:       #2B6CB8; --px-ai-bg: rgba(43,108,184,0.10); --px-ai-line: rgba(43,108,184,0.22);
  --px-caution:  #B5740F; --px-caution-bg: rgba(181,116,15,0.10); --px-caution-line: rgba(181,116,15,0.24);
  --px-ok:       #0A8F5B; --px-ok-bg: rgba(10,143,91,0.10); --px-ok-line: rgba(10,143,91,0.26);
  --px-danger:   #C0362B; --px-danger-bg: rgba(192,54,43,0.10); --px-danger-line: rgba(192,54,43,0.24);
  --px-human:    #6D4FB8; --px-human-bg: rgba(109,79,184,0.10); --px-human-line: rgba(109,79,184,0.22);

  /* Density rhythm */
  --px-row-h: 34px; --px-row-py: 10px; --px-group-gap: 14px; --px-topbar-h: 48px;

  /* Radii */
  --px-r-xs: 4px; --px-r-sm: 6px; --px-r-md: 8px; --px-r-lg: 10px; --px-r-xl: 14px;

  /* Shadows */
  --px-shadow-sm: 0 1px 2px rgba(40, 50, 70, 0.06);
  --px-shadow-md: 0 8px 24px rgba(40, 50, 70, 0.10), 0 2px 4px rgba(40, 50, 70, 0.06);
  --px-shadow-lg: 0 24px 60px rgba(40, 50, 70, 0.16), 0 6px 16px rgba(40, 50, 70, 0.08);

  /* Motion */
  --px-ease: cubic-bezier(0.2, 0.7, 0.2, 1); --px-d1: 120ms; --px-d2: 200ms; --px-d3: 320ms;

  /* ─── shadcn semantic tokens ─── */
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

- [ ] **Step 2: Replace the glass utilities + aura styles (remove `.liquid-aura*`)**

In `app/globals.css`, find the section that starts with the comment `/* ───────────────────────── Cinematic glass (dark-cinematic theme) ───────────────────────── */` and runs through the end of the `.liquid-aura` styles + the `@media (prefers-reduced-motion: reduce)` block that disables them (the `.liquid-aura__glow { opacity: 0.5; }` block). Replace that ENTIRE section with:

```css

/* ───────────────────────── Light glass + ambient background ───────────────────────── */

/* .px-cine-bg is transparent: the animated background (mounted in layout) shows through. */
.px-cine-bg { background: transparent; }
.px-app-base { background: var(--px-app-base); }

.px-glass {
  background: var(--px-glass-bg);
  border: 1px solid var(--px-glass-border);
  -webkit-backdrop-filter: blur(var(--px-glass-blur)) saturate(1.4);
  backdrop-filter: blur(var(--px-glass-blur)) saturate(1.4);
  box-shadow: var(--px-glass-shadow), inset 0 1px 0 rgba(255, 255, 255, 0.7);
}
.px-glass-strong { background: var(--px-glass-bg-strong); }

.px-glass-pill {
  background: var(--px-glass-bg);
  border: 1px solid var(--px-glass-border);
  -webkit-backdrop-filter: blur(var(--px-glass-blur)) saturate(1.4);
  backdrop-filter: blur(var(--px-glass-blur)) saturate(1.4);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
  border-radius: 999px;
}

/* Multi-hue gradient mark — panel avatars + the reduced-motion aura fallback */
.aura-mark {
  border-radius: 50%;
  background: conic-gradient(from 0deg, #2fd0bb, #3aa7ff, #8a7bff, #ff7bd5, #ffb86b, #6fe6a8, #2fd0bb);
  box-shadow: 0 0 10px rgba(80, 140, 220, 0.35);
}

/* Animated ambient background (component renders .px-bg-blob spans) */
.px-bg-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(48px);
  opacity: 0.55;
  animation: px-drift 17s ease-in-out infinite alternate;
}
@keyframes px-drift {
  from { transform: translate(0, 0) scale(1); }
  to   { transform: translate(5%, 4%) scale(1.15); }
}
@media (prefers-reduced-motion: reduce) {
  .px-bg-blob { animation: none; }
}
```

- [ ] **Step 3: Switch the layout theme + base background**

In `app/layout.tsx`: change `data-px-theme="dark-cinematic"` → `data-px-theme="cool-light"`, and in the wrapper `<div>` inline style change `background: "var(--px-bg)"` → `background: "var(--px-app-base)"`. (Leave the `--px-accent` line and `color` as-is. The `<AnimatedBackground/>` mount comes in Task 3.)

- [ ] **Step 4: Replace the theme guard test**

Delete the old test and create the new one:

```bash
git rm tests/app/dark-cinematic-theme.test.ts
```

Create `tests/app/cool-light-theme.test.ts`:

```typescript
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../app/globals.css'), 'utf8')

describe('cool-light theme', () => {
  it('declares the cool-light theme block', () => {
    expect(css).toContain('[data-px-theme="cool-light"]')
  })
  it('defines the prominent glass + app-base tokens', () => {
    for (const t of ['--px-glass-bg', '--px-glass-border', '--px-app-base', '--px-accent']) {
      expect(css).toContain(t)
    }
  })
  it('ships the aura-mark + animated-background styles with a reduced-motion guard', () => {
    expect(css).toContain('.aura-mark')
    expect(css).toContain('@keyframes px-drift')
    expect(css).toContain('prefers-reduced-motion: reduce')
  })
  it('no longer ships the retired dark-cinematic / liquid-aura styles', () => {
    expect(css).not.toContain('[data-px-theme="dark-cinematic"]')
    expect(css).not.toContain('liquid-aura')
  })
})
```

- [ ] **Step 5: Verify + commit**

Run: `npm run build && npm run test -- cool-light-theme` (build success; 4 tests pass). Then `npm run test` (all pass — the old dark-cinematic-theme test is gone).
Note: `liquid-aura.tsx` still exists and renders (its own test still passes — it asserts DOM attributes, not CSS), but is now unstyled. It is deleted in Task 6.

```bash
git add app/globals.css app/layout.tsx tests/app/cool-light-theme.test.ts tests/app/dark-cinematic-theme.test.ts
git commit -m "feat(session): cool-light theme + prominent glass; retire dark-cinematic + liquid-aura CSS"
```

---

### Task 2: `usePrefersReducedMotion` hook + jsdom `matchMedia` polyfill

**Files:**
- Create: `hooks/use-prefers-reduced-motion.ts`
- Modify: `tests/setup.ts`
- Test: `tests/hooks/use-prefers-reduced-motion.test.tsx`

- [ ] **Step 1: Add a `matchMedia` polyfill to `tests/setup.ts`**

Append to `tests/setup.ts` (jsdom has no `matchMedia`; our hook + reduced-motion code needs it):

```typescript
// jsdom has no matchMedia; provide a no-match stub so reduced-motion code runs.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}
```

- [ ] **Step 2: Write the failing test** `tests/hooks/use-prefers-reduced-motion.test.tsx`:

```tsx
import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

describe('usePrefersReducedMotion', () => {
  it('returns false by default (matchMedia stub does not match)', () => {
    const { result } = renderHook(() => usePrefersReducedMotion())
    expect(result.current).toBe(false)
  })
  it('does not throw when matchMedia is unavailable', () => {
    const original = window.matchMedia
    // @ts-expect-error force-remove for the test
    delete window.matchMedia
    expect(() => renderHook(() => usePrefersReducedMotion())).not.toThrow()
    window.matchMedia = original
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `npm run test -- use-prefers-reduced-motion`
Expected: FAIL (import unresolved).

- [ ] **Step 4: Implement** `hooks/use-prefers-reduced-motion.ts`:

```typescript
'use client'

import { useEffect, useState } from 'react'

/** True when the user requested reduced motion. SSR/jsdom-safe (defaults false). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduced(mq.matches)
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return reduced
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npm run test -- use-prefers-reduced-motion`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add hooks/use-prefers-reduced-motion.ts tests/setup.ts tests/hooks/use-prefers-reduced-motion.test.tsx
git commit -m "feat(session): prefers-reduced-motion hook + jsdom matchMedia polyfill"
```

---

### Task 3: `AnimatedBackground` component + mount in layout

**Files:**
- Create: `components/agents-ui/animated-background.tsx`
- Modify: `app/layout.tsx`
- Test: `tests/components/agents-ui/animated-background.test.tsx`

- [ ] **Step 1: Write the failing test** `tests/components/agents-ui/animated-background.test.tsx`:

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnimatedBackground } from '@/components/agents-ui/animated-background'

describe('AnimatedBackground', () => {
  it('renders a decorative, aria-hidden, fixed layer with drifting blobs', () => {
    const { container } = render(<AnimatedBackground />)
    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveAttribute('aria-hidden', 'true')
    expect(root.className).toContain('fixed')
    expect(root.querySelectorAll('.px-bg-blob').length).toBeGreaterThanOrEqual(3)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- animated-background`
Expected: FAIL.

- [ ] **Step 3: Implement** `components/agents-ui/animated-background.tsx`:

```tsx
'use client'

import { type CSSProperties } from 'react'
import { cn } from '@/lib/utils'

// Cool-light ambient blobs (sky / lavender / mint / blush). Drift + reduced-motion
// freeze are handled by the .px-bg-blob class in globals.css.
const BLOBS: { color: string; style: CSSProperties }[] = [
  { color: '#cfe0ff', style: { width: '46vw', height: '46vw', left: '-10vw', top: '-12vh' } },
  { color: '#ddd4ff', style: { width: '42vw', height: '42vw', right: '-8vw', top: '6vh', animationDelay: '-4s' } },
  { color: '#d4f0e6', style: { width: '50vw', height: '50vw', left: '15vw', bottom: '-20vh', animationDelay: '-8s' } },
  { color: '#ffe0ec', style: { width: '34vw', height: '34vw', right: '12vw', bottom: '-10vh', animationDelay: '-2s' } },
]

/** Decorative drifting ambient background. Mounted once behind all content. */
export function AnimatedBackground({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn('px-app-base pointer-events-none fixed inset-0 -z-10 overflow-hidden', className)}>
      {BLOBS.map((b, i) => (
        <span key={i} className="px-bg-blob" style={{ background: b.color, ...b.style }} />
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- animated-background`
Expected: PASS.

- [ ] **Step 5: Mount it in `app/layout.tsx`**

Add the import at the top:

```tsx
import { AnimatedBackground } from "@/components/agents-ui/animated-background";
```

Inside the wrapper `<div style={...}>`, render `<AnimatedBackground />` as the FIRST child, before `{children}`:

```tsx
          <div
            className="min-h-screen w-full"
            style={ /* unchanged: --px-accent, background: var(--px-app-base), color */ }
          >
            <AnimatedBackground />
            {children}
          </div>
```

(The `-z-10` keeps it behind content; `px-app-base` paints the gradient even where blobs don't cover.)

- [ ] **Step 6: Verify + commit**

Run: `npm run type-check && npm run build && npm run test`
Expected: no new type errors; build success; all tests pass.

```bash
git add components/agents-ui/animated-background.tsx tests/components/agents-ui/animated-background.test.tsx app/layout.tsx
git commit -m "feat(session): animated ambient background (reduced-motion-safe)"
```

---

### Task 4: Restore LiveKit's stock aura files from git history

**Files (restore from `d18a3ac`, the commit before the redesign):**
- `components/agents-ui/agent-audio-visualizer-aura.tsx`
- `components/agents-ui/react-shader-toy.tsx`
- `hooks/agents-ui/use-agent-audio-visualizer-aura.ts`

- [ ] **Step 1: Restore the three files**

```bash
git checkout d18a3ac -- \
  frontend/session/components/agents-ui/agent-audio-visualizer-aura.tsx \
  frontend/session/components/agents-ui/react-shader-toy.tsx \
  frontend/session/hooks/agents-ui/use-agent-audio-visualizer-aura.ts
```

(Run from the repo root `/home/ishant/Projects/ProjectX`, or prefix the paths accordingly. `git checkout <sha> -- <paths>` stages the restored files.)

- [ ] **Step 2: Verify they compile in isolation**

Run: `npm run type-check`
Expected: only the 4 baseline errors. If the restored files reference an import that no longer exists (they should only need `@/components/agents-ui/react-shader-toy`, `@/hooks/agents-ui/use-agent-audio-visualizer-aura`, `@/lib/utils`, `class-variance-authority`, `livekit-client`, `@livekit/components-react` — all present), report it.

Run: `npm run build`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add components/agents-ui/agent-audio-visualizer-aura.tsx components/agents-ui/react-shader-toy.tsx hooks/agents-ui/use-agent-audio-visualizer-aura.ts
git commit -m "feat(session): restore LiveKit stock AgentAudioVisualizerAura (+ react-shader-toy, hook)"
```

---

### Task 5: `Aura` wrapper (stock shader + reduced-motion fallback, colorShift 2)

**Files:**
- Create: `components/agents-ui/aura.tsx`
- Test: `tests/components/agents-ui/aura.test.tsx`

- [ ] **Step 1: Write the failing test** `tests/components/agents-ui/aura.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Stub the WebGL stock aura so jsdom never touches WebGL.
const stockMock = vi.fn((props: Record<string, unknown>) => (
  <div data-testid="stock-aura" data-color-shift={String(props.colorShift)} data-theme={String(props.themeMode)} role="img" aria-label="AI interviewer" />
))
vi.mock('@/components/agents-ui/agent-audio-visualizer-aura', () => ({
  AgentAudioVisualizerAura: (props: Record<string, unknown>) => stockMock(props),
}))
const reducedMock = vi.fn(() => false)
vi.mock('@/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => reducedMock(),
}))

import { Aura } from '@/components/agents-ui/aura'

describe('Aura', () => {
  it('renders the stock shader with colorShift=2 and themeMode=light when motion is allowed', () => {
    reducedMock.mockReturnValue(false)
    render(<Aura state="speaking" audioTrack={undefined} size="xl" />)
    const el = screen.getByTestId('stock-aura')
    expect(el).toHaveAttribute('data-color-shift', '2')
    expect(el).toHaveAttribute('data-theme', 'light')
  })

  it('renders a static aura-mark fallback (no shader) under reduced motion', () => {
    reducedMock.mockReturnValue(true)
    const { container } = render(<Aura state="listening" audioTrack={undefined} size="xl" />)
    expect(screen.queryByTestId('stock-aura')).not.toBeInTheDocument()
    expect(container.querySelector('.aura-mark')).not.toBeNull()
    expect(screen.getByRole('img', { name: /interviewer/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- aura.test`
Expected: FAIL (import unresolved).

- [ ] **Step 3: Implement** `components/agents-ui/aura.tsx`:

```tsx
'use client'

import { type LocalAudioTrack, type RemoteAudioTrack } from 'livekit-client'
import { type AgentState, type TrackReferenceOrPlaceholder } from '@livekit/components-react'

import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura'
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'
import { cn } from '@/lib/utils'

/** Base hue for the shader; colorShift=2 makes it cycle through many hues. */
const AURA_COLOR = '#1FD5F9'
const AURA_COLOR_SHIFT = 2

type AuraSize = 'sm' | 'md' | 'lg' | 'xl'

const FALLBACK_SIZE: Record<AuraSize, string> = {
  sm: 'size-[120px]',
  md: 'size-[180px]',
  lg: 'size-[260px]',
  xl: 'size-[340px]',
}

export interface AuraProps {
  state?: AgentState
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  size?: AuraSize
  className?: string
}

/**
 * The AI interviewer's presence. Renders LiveKit's stock WebGL aura shader
 * (colorShift 2, light theme); under prefers-reduced-motion it renders a static
 * multi-hue gradient orb instead (no WebGL).
 */
export function Aura({ state = 'connecting', audioTrack, size = 'xl', className }: AuraProps) {
  const reduced = usePrefersReducedMotion()

  if (reduced) {
    return (
      <span
        role="img"
        aria-label="AI interviewer"
        className={cn('aura-mark block', FALLBACK_SIZE[size], className)}
      />
    )
  }

  return (
    <AgentAudioVisualizerAura
      role="img"
      aria-label="AI interviewer"
      state={state}
      audioTrack={audioTrack}
      size={size}
      color={AURA_COLOR}
      colorShift={AURA_COLOR_SHIFT}
      themeMode="light"
      className={className}
    />
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- aura.test`
Expected: PASS (2 tests).

- [ ] **Step 5: Type-check + commit**

Run: `npm run type-check` (no new errors).

```bash
git add components/agents-ui/aura.tsx tests/components/agents-ui/aura.test.tsx
git commit -m "feat(session): Aura wrapper — stock shader (colorShift 2, light) + reduced-motion fallback"
```

---

### Task 6: Swap `LiquidAura` → `Aura` / `.aura-mark`; update tests; delete `LiquidAura`

**Files:**
- Modify: `components/interview/session/AuraStage.tsx`, `components/interview/app/welcome-view.tsx`, `app/interview/[token]/WizardFrame.tsx`, `components/interview/session/InterviewSessionPanel.tsx`, `app-config.ts`
- Modify (test mocks): `tests/components/interview/session/LiveInterview.test.tsx`, `tests/components/interview/app.test.tsx`
- Delete: `components/agents-ui/liquid-aura.tsx`, `tests/components/agents-ui/liquid-aura.test.tsx`

- [ ] **Step 1: `AuraStage.tsx` — use `Aura`**

Change the import `import { LiquidAura } from '@/components/agents-ui/liquid-aura'` → `import { Aura } from '@/components/agents-ui/aura'`, and the render line `<LiquidAura state={state} audioTrack={audioTrack} size="hero" />` → `<Aura state={state} audioTrack={audioTrack} size="xl" />`.

- [ ] **Step 2: `welcome-view.tsx` — use `Aura`**

Change the import to `import { Aura } from '@/components/agents-ui/aura'`, and the line `<LiquidAura state="listening" audioTrack={undefined} size="hero" className="mb-6 size-[200px]" />` → `<Aura state="listening" audioTrack={undefined} size="xl" className="mb-6" />`.

- [ ] **Step 3: `WizardFrame.tsx` — use `Aura`**

Change the import to `import { Aura } from '@/components/agents-ui/aura'`, and the line `<LiquidAura state="listening" audioTrack={undefined} size="hero" className="size-[120px]" />` → `<Aura state="listening" audioTrack={undefined} size="md" />`.

- [ ] **Step 4: `InterviewSessionPanel.tsx` — use the `.aura-mark` CSS class**

Remove the `import { LiquidAura } from '@/components/agents-ui/liquid-aura'` line. There are two usages of `<LiquidAura state={agentState} audioTrack={undefined} size="mark" />` (each already wrapped in `<span aria-hidden="true">…</span>`). Replace EACH `<LiquidAura ... size="mark" />` with:

```tsx
<span aria-hidden className="aura-mark block size-[22px]" />
```

i.e. the existing wrapping `<span aria-hidden="true">` can be collapsed into this single span — replace the wrapper-`<span>`-plus-`<LiquidAura>` pair at both sites with the single `.aura-mark` span above. (If the existing structure is `<span aria-hidden="true"><LiquidAura .../></span>`, replace the whole pair with the single span.)

- [ ] **Step 5: `app-config.ts` — set colorShift default**

Add `audioVisualizerColorShift: 2,` to the `APP_CONFIG_DEFAULTS` object (next to `audioVisualizerType: 'aura'`). This documents the chosen value (the canonical value also lives in `aura.tsx`; keep both at `2`).

- [ ] **Step 6: Fix test mocks that now render the stock aura**

`tests/components/interview/session/LiveInterview.test.tsx` renders `AuraStage` → `Aura` → the WebGL stock aura. Add a stub mock so jsdom never loads WebGL. Add this `vi.mock` alongside the existing mocks (and you may remove the now-unused `useMultibandTrackVolume: () => [0]` line from the `@livekit/components-react` mock, since the new aura doesn't use it — but leaving it is harmless):

```tsx
vi.mock('@/components/agents-ui/agent-audio-visualizer-aura', () => ({
  AgentAudioVisualizerAura: (props: Record<string, unknown>) => (
    <div role="img" aria-label="AI interviewer" data-testid="stock-aura" />
  ),
}))
```

`tests/components/interview/app.test.tsx` renders `WelcomeView` → `Aura` → stock aura. Add the SAME `vi.mock('@/components/agents-ui/agent-audio-visualizer-aura', …)` block to that file (and the previously-added `useMultibandTrackVolume` line may be removed). Keep all other mocks.

- [ ] **Step 7: Delete `LiquidAura` + its test**

```bash
git rm components/agents-ui/liquid-aura.tsx tests/components/agents-ui/liquid-aura.test.tsx
```

Confirm nothing else references it:

```bash
grep -rn "LiquidAura\|liquid-aura" --include="*.ts" --include="*.tsx" --include="*.css" . | grep -v node_modules
```
Expected: NO matches. (The `.liquid-aura` CSS was already removed in Task 1; the `size="mark"`/`size="hero"` props are gone.)

- [ ] **Step 8: Verify everything**

Run: `npm run type-check` (only the 4 baseline errors), `npm run build` (success), `npm run test` (all pass).

- [ ] **Step 9: Commit**

```bash
git add components/interview/session/AuraStage.tsx components/interview/app/welcome-view.tsx "app/interview/[token]/WizardFrame.tsx" components/interview/session/InterviewSessionPanel.tsx app-config.ts tests/components/interview/session/LiveInterview.test.tsx tests/components/interview/app.test.tsx components/agents-ui/liquid-aura.tsx tests/components/agents-ui/liquid-aura.test.tsx
git commit -m "feat(session): swap LiquidAura for LiveKit stock Aura + aura-mark; remove LiquidAura"
```

---

## Self-review notes (verified while writing)

- **Spec (Revision b) coverage:** cool-light theme + prominent glass (Task 1), animated background reduced-motion-safe (Tasks 2-3), restore stock aura (Task 4), `Aura` wrapper with colorShift 2 + themeMode light + reduced-motion fallback (Task 5), swap all `LiquidAura` sites incl. CSS marks + app-config colorShift + delete LiquidAura (Task 6). Layout/wiring/voice-only/panel/wizard/terminal/End-wiring untouched (not in any task).
- **Type/name consistency:** `Aura` props (`state`, `audioTrack`, `size: 'sm'|'md'|'lg'|'xl'`, `className`) match all call sites (xl/xl/md). Stock aura props (`size`, `state`, `color`, `colorShift`, `themeMode`, `audioTrack`, `...props`) match the LiveKit docs. `usePrefersReducedMotion` used by `Aura`. `.aura-mark` defined in Task 1, used by `Aura` fallback + the panel.
- **No placeholders:** every step has concrete code/commands.
- **jsdom safety:** matchMedia polyfill (Task 2) + stock-aura mock in the two tests that transitively render it (Task 6) prevent WebGL/matchMedia crashes.
- **Constraints:** no Supabase; no change to `proxy.ts`/`next.config.ts`/`lib/api/candidate-session.ts`/`lib/env.ts`; stock aura lazy by virtue of living on the live/wizard surfaces (not the bare landing); reduced-motion honored for both background and aura.

## Notes
- The retired dark theme + `LiquidAura` are gone after this plan. The `warm-light` theme block remains in `globals.css` (unused, harmless).
- Only ONE WebGL aura renders at a time (live hero OR welcome OR wizard — different routes/states); the panel marks are pure CSS. No multi-canvas perf concern.
