# Candidate Pre-Check Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-click candidate pre-check wizard with a fast, welcoming, 2-stage flow (Intro → Ready, OTP only when required) on BinQle.ai branding, with a futuristic scale/blur/fade transition — preserving consent + OTP + single-use semantics exactly.

**Architecture:** Pure front-end change in `frontend/session`. `WizardShell` derives a `stage` from the cached `/pre-check` state and renders the active stage inside a `motion`/`AnimatePresence` transition. Consent is soft-folded into the Intro "I'm ready" CTA (reusing the existing `useConsent` cache-flip hook). The Ready stage's single "Start" mounts `<App mode="start" autoStart>`, which connects immediately — removing the redundant in-session `WelcomeView`. No backend, API, or dependency changes.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript strict, Tailwind v4 (`--px-*` tokens), `motion` v12 (`motion/react`), `radix-ui` Dialog, `lucide-react`, TanStack Query v5, Vitest + Testing Library.

---

## Context the implementer must know

- **Surface rules** (`frontend/session/CLAUDE.md`, `AGENTS.md`): NO `@supabase/*`, no new analytics, the candidate JWT (`token`) must never be logged/stored, CSP (`proxy.ts`) + headers (`next.config.ts`) unchanged, audio constraints come from `/start` (`audio_processing_hints`) — never hard-coded, accessibility on every step.
- **Consent/OTP cache mechanism (reuse verbatim):** `lib/hooks/use-consent.ts` `setQueryData`-flips the cached `/pre-check` `state → 'consented'` synchronously then invalidates; `lib/hooks/use-verify-otp.ts` stamps `otp_verified_at` the same way. This is deliberate (avoids a subscriber-notify race that stranded the old wizard). Do NOT reimplement state advancement — let `WizardShell` re-derive from cache.
- **`PreCheckResponse` fields** (`lib/api/candidate-session.ts`): `session_id`, `company_name`, `job_title`, `duration_minutes`, `consent_text`, `state`, `otp_required`, `otp_verified_at: string | null`, `otp_issued_at: string | null`, `proctoring_enabled`, `proctoring_outcome: string | null`.
- **Design tokens / classes** (`app/globals.css`): semantic CSS vars `--px-fg`, `--px-fg-2..5`, `--px-surface`, `--px-hairline`, `--px-accent`, `--px-ok`, `--px-caution`, `--px-danger` (+ `-bg`/`-line` variants), plus Tailwind utilities mapped from them (`text-px-fg`, `bg-px-surface`, `border-px-hairline`, etc.). Helper classes: `.px-glass` (glass card), `.px-cine-bg` (transparent — the site-wide `AnimatedBackground` shows through), `.px-serif` / `font-serif` (Fraunces), `.px-btn` (via `<Button>`). **Never use raw hex in components** — only tokens.
- **Reduced motion:** use the existing hook `@/hooks/use-prefers-reduced-motion` (`usePrefersReducedMotion(): boolean`).
- **Buttons:** `import { Button } from '@/components/px'` — `variant` (`primary|outline|ghost|secondary|destructive|link`), `size` (`lg|default|sm|...`). Renders `<button>`.
- **Test harness:** `tests/_utils/render.tsx` exports `renderWithProviders` (wraps a fresh `QueryClient`). Use it for anything touching TanStack hooks. `tests/setup.ts` polyfills `matchMedia` + `navigator.mediaDevices.getUserMedia`. Stub the Aura WebGL via `vi.mock('@/components/agents-ui/aura', ...)`.
- **Run all session commands from `frontend/session/`.** Test a single file: `npm run test -- <path>`. Full suite: `npm run test`. Lint: `npm run lint`. Types: `npm run type-check`. Build: `npm run build`.

## File structure (locked decomposition)

```
frontend/session/
├── lib/brand.ts                                   ← NEW: minimal BinQle brand config
├── public/brand/binqle-mark.png|binqle-wordmark.png ← NEW: copied from frontend/app
├── components/interview/BrandMark.tsx             ← NEW: BinQle logo component
├── app/interview/[token]/
│   ├── WizardShell.tsx                            ← REWRITE: stage derivation + transition
│   ├── WizardFrame.tsx                            ← REWRITE: split layout + BrandMark + StageProgress
│   ├── StageTransition.tsx                        ← NEW: motion/AnimatePresence wrapper
│   ├── StageProgress.tsx                          ← NEW: minimal step indicator
│   ├── IntroStage.tsx                             ← NEW: replaces WelcomeStep + ConsentStep
│   ├── VerifyStage.tsx                            ← NEW: replaces OtpStep (logic ported)
│   ├── ReadyStage.tsx                             ← NEW: replaces CameraMicStep (Start CTA)
│   ├── InstructionList.tsx                        ← NEW: interactive instruction list
│   ├── ConsentDialog.tsx                          ← NEW: radix dialog for full consent text
│   ├── illustrations/HeroScene.tsx               ← NEW: hero SVG (Arjun orb + calm setup)
│   ├── illustrations/glyphs.tsx                  ← NEW: per-instruction SVG glyphs
│   ├── sampleNoiseFloorDbfs.ts                    ← KEEP (reused by ReadyStage)
│   └── (DELETE) WelcomeStep/ConsentStep/OtpStep/CameraMicStep/WizardStepper.tsx
├── app-config.ts                                  ← MODIFY: BinQle defaults
└── components/interview/app/
    ├── app.tsx                                    ← MODIFY: autoStart prop
    ├── view-controller.tsx                        ← MODIFY: connecting view on autoStart
    └── ConnectingView.tsx                         ← NEW: branded "connecting" screen
```

---

## Task 1: BinQle branding — assets, config, BrandMark, app-config

**Files:**
- Create: `frontend/session/public/brand/binqle-mark.png`, `frontend/session/public/brand/binqle-wordmark.png` (copied)
- Create: `frontend/session/lib/brand.ts`
- Create: `frontend/session/components/interview/BrandMark.tsx`
- Modify: `frontend/session/app-config.ts`
- Test: `frontend/session/tests/components/interview/BrandMark.test.tsx`

- [ ] **Step 1: Copy brand assets**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
mkdir -p public/brand
cp ../app/public/brand/binqle-mark.png public/brand/binqle-mark.png
cp ../app/public/brand/binqle-wordmark.png public/brand/binqle-wordmark.png
ls -la public/brand
```
Expected: both PNGs present.

- [ ] **Step 2: Create `lib/brand.ts`**

```ts
// lib/brand.ts
// Minimal product-identity config for the candidate surface. The recruiter app
// (frontend/app/lib/brand.ts) is the fuller source; this is the candidate-side
// subset (name + logo only — no theme/density config needed here). Keep the
// logo asset shape compatible if the two are ever reconciled.

export interface LogoAsset {
  /** Path under public/ */
  src: string
  /** Intrinsic pixel dimensions — required by next/image. */
  width: number
  height: number
}

export interface SessionBrand {
  /** Full product name — used as logo alt text + prose. */
  name: string
  /** Compact name for tight inline contexts. */
  shortName: string
  logo: { wordmark: LogoAsset; mark: LogoAsset }
}

export const brand: SessionBrand = {
  name: 'BinQle.ai',
  shortName: 'BinQle',
  logo: {
    wordmark: { src: '/brand/binqle-wordmark.png', width: 960, height: 263 },
    mark: { src: '/brand/binqle-mark.png', width: 256, height: 256 },
  },
}
```

- [ ] **Step 3: Write the failing test**

```tsx
// tests/components/interview/BrandMark.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandMark } from '@/components/interview/BrandMark'

describe('BrandMark', () => {
  it('renders the BinQle mark with accessible alt text', () => {
    render(<BrandMark variant="mark" />)
    const img = screen.getByRole('img', { name: /binqle\.ai/i })
    expect(img).toHaveAttribute('src', expect.stringContaining('binqle-mark'))
  })

  it('renders the wordmark variant', () => {
    render(<BrandMark variant="wordmark" />)
    const img = screen.getByRole('img', { name: /binqle\.ai/i })
    expect(img).toHaveAttribute('src', expect.stringContaining('binqle-wordmark'))
  })
})
```

- [ ] **Step 4: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/BrandMark.test.tsx`
Expected: FAIL — `Cannot find module '@/components/interview/BrandMark'`.

- [ ] **Step 5: Create `BrandMark.tsx`**

```tsx
// components/interview/BrandMark.tsx
'use client'

import Image from 'next/image'

import { brand } from '@/lib/brand'
import { cn } from '@/lib/utils'

interface Props {
  variant?: 'mark' | 'wordmark'
  className?: string
  priority?: boolean
}

/**
 * BinQle.ai platform logo. `mark` is the square glyph (header chip); `wordmark`
 * is the full lockup. Alt text is always the product name for screen readers.
 */
export function BrandMark({ variant = 'mark', className, priority = true }: Props) {
  const asset = brand.logo[variant]
  return (
    <Image
      src={asset.src}
      alt={brand.name}
      width={asset.width}
      height={asset.height}
      priority={priority}
      className={cn(
        variant === 'mark' ? 'h-7 w-7 rounded-[7px]' : 'h-7 w-auto',
        'select-none',
        className,
      )}
    />
  )
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `npm run test -- tests/components/interview/BrandMark.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 7: Update `app-config.ts` BinQle defaults**

In `frontend/session/app-config.ts`, change the `APP_CONFIG_DEFAULTS` object fields:
- `pageTitle: 'ProjectX · Interview'` → `pageTitle: 'BinQle.ai · Interview'`
- `logo: '/projectx-logo.svg'` → `logo: '/brand/binqle-mark.png'`

Leave `companyName: 'ProjectX'` as the fallback only for the rare null-data case, but change it to `'BinQle.ai'`:
- `companyName: 'ProjectX'` → `companyName: 'BinQle.ai'`

(Do NOT change `accent`, visualizer, or other fields.)

- [ ] **Step 8: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/public/brand frontend/session/lib/brand.ts \
  frontend/session/components/interview/BrandMark.tsx frontend/session/app-config.ts \
  frontend/session/tests/components/interview/BrandMark.test.tsx
git commit -m "feat(session): add BinQle.ai brand config + BrandMark; retire ProjectX defaults"
```

---

## Task 2: Hand-crafted SVG illustrations (hero scene + instruction glyphs)

**Files:**
- Create: `frontend/session/app/interview/[token]/illustrations/glyphs.tsx`
- Create: `frontend/session/app/interview/[token]/illustrations/HeroScene.tsx`
- Test: `frontend/session/tests/components/interview/illustrations.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/illustrations.test.tsx
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HeroScene } from '@/app/interview/[token]/illustrations/HeroScene'
import {
  ArjunGlyph,
  QuietRoomGlyph,
  SingleScreenGlyph,
  ShieldGlyph,
  OneTimeLinkGlyph,
} from '@/app/interview/[token]/illustrations/glyphs'

describe('illustrations', () => {
  it('renders the hero scene as a decorative svg', () => {
    const { container } = render(<HeroScene />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg).toHaveAttribute('aria-hidden', 'true')
  })

  it.each([
    ['arjun', ArjunGlyph],
    ['quiet', QuietRoomGlyph],
    ['screen', SingleScreenGlyph],
    ['shield', ShieldGlyph],
    ['link', OneTimeLinkGlyph],
  ])('renders the %s glyph svg', (_name, Glyph) => {
    const { container } = render(<Glyph />)
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/illustrations.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `illustrations/glyphs.tsx`**

```tsx
// app/interview/[token]/illustrations/glyphs.tsx
// Bespoke line glyphs for the instruction list. All use currentColor + a
// consistent 1.5 stroke so they inherit the row's token color and stay crisp at
// any size. Decorative — the row's text label is the accessible name.
import type { SVGProps } from 'react'

const base: SVGProps<SVGSVGElement> = {
  width: 24,
  height: 24,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

/** Arjun — a friendly AI orb with a soft spark. */
export function ArjunGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="6.5" />
      <circle cx="12" cy="12" r="2.25" fill="currentColor" stroke="none" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2" opacity="0.55" />
    </svg>
  )
}

/** Quiet room — a person silhouette with a hush wave. */
export function QuietRoomGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <path d="M17 8c1.4 1.2 1.4 6.8 0 8M19.5 6c2.4 2 2.4 8 0 12" opacity="0.55" />
    </svg>
  )
}

/** Single screen — one monitor, a second crossed out. */
export function SingleScreenGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="4.5" width="12" height="9" rx="1.5" />
      <path d="M9 13.5v3M6.5 16.5h5" />
      <path d="M18 7l4 4M22 7l-4 4" opacity="0.7" />
    </svg>
  )
}

/** Proctored — a shield with a check. */
export function ShieldGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M12 2.5l7 2.5v6c0 4.2-2.9 7.4-7 9-4.1-1.6-7-4.8-7-9V5z" />
      <path d="M9 11.5l2.2 2.2L15.5 9.4" />
    </svg>
  )
}

/** One-time link — a chain link with a small clock/expiry hint. */
export function OneTimeLinkGlyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M9.5 14.5l5-5" />
      <path d="M7 12l-1.5 1.5a3 3 0 0 0 4.25 4.25L11 16.5" />
      <path d="M17 12l1.5-1.5a3 3 0 0 0-4.25-4.25L13 7.5" />
    </svg>
  )
}
```

- [ ] **Step 4: Create `illustrations/HeroScene.tsx`**

```tsx
// app/interview/[token]/illustrations/HeroScene.tsx
'use client'

// Calm, on-brand hero: a glowing "Arjun" orb above a soft desk horizon with a
// few drifting particles. Token-colored via the inherited --px-accent. Motion is
// CSS-only and reduced-motion-safe (the keyframes are gated in globals.css; see
// Task 9 note). Decorative — aria-hidden.
import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

export function HeroScene({ className }: { className?: string }) {
  const reduced = usePrefersReducedMotion()
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 320 320"
      className={className}
      role="presentation"
    >
      <defs>
        <radialGradient id="orbGlow" cx="50%" cy="42%" r="55%">
          <stop offset="0%" stopColor="var(--px-accent)" stopOpacity="0.9" />
          <stop offset="55%" stopColor="var(--px-accent)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--px-accent)" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="deskFade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--px-accent)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--px-accent)" stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* glow halo */}
      <circle cx="160" cy="140" r="120" fill="url(#orbGlow)" />

      {/* orb */}
      <g className={reduced ? undefined : 'hero-orb'}>
        <circle cx="160" cy="140" r="46" fill="var(--px-surface)" stroke="var(--px-accent)" strokeWidth="2" />
        <circle cx="160" cy="140" r="14" fill="var(--px-accent)" />
        <circle cx="160" cy="140" r="70" fill="none" stroke="var(--px-accent)" strokeOpacity="0.25" strokeWidth="1.5" />
      </g>

      {/* desk horizon */}
      <rect x="40" y="232" width="240" height="60" rx="10" fill="url(#deskFade)" />
      <line x1="40" y1="232" x2="280" y2="232" stroke="var(--px-hairline-strong)" strokeWidth="1.5" />

      {/* drifting particles */}
      {[
        { cx: 96, cy: 96, r: 3 },
        { cx: 232, cy: 110, r: 2.5 },
        { cx: 210, cy: 70, r: 2 },
        { cx: 110, cy: 190, r: 2 },
      ].map((p, i) => (
        <circle
          key={i}
          cx={p.cx}
          cy={p.cy}
          r={p.r}
          fill="var(--px-accent)"
          opacity="0.5"
          className={reduced ? undefined : `hero-particle hero-particle-${i}`}
        />
      ))}
    </svg>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/illustrations.test.tsx`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/illustrations \
  frontend/session/tests/components/interview/illustrations.test.tsx
git commit -m "feat(session): hand-crafted SVG hero scene + instruction glyphs"
```

---

## Task 3: InstructionList (interactive, accessible, progressive disclosure)

**Files:**
- Create: `frontend/session/app/interview/[token]/InstructionList.tsx`
- Test: `frontend/session/tests/components/interview/instruction-list.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/instruction-list.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { InstructionList, type Instruction } from '@/app/interview/[token]/InstructionList'

function Glyph() {
  return <svg data-testid="glyph" />
}

const items: Instruction[] = [
  { id: 'a', Icon: Glyph, title: 'Meet Arjun', detail: 'Led by a friendly AI.' },
  { id: 'b', Icon: Glyph, title: 'One-time link', detail: 'Used up once you start.', tone: 'caution' },
]

describe('InstructionList', () => {
  it('renders every instruction title', () => {
    render(<InstructionList items={items} />)
    expect(screen.getByText('Meet Arjun')).toBeInTheDocument()
    expect(screen.getByText('One-time link')).toBeInTheDocument()
  })

  it('toggles a row open via aria-expanded on click', async () => {
    const user = userEvent.setup()
    render(<InstructionList items={items} />)
    const row = screen.getByRole('button', { name: /meet arjun/i })
    expect(row).toHaveAttribute('aria-expanded', 'false')
    await user.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')
  })

  it('marks a caution row with data-tone', () => {
    render(<InstructionList items={items} />)
    const row = screen.getByRole('button', { name: /one-time link/i })
    expect(row).toHaveAttribute('data-tone', 'caution')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/instruction-list.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `InstructionList.tsx`**

```tsx
// app/interview/[token]/InstructionList.tsx
'use client'

import { useState, type ComponentType, type SVGProps } from 'react'
import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

export interface Instruction {
  id: string
  Icon: ComponentType<SVGProps<SVGSVGElement>>
  title: string
  detail: string
  /** 'caution' gets a distinct (reassuring, not alarming) accent. */
  tone?: 'default' | 'caution'
}

/**
 * Interactive instruction list. Each row is a button that expands a one-line
 * "why" (progressive disclosure). The detail stays in the DOM and collapses via
 * a grid-rows 0fr→1fr transition — no JS height measurement, no layout-thrash.
 */
export function InstructionList({ items }: { items: Instruction[] }) {
  return (
    <ul className="flex flex-col gap-1.5" aria-label="What to know before you start">
      {items.map((item) => (
        <InstructionRow key={item.id} item={item} />
      ))}
    </ul>
  )
}

function InstructionRow({ item }: { item: Instruction }) {
  const [open, setOpen] = useState(false)
  const tone = item.tone ?? 'default'
  const { Icon } = item
  return (
    <li>
      <button
        type="button"
        data-tone={tone}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'group flex w-full items-center gap-3 rounded-[12px] border px-3.5 py-3 text-left',
          'min-h-[44px] transition-colors duration-200',
          'border-px-hairline bg-px-surface/60 hover:bg-px-surface',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-px-accent',
        )}
      >
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-[9px]',
            tone === 'caution'
              ? 'bg-[var(--px-caution-bg)] text-px-caution'
              : 'bg-[var(--px-accent-tint)] text-px-accent',
          )}
        >
          <Icon />
        </span>
        <span className="flex-1 text-[15px] font-medium text-px-fg">{item.title}</span>
        <ChevronDown
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-px-fg-4 transition-transform duration-200',
            open && 'rotate-180',
          )}
        />
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <p className="px-3.5 pt-1.5 pb-1 pl-[60px] text-[13.5px] leading-relaxed text-px-fg-3">
            {item.detail}
          </p>
        </div>
      </div>
    </li>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/instruction-list.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/InstructionList.tsx \
  frontend/session/tests/components/interview/instruction-list.test.tsx
git commit -m "feat(session): interactive instruction list with progressive disclosure"
```

---

## Task 4: ConsentDialog (full consent text, accessible modal)

**Files:**
- Create: `frontend/session/app/interview/[token]/ConsentDialog.tsx`
- Test: `frontend/session/tests/components/interview/consent-dialog.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/consent-dialog.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConsentDialog } from '@/app/interview/[token]/ConsentDialog'

describe('ConsentDialog', () => {
  it('opens the full consent text on trigger click', async () => {
    const user = userEvent.setup()
    render(<ConsentDialog consentText="You consent to recording and AI evaluation." />)
    expect(screen.queryByText(/consent to recording/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /privacy & consent/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/consent to recording/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/consent-dialog.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `ConsentDialog.tsx`**

```tsx
// app/interview/[token]/ConsentDialog.tsx
'use client'

import { Dialog } from 'radix-ui'
import { X } from 'lucide-react'

/**
 * Read-only disclosure of the full AIVIA consent text. Opening this dialog does
 * NOT record consent — only the Intro "I'm ready" CTA does that. radix provides
 * focus-trap + Esc-to-close for accessibility.
 */
export function ConsentDialog({ consentText }: { consentText: string }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="text-[13px] font-medium text-px-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-px-accent"
        >
          Privacy &amp; consent
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-sm" />
        <Dialog.Content className="px-glass fixed left-1/2 top-1/2 z-50 max-h-[80vh] w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl p-6">
          <div className="mb-3 flex items-start justify-between gap-4">
            <Dialog.Title className="px-serif text-[20px] font-normal text-px-fg">
              Privacy &amp; consent
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="grid size-8 place-items-center rounded-full text-px-fg-3 hover:bg-px-surface-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-px-accent"
              >
                <X className="size-4" aria-hidden />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="sr-only">
            Full consent and privacy terms for this AI interview.
          </Dialog.Description>
          <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-px-fg-2">
            {consentText}
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/interview/consent-dialog.test.tsx`
Expected: PASS. (If radix's portal needs it, the harness `render` is sufficient — radix renders to `document.body`.)

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/ConsentDialog.tsx \
  frontend/session/tests/components/interview/consent-dialog.test.tsx
git commit -m "feat(session): accessible consent disclosure dialog"
```

---

## Task 5: StageTransition + StageProgress

**Files:**
- Create: `frontend/session/app/interview/[token]/StageTransition.tsx`
- Create: `frontend/session/app/interview/[token]/StageProgress.tsx`
- Test: `frontend/session/tests/components/interview/stage-transition.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/stage-transition.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StageTransition } from '@/app/interview/[token]/StageTransition'
import { StageProgress } from '@/app/interview/[token]/StageProgress'

describe('StageTransition', () => {
  it('renders the active stage children', () => {
    render(
      <StageTransition stageKey="intro">
        <div>intro body</div>
      </StageTransition>,
    )
    expect(screen.getByText('intro body')).toBeInTheDocument()
  })
})

describe('StageProgress', () => {
  it('shows step position and total', () => {
    render(<StageProgress steps={['Welcome', 'Verify', 'Ready']} currentIndex={1} />)
    expect(screen.getByText(/step 2 of 3/i)).toBeInTheDocument()
  })

  it('marks the active dot with aria-current', () => {
    render(<StageProgress steps={['Welcome', 'Ready']} currentIndex={1} />)
    const current = screen.getByText('Ready').closest('li')
    expect(current).toHaveAttribute('aria-current', 'step')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/stage-transition.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `StageTransition.tsx`**

```tsx
// app/interview/[token]/StageTransition.tsx
'use client'

import type { ReactNode } from 'react'
import { AnimatePresence, motion, type Variants } from 'motion/react'

import { usePrefersReducedMotion } from '@/hooks/use-prefers-reduced-motion'

// Full motion: incoming scales up + de-blurs + fades in; outgoing scales down +
// blurs + fades out, drifting up. Exit is shorter than enter (responsive feel).
// Transform/opacity/filter only — no layout reflow.
const FULL: Variants = {
  enter: { opacity: 0, scale: 1.04, filter: 'blur(8px)', y: 12 },
  center: {
    opacity: 1,
    scale: 1,
    filter: 'blur(0px)',
    y: 0,
    transition: { type: 'spring', stiffness: 240, damping: 28, opacity: { duration: 0.4 } },
  },
  exit: {
    opacity: 0,
    scale: 0.94,
    filter: 'blur(6px)',
    y: -12,
    transition: { duration: 0.28, ease: 'easeIn' },
  },
}

const REDUCED: Variants = {
  enter: { opacity: 0 },
  center: { opacity: 1, transition: { duration: 0.15 } },
  exit: { opacity: 0, transition: { duration: 0.1 } },
}

export function StageTransition({
  stageKey,
  children,
}: {
  stageKey: string
  children: ReactNode
}) {
  const reduced = usePrefersReducedMotion()
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={stageKey}
        variants={reduced ? REDUCED : FULL}
        initial="enter"
        animate="center"
        exit="exit"
        className="w-full"
      >
        {children}
      </motion.div>
    </AnimatePresence>
  )
}
```

- [ ] **Step 4: Create `StageProgress.tsx`**

```tsx
// app/interview/[token]/StageProgress.tsx
'use client'

import { cn } from '@/lib/utils'

/**
 * Minimal multi-step indicator (dots + "Step N of M"). Honors the UX rule to
 * show progress in multi-step flows without the heavier numbered stepper.
 */
export function StageProgress({
  steps,
  currentIndex,
  className,
}: {
  steps: string[]
  currentIndex: number
  className?: string
}) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <ol className="flex items-center gap-1.5" aria-label="Setup progress">
        {steps.map((label, i) => {
          const active = i === currentIndex
          const done = i < currentIndex
          return (
            <li
              key={label}
              aria-current={active ? 'step' : undefined}
              className="flex items-center gap-1.5"
            >
              <span
                className={cn(
                  'h-1.5 rounded-full transition-all duration-300',
                  active ? 'w-6 bg-px-accent' : done ? 'w-1.5 bg-px-accent' : 'w-1.5 bg-px-surface-3',
                )}
              />
              <span className="sr-only">{label}</span>
            </li>
          )
        })}
      </ol>
      <span className="text-[11px] font-medium text-px-fg-4">
        Step {currentIndex + 1} of {steps.length}
      </span>
    </div>
  )
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/stage-transition.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/StageTransition.tsx \
  frontend/session/app/interview/\[token\]/StageProgress.tsx \
  frontend/session/tests/components/interview/stage-transition.test.tsx
git commit -m "feat(session): stage transition (scale/blur/fade) + minimal progress indicator"
```

---

## Task 6: IntroStage (replaces WelcomeStep + ConsentStep, consent soft-fold)

**Files:**
- Create: `frontend/session/app/interview/[token]/IntroStage.tsx`
- Test: `frontend/session/tests/components/interview/intro-stage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/intro-stage.test.tsx
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { IntroStage } from '@/app/interview/[token]/IntroStage'
import { candidateSessionApi } from '@/lib/api/candidate-session'

const baseProps = {
  token: 'tok',
  companyName: 'Acme',
  jobTitle: 'Backend Engineer',
  durationMinutes: 20,
  consentText: 'You consent to recording.',
  proctoringEnabled: true,
}

afterEach(() => vi.restoreAllMocks())

describe('IntroStage', () => {
  it('shows the screening title, duration, and the one-time-link warning', () => {
    renderWithProviders(<IntroStage {...baseProps} />)
    expect(screen.getByRole('heading', { name: /backend engineer/i })).toBeInTheDocument()
    expect(screen.getByText(/20 min/i)).toBeInTheDocument()
    expect(screen.getByText(/one-time link/i)).toBeInTheDocument()
    expect(screen.getByText(/meet arjun/i)).toBeInTheDocument()
  })

  it('fires POST /consent exactly once when "I\'m ready" is clicked', async () => {
    const user = userEvent.setup()
    const spy = vi.spyOn(candidateSessionApi, 'consent').mockResolvedValue(undefined)
    renderWithProviders(<IntroStage {...baseProps} />)
    await user.click(screen.getByRole('button', { name: /i'm ready/i }))
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))
    expect(spy).toHaveBeenCalledWith('tok', expect.objectContaining({ consented: true }))
  })

  it('keeps the CTA enabled again after a consent error (no advance)', async () => {
    const user = userEvent.setup()
    vi.spyOn(candidateSessionApi, 'consent').mockRejectedValue(new Error('network'))
    renderWithProviders(<IntroStage {...baseProps} />)
    const cta = screen.getByRole('button', { name: /i'm ready/i })
    await user.click(cta)
    await waitFor(() => expect(cta).toBeEnabled())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/intro-stage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `IntroStage.tsx`**

```tsx
// app/interview/[token]/IntroStage.tsx
'use client'

import { toast } from 'sonner'

import { Button } from '@/components/px'
import { useConsent } from '@/lib/hooks/use-consent'
import { ConsentDialog } from './ConsentDialog'
import { HeroScene } from './illustrations/HeroScene'
import {
  ArjunGlyph,
  QuietRoomGlyph,
  SingleScreenGlyph,
  ShieldGlyph,
  OneTimeLinkGlyph,
} from './illustrations/glyphs'
import { InstructionList, type Instruction } from './InstructionList'

interface Props {
  token: string
  companyName: string
  jobTitle: string
  durationMinutes: number
  consentText: string
  proctoringEnabled: boolean
}

function buildInstructions(proctoringEnabled: boolean): Instruction[] {
  const items: Instruction[] = [
    {
      id: 'arjun',
      Icon: ArjunGlyph,
      title: 'Meet Arjun, your AI interviewer',
      detail: 'Your screening is led by Arjun, a friendly AI. Just talk naturally — there are no trick questions.',
    },
    {
      id: 'quiet',
      Icon: QuietRoomGlyph,
      title: 'Find a quiet spot, alone',
      detail: 'Pick a calm room with no one else around. Background noise and other voices can disrupt the conversation.',
    },
    {
      id: 'screen',
      Icon: SingleScreenGlyph,
      title: 'Use a single screen',
      detail: 'Extra monitors aren’t allowed during the screening and are flagged for review.',
    },
  ]
  if (proctoringEnabled) {
    items.push({
      id: 'shield',
      Icon: ShieldGlyph,
      title: 'This is a proctored screening',
      detail: 'Your camera and focus are monitored to keep things fair. It’s for review only — never an automatic rejection.',
    })
  }
  items.push({
    id: 'link',
    Icon: OneTimeLinkGlyph,
    title: 'Your link is one-time',
    detail:
      'Not ready yet? You can revisit this page anytime. But once you start, this link is used up — you’ll need a fresh one from the recruiter to come back.',
    tone: 'caution',
  })
  return items
}

/**
 * Stage 1 — welcome + instructions + consent soft-fold. Clicking "I'm ready"
 * records the AIVIA consent event (POST /consent via useConsent). On success the
 * cached pre-check state flips to 'consented' and WizardShell advances; on error
 * the CTA re-enables and a toast explains. The full consent text is always one
 * tap away via ConsentDialog.
 */
export function IntroStage({
  token,
  companyName,
  jobTitle,
  durationMinutes,
  consentText,
  proctoringEnabled,
}: Props) {
  const consent = useConsent(token)
  const instructions = buildInstructions(proctoringEnabled)

  const onReady = () => {
    consent.mutate(
      { consented: true, user_agent: navigator.userAgent },
      { onError: (err) => toast.error(err.message) },
    )
  }

  return (
    <div className="grid items-center gap-10 lg:grid-cols-[0.9fr_1.1fr]">
      {/* Illustration side */}
      <div className="order-1 hidden justify-center lg:flex">
        <HeroScene className="w-full max-w-[360px]" />
      </div>

      {/* Content side */}
      <section className="order-2 flex flex-col">
        {/* compact hero on mobile */}
        <HeroScene className="mx-auto mb-4 block w-40 max-w-full lg:hidden" />

        <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
          {companyName} · Screening
        </p>
        <h1 className="px-serif mt-1.5 text-[clamp(28px,6vw,40px)] font-normal leading-[1.08] tracking-[-0.5px] text-px-fg">
          {jobTitle}
        </h1>
        <div className="mt-3 inline-flex w-fit items-center gap-2 rounded-full border border-px-hairline bg-px-surface/60 px-3 py-1 text-[12.5px] font-medium text-px-fg-2">
          <span className="size-1.5 rounded-full bg-px-accent" aria-hidden />
          AI screening with Arjun · ~{durationMinutes} min
        </div>

        <div className="mt-6">
          <InstructionList items={instructions} />
        </div>

        <div className="mt-7 flex flex-col gap-3">
          <Button
            size="lg"
            onClick={onReady}
            disabled={consent.isPending}
            aria-busy={consent.isPending}
            className="w-full sm:w-auto"
          >
            {consent.isPending ? 'Getting ready…' : 'I’m ready →'}
          </Button>
          <p className="text-[12.5px] leading-relaxed text-px-fg-3">
            By starting, you consent to this AI-led interview and its recording.{' '}
            <ConsentDialog consentText={consentText} />
          </p>
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/intro-stage.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/IntroStage.tsx \
  frontend/session/tests/components/interview/intro-stage.test.tsx
git commit -m "feat(session): IntroStage — welcome + instructions + consent soft-fold"
```

---

## Task 7: VerifyStage (port OtpStep into the new shell)

**Files:**
- Create: `frontend/session/app/interview/[token]/VerifyStage.tsx`
- Test: `frontend/session/tests/components/interview/verify-stage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/verify-stage.test.tsx
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { VerifyStage } from '@/app/interview/[token]/VerifyStage'
import { candidateSessionApi } from '@/lib/api/candidate-session'

afterEach(() => vi.restoreAllMocks())

describe('VerifyStage', () => {
  it('requests a code then verifies the 6-digit input', async () => {
    const user = userEvent.setup()
    const reqSpy = vi.spyOn(candidateSessionApi, 'requestOtp').mockResolvedValue(undefined)
    const verSpy = vi.spyOn(candidateSessionApi, 'verifyOtp').mockResolvedValue(undefined)
    renderWithProviders(<VerifyStage token="tok" otpIssuedAt={null} />)

    await user.click(screen.getByRole('button', { name: /send code/i }))
    await waitFor(() => expect(reqSpy).toHaveBeenCalledTimes(1))

    await user.type(screen.getByPlaceholderText('123456'), '123456')
    await user.click(screen.getByRole('button', { name: /^verify$/i }))
    await waitFor(() => expect(verSpy).toHaveBeenCalledWith('tok', { code: '123456' }))
  })

  it('disables Verify until 6 digits are entered', async () => {
    const user = userEvent.setup()
    renderWithProviders(<VerifyStage token="tok" otpIssuedAt={null} />)
    const verify = screen.getByRole('button', { name: /^verify$/i })
    expect(verify).toBeDisabled()
    await user.type(screen.getByPlaceholderText('123456'), '12345')
    expect(verify).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/verify-stage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `VerifyStage.tsx`**

Port the existing `OtpStep.tsx` logic verbatim (cooldown from `otp_issued_at`, attempts-remaining `aria-live`, numeric input), re-themed into the new stage shell. Full code:

```tsx
// app/interview/[token]/VerifyStage.tsx
'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button, Input } from '@/components/px'
import type { CandidateSessionError } from '@/lib/api/candidate-session'
import { useRequestOtp } from '@/lib/hooks/use-request-otp'
import { useVerifyOtp } from '@/lib/hooks/use-verify-otp'

interface Props {
  token: string
  /** Last OTP issuance from /pre-check — restores the 60s cooldown on reload. */
  otpIssuedAt: string | null
}

function asCandidateError(err: Error): CandidateSessionError | null {
  if (err && typeof err === 'object' && 'status' in err) {
    return err as CandidateSessionError
  }
  return null
}

function initialCooldown(otpIssuedAt: string | null): number {
  if (!otpIssuedAt) return 0
  const elapsed = Math.floor((Date.now() - new Date(otpIssuedAt).getTime()) / 1000)
  return Math.max(0, 60 - elapsed)
}

export function VerifyStage({ token, otpIssuedAt }: Props) {
  const [code, setCode] = useState('')
  const [cooldown, setCooldown] = useState(() => initialCooldown(otpIssuedAt))
  const [attemptsRemaining, setAttemptsRemaining] = useState<number | null>(null)
  const requestOtp = useRequestOtp(token)
  const verifyOtp = useVerifyOtp(token)

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setInterval(() => setCooldown((n) => Math.max(0, n - 1)), 1000)
    return () => clearInterval(timer)
  }, [cooldown])

  const onSendCode = () => {
    requestOtp.mutate(undefined, {
      onSuccess: () => {
        toast.success('Code sent to your email')
        setCooldown(60)
        setAttemptsRemaining(null)
      },
      onError: (err) => {
        const ce = asCandidateError(err)
        if (ce?.retry_after_seconds) setCooldown(ce.retry_after_seconds)
        toast.error(err.message)
      },
    })
  }

  const onVerify = () => {
    verifyOtp.mutate(
      { code },
      {
        onSuccess: () => {
          toast.success('Verified')
          setAttemptsRemaining(null)
        },
        onError: (err) => {
          const ce = asCandidateError(err)
          if (ce && typeof ce.attempts_remaining === 'number') {
            setAttemptsRemaining(ce.attempts_remaining)
          }
          toast.error(err.message)
        },
      },
    )
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
        Verify identity
      </p>
      <h1 className="px-serif mt-1.5 text-[clamp(24px,5vw,30px)] font-normal tracking-[-0.4px] text-px-fg">
        Enter your access code
      </h1>
      <p className="mt-2 text-[14.5px] leading-relaxed text-px-fg-2">
        Tap <strong>Send code</strong> to get a 6-digit code by email. It’s valid for 10 minutes.
      </p>

      <div className="mt-5 flex items-center gap-3">
        <Button variant="outline" onClick={onSendCode} disabled={cooldown > 0 || requestOtp.isPending}>
          {cooldown > 0 ? `Resend in ${cooldown}s` : 'Send code'}
        </Button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Input
          type="text"
          inputMode="numeric"
          pattern="\d*"
          maxLength={6}
          placeholder="123456"
          aria-label="6-digit access code"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          className="px-input mono w-40 text-center text-lg tracking-[0.4em]"
        />
        <Button onClick={onVerify} disabled={code.length !== 6 || verifyOtp.isPending}>
          {verifyOtp.isPending ? 'Verifying…' : 'Verify'}
        </Button>
      </div>

      {attemptsRemaining !== null && (
        <p className="mt-2 text-sm text-px-danger" role="alert" aria-live="polite">
          {attemptsRemaining === 0
            ? 'No attempts remaining — please request a new code.'
            : `Invalid code. ${attemptsRemaining} attempt${attemptsRemaining === 1 ? '' : 's'} remaining.`}
        </p>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/verify-stage.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/VerifyStage.tsx \
  frontend/session/tests/components/interview/verify-stage.test.tsx
git commit -m "feat(session): VerifyStage — OTP step re-themed (logic unchanged)"
```

---

## Task 8: ReadyStage (camera/mic test → single Start)

**Files:**
- Create: `frontend/session/app/interview/[token]/ReadyStage.tsx`
- Test: `frontend/session/tests/components/interview/ready-stage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/ready-stage.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ReadyStage } from '@/app/interview/[token]/ReadyStage'

vi.mock('@/app/interview/[token]/sampleNoiseFloorDbfs', () => ({
  sampleNoiseFloorDbfs: vi.fn().mockResolvedValue(-50),
}))
vi.mock('@/lib/proctoring/displays', () => ({
  isMultiDisplay: () => false,
  subscribeDisplayChange: () => () => {},
}))

describe('ReadyStage', () => {
  it('keeps Start hidden until devices are tested, then starts on click', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn()
    render(<ReadyStage onStart={onStart} proctored={false} />)

    // No Start before testing.
    expect(screen.queryByRole('button', { name: /^start$/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /test camera & mic/i }))
    const start = await screen.findByRole('button', { name: /^start$/i })
    await user.click(start)
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  it('shows a retry path when permission is denied', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockRejectedValueOnce(
      Object.assign(new Error('denied'), { name: 'NotAllowedError' }),
    )
    render(<ReadyStage onStart={vi.fn()} proctored={false} />)
    await user.click(screen.getByRole('button', { name: /test camera & mic/i }))
    await waitFor(() => expect(screen.getByText(/permission denied/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/ready-stage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `ReadyStage.tsx`**

Port `CameraMicStep.tsx` logic verbatim (getUserMedia, noise sample, multi-display warning), renaming the pass callback to `onStart` and the final CTA to **Start**. Full code:

```tsx
// app/interview/[token]/ReadyStage.tsx
'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  /** Called when the candidate clicks Start after devices pass. */
  onStart: () => void
  /** When true, surface the single-display warning (non-blocking). */
  proctored?: boolean
}

type Status = 'idle' | 'prompting' | 'sampling' | 'ready' | 'denied'

// dBFS threshold for a "noisy" room (post browser-NS). We warn, never block.
const NOISE_WARN_DBFS = -30

export function ReadyStage({ onStart, proctored = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)
  const [multiDisplay, setMultiDisplay] = useState<boolean | null>(null)

  useEffect(() => {
    if (!proctored) return
    const refresh = () => setMultiDisplay(isMultiDisplay())
    refresh()
    return subscribeDisplayChange(refresh)
  }, [proctored])

  const displayWarn = proctored && multiDisplay === true

  const start = async () => {
    setStatus('prompting')
    setError(null)
    setNoiseDbfs(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setStatus('sampling')
      const dbfs = await sampleNoiseFloorDbfs(stream)
      setNoiseDbfs(dbfs)
      setStatus('ready')
    } catch (err) {
      const name = (err as Error).name
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setError('Permission denied. Please enable camera and microphone in your browser settings.')
      } else if (name === 'NotFoundError') {
        setError('No camera or microphone detected on this device.')
      } else {
        setError((err as Error).message)
      }
      setStatus('denied')
    }
  }

  // Release devices when leaving this stage so LiveKit can re-acquire them.
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const noisy = noiseDbfs !== null && noiseDbfs > NOISE_WARN_DBFS

  return (
    <div className="mx-auto w-full max-w-lg">
      <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
        Camera &amp; microphone
      </p>
      <h1 className="px-serif mt-1.5 text-[clamp(24px,5vw,32px)] font-normal tracking-[-0.4px] text-px-fg">
        Let’s check your setup
      </h1>
      <p className="mt-2 text-[14.5px] leading-relaxed text-px-fg-2">
        We’ll use your camera and microphone during the interview. Headphones are recommended for the cleanest call.
      </p>

      <div className="mt-5 aspect-video w-full overflow-hidden rounded-2xl border border-px-hairline bg-black/85">
        <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {status === 'idle' && <Button onClick={start}>Test camera &amp; mic</Button>}
        {status === 'prompting' && (
          <p className="text-sm text-px-fg-3">Waiting for permission…</p>
        )}
        {status === 'sampling' && (
          <p className="text-sm text-px-fg-3">Listening for background noise (stay quiet for a moment)…</p>
        )}
        {status === 'ready' && (
          <>
            <span className="text-sm font-medium text-px-ok">Camera and mic are working ✓</span>
            <Button size="lg" onClick={onStart}>
              Start →
            </Button>
          </>
        )}
        {status === 'denied' && (
          <>
            <span className="text-sm text-px-danger">{error}</span>
            <Button variant="outline" onClick={start}>
              Retry
            </Button>
          </>
        )}
      </div>

      {status === 'ready' && (noisy || displayWarn) && (
        <div role="status" className="mt-3 space-y-2">
          {noisy && (
            <p className="text-[13px] leading-relaxed text-px-caution">
              Your environment sounds noisy. The interview will still work, but for the cleanest call, find a quieter spot.
            </p>
          )}
          {displayWarn && (
            <p className="text-[13px] leading-relaxed text-px-caution">
              We detected more than one display. A single screen is recommended — using multiple displays is flagged during the interview.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- tests/components/interview/ready-stage.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/ReadyStage.tsx \
  frontend/session/tests/components/interview/ready-stage.test.tsx
git commit -m "feat(session): ReadyStage — camera/mic test with single Start CTA"
```

---

## Task 9: WizardFrame rewrite (BinQle header + split layout + StageProgress) + hero keyframes

**Files:**
- Modify (rewrite): `frontend/session/app/interview/[token]/WizardFrame.tsx`
- Modify: `frontend/session/app/globals.css` (add reduced-motion-safe hero keyframes)
- Test: `frontend/session/tests/components/interview/wizard-frame.test.tsx`

- [ ] **Step 1: Add hero keyframes to `globals.css`**

Append near the other animation/keyframe declarations (search for `@keyframes` in `app/globals.css` and add alongside). These are gated by `prefers-reduced-motion` so they never run for motion-sensitive users:

```css
/* Hero scene — gentle float for the Arjun orb + particles (IntroStage). */
@media (prefers-reduced-motion: no-preference) {
  @keyframes heroOrbFloat {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-6px); }
  }
  @keyframes heroParticleDrift {
    0%, 100% { transform: translateY(0); opacity: 0.5; }
    50% { transform: translateY(-10px); opacity: 0.85; }
  }
  .hero-orb { animation: heroOrbFloat 6s ease-in-out infinite; transform-origin: center; }
  .hero-particle { animation: heroParticleDrift 5s ease-in-out infinite; }
  .hero-particle-0 { animation-delay: 0s; }
  .hero-particle-1 { animation-delay: 0.8s; }
  .hero-particle-2 { animation-delay: 1.6s; }
  .hero-particle-3 { animation-delay: 2.4s; }
}
```

- [ ] **Step 2: Write the failing test**

```tsx
// tests/components/interview/wizard-frame.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WizardFrame } from '@/app/interview/[token]/WizardFrame'

describe('WizardFrame', () => {
  it('renders the BinQle brand, the screening title, the progress indicator, and children', () => {
    render(
      <WizardFrame companyName="Acme" jobTitle="Engineer" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div>stage body</div>
      </WizardFrame>,
    )
    expect(screen.getByRole('img', { name: /binqle\.ai/i })).toBeInTheDocument()
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText(/step 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByText('stage body')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/wizard-frame.test.tsx`
Expected: FAIL — current `WizardFrame` has a different prop signature (`current`/`otpRequired`) and renders the company initial chip, not BrandMark.

- [ ] **Step 4: Rewrite `WizardFrame.tsx`**

```tsx
// app/interview/[token]/WizardFrame.tsx
'use client'

import type { ReactNode } from 'react'

import { BrandMark } from '@/components/interview/BrandMark'
import { StageProgress } from './StageProgress'

interface WizardFrameProps {
  companyName: string
  jobTitle: string
  steps: string[]
  currentIndex: number
  accent?: string
  children: ReactNode
}

function Header({ companyName, jobTitle }: { companyName: string; jobTitle: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark variant="mark" className="h-7 w-7" />
      <span className="text-[13px] text-px-fg">
        <b className="font-semibold">{companyName}</b>
        {jobTitle && <span className="text-px-fg-4"> · {jobTitle}</span>}
      </span>
    </div>
  )
}

/**
 * Outer chrome for the pre-check stages: BinQle header + screening identity, a
 * minimal progress indicator, and the active stage (already wrapped in
 * StageTransition by WizardShell). The site-wide AnimatedBackground shows through
 * the transparent .px-cine-bg.
 */
export function WizardFrame({
  companyName,
  jobTitle,
  steps,
  currentIndex,
  accent,
  children,
}: WizardFrameProps) {
  return (
    <div
      className="px-cine-bg flex min-h-dvh flex-col"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-6 pt-6">
        <Header companyName={companyName} jobTitle={jobTitle} />
        <StageProgress steps={steps} currentIndex={currentIndex} className="hidden sm:flex" />
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 items-center px-6 py-8">
        <div className="w-full">{children}</div>
      </main>
    </div>
  )
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm run test -- tests/components/interview/wizard-frame.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/WizardFrame.tsx \
  frontend/session/app/globals.css \
  frontend/session/tests/components/interview/wizard-frame.test.tsx
git commit -m "feat(session): WizardFrame — BinQle header, split layout, progress; hero keyframes"
```

---

## Task 10: WizardShell rewrite (stage derivation + transition wiring)

**Files:**
- Modify (rewrite): `frontend/session/app/interview/[token]/WizardShell.tsx`
- Modify: `frontend/session/tests/components/interview/wizard-shell-terminated.test.tsx` (prop/import drift only — verify still green)
- Test: `frontend/session/tests/components/interview/wizard-shell.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/interview/wizard-shell.test.tsx
import { screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { WizardShell } from '@/app/interview/[token]/WizardShell'
import { candidateSessionApi, type PreCheckResponse } from '@/lib/api/candidate-session'

vi.mock('@/components/agents-ui/aura', () => ({ Aura: () => <div data-testid="aura" /> }))
// Keep the heavy live App out of these pre-check tests.
vi.mock('@/components/interview/app/app', () => ({ App: () => <div data-testid="live-app" /> }))

const base: PreCheckResponse = {
  session_id: 's1',
  company_name: 'Acme',
  job_title: 'Engineer',
  duration_minutes: 20,
  consent_text: 'consent',
  state: 'created',
  otp_required: false,
  otp_verified_at: null,
  otp_issued_at: null,
  proctoring_enabled: true,
  proctoring_outcome: null,
} as PreCheckResponse

function mockPreCheck(data: PreCheckResponse) {
  return vi.spyOn(candidateSessionApi, 'preCheck').mockResolvedValue(data)
}

afterEach(() => vi.restoreAllMocks())

describe('WizardShell stage derivation', () => {
  it('renders IntroStage for a created session', async () => {
    mockPreCheck(base)
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByRole('heading', { name: /engineer/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /i'm ready/i })).toBeInTheDocument()
  })

  it('renders VerifyStage when consented + otp required + unverified', async () => {
    mockPreCheck({ ...base, state: 'consented', otp_required: true, otp_verified_at: null })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByText(/enter your access code/i)).toBeInTheDocument()
  })

  it('renders ReadyStage when consented + otp satisfied', async () => {
    mockPreCheck({ ...base, state: 'consented', otp_required: false })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByText(/let’s check your setup/i)).toBeInTheDocument()
  })

  it('mounts the live App (rejoin) for an active session', async () => {
    mockPreCheck({ ...base, state: 'active' })
    renderWithProviders(<WizardShell token="tok" />)
    expect(await screen.findByTestId('live-app')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- tests/components/interview/wizard-shell.test.tsx`
Expected: FAIL — the current `WizardShell` still renders the old `WelcomeStep`/`ConsentStep` (no "I'm ready" button, no `heading` for the role).

- [ ] **Step 3: Rewrite `WizardShell.tsx`**

```tsx
// app/interview/[token]/WizardShell.tsx
'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'

import { APP_CONFIG_DEFAULTS, type AppConfig } from '@/app-config'
import { CompletionScreen } from '@/components/interview/app/CompletionScreen'
import { ProctoringEndedScreen } from '@/components/interview/app/ProctoringEndedScreen'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { IntroStage } from './IntroStage'
import { ReadyStage } from './ReadyStage'
import { StageTransition } from './StageTransition'
import { VerifyStage } from './VerifyStage'
import { WizardFrame } from './WizardFrame'

const App = dynamic(() => import('@/components/interview/app/app').then((m) => m.App), {
  ssr: false,
  loading: () => (
    <div className="grid min-h-dvh place-items-center text-[14px] text-px-fg-3">Connecting…</div>
  ),
})

type Stage = 'intro' | 'verify' | 'ready'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)
  const [camMicPassed, setCamMicPassed] = useState(false)

  const stage = useMemo<Stage>(() => {
    if (!data) return 'intro'
    if (data.state === 'created' || data.state === 'pre_check') return 'intro'
    if (data.state === 'consented') {
      if (data.otp_required && !data.otp_verified_at) return 'verify'
      return 'ready'
    }
    return 'ready'
  }, [data])

  const appConfig = useMemo<AppConfig>(
    () =>
      data
        ? {
            ...APP_CONFIG_DEFAULTS,
            companyName: data.company_name,
            pageTitle: `${data.company_name} · Interview`,
          }
        : APP_CONFIG_DEFAULTS,
    [data],
  )

  // The progress indicator: OTP only contributes a step when required.
  const { steps, currentIndex } = useMemo(() => {
    const otp = !!data?.otp_required
    const labels = otp ? ['Welcome', 'Verify', 'Ready'] : ['Welcome', 'Ready']
    const idx = stage === 'intro' ? 0 : stage === 'verify' ? 1 : labels.length - 1
    return { steps: labels, currentIndex: idx }
  }, [data?.otp_required, stage])

  if (isLoading) {
    return (
      <WizardFrame companyName="" jobTitle="" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div className="px-glass mx-auto max-w-md rounded-2xl p-6 text-center text-sm text-px-fg-3">
          Loading…
        </div>
      </WizardFrame>
    )
  }

  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div className="px-glass mx-auto max-w-md rounded-2xl p-8 text-center">
          <h1 className="px-serif m-0 text-[28px] font-normal text-px-fg">This link isn’t valid</h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px] leading-relaxed text-px-fg-3">
            The invite may have been revoked, replaced, or expired. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  if (!data) return null

  if (data.state === 'completed') return <CompletionScreen />
  if (data.state === 'terminated') return <ProctoringEndedScreen reason={data.proctoring_outcome} />
  if (data.state === 'cancelled' || data.state === 'error') {
    return (
      <WizardFrame
        companyName={data.company_name}
        jobTitle={data.job_title}
        steps={steps}
        currentIndex={0}
        accent={appConfig.accent}
      >
        <div className="px-glass mx-auto max-w-md rounded-2xl p-8 text-center">
          <h1 className="px-serif m-0 text-[28px] font-normal text-px-fg">This session has ended</h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px] leading-relaxed text-px-fg-3">
            This interview link is no longer active. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  // Active session → rejoin path (bypasses pre-check; already consented).
  if (data.state === 'active') {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="rejoin" />
  }

  // Ready + devices passed → start path with autoStart (no redundant WelcomeView).
  if (stage === 'ready' && camMicPassed) {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="start" autoStart />
  }

  return (
    <WizardFrame
      companyName={data.company_name}
      jobTitle={data.job_title}
      steps={steps}
      currentIndex={currentIndex}
      accent={appConfig.accent}
    >
      <StageTransition stageKey={stage}>
        {stage === 'intro' && (
          <IntroStage
            token={token}
            companyName={data.company_name}
            jobTitle={data.job_title}
            durationMinutes={data.duration_minutes}
            consentText={data.consent_text}
            proctoringEnabled={data.proctoring_enabled}
          />
        )}
        {stage === 'verify' && <VerifyStage token={token} otpIssuedAt={data.otp_issued_at} />}
        {stage === 'ready' && (
          <ReadyStage onStart={() => setCamMicPassed(true)} proctored={data.proctoring_enabled} />
        )}
      </StageTransition>
    </WizardFrame>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- tests/components/interview/wizard-shell.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify the terminated-state test still passes (fix prop drift if needed)**

Run: `npm run test -- tests/components/interview/wizard-shell-terminated.test.tsx`
Expected: PASS. The `terminated`/`completed` branches are unchanged behaviorally. If the test mocks `App` or asserts on the old stepper, update only the import/mocks — do not change the asserted terminal behavior. (Note: `App` now accepts an extra `autoStart` prop; existing mocks ignore it.)

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/app/interview/\[token\]/WizardShell.tsx \
  frontend/session/tests/components/interview/wizard-shell.test.tsx \
  frontend/session/tests/components/interview/wizard-shell-terminated.test.tsx
git commit -m "feat(session): WizardShell — 2-stage derivation + transition wiring"
```

---

## Task 11: App autoStart (remove redundant in-session WelcomeView for the start path)

**Files:**
- Modify: `frontend/session/components/interview/app/app.tsx`
- Modify: `frontend/session/components/interview/app/view-controller.tsx`
- Create: `frontend/session/components/interview/app/ConnectingView.tsx`
- Modify: `frontend/session/tests/components/interview/app.test.tsx` (update start-path expectation)
- Test: `frontend/session/tests/components/interview/connecting-view.test.tsx`

- [ ] **Step 1: Create `ConnectingView.tsx` + its test**

```tsx
// tests/components/interview/connecting-view.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ConnectingView } from '@/components/interview/app/ConnectingView'

describe('ConnectingView', () => {
  it('shows a branded connecting message', () => {
    render(<ConnectingView />)
    expect(screen.getByText(/connecting you to your interview/i)).toBeInTheDocument()
  })
})
```

```tsx
// components/interview/app/ConnectingView.tsx
'use client'

import { BrandMark } from '@/components/interview/BrandMark'

/**
 * Shown while the start path connects to LiveKit (autoStart). Replaces the old
 * second "Start interview" welcome screen — the wizard's Ready stage is the only
 * Start the candidate sees.
 */
export function ConnectingView() {
  return (
    <div className="px-cine-bg grid min-h-dvh place-items-center px-6">
      <div className="px-glass flex max-w-sm flex-col items-center gap-4 rounded-2xl px-8 py-10 text-center">
        <BrandMark variant="mark" className="h-9 w-9" />
        <div
          className="size-6 animate-spin rounded-full border-2 border-px-hairline border-t-px-accent"
          aria-hidden
        />
        <p className="text-[15px] font-medium text-px-fg" role="status" aria-live="polite">
          Connecting you to your interview…
        </p>
        <p className="text-[13px] text-px-fg-3">Arjun will be with you in a moment.</p>
      </div>
    </div>
  )
}
```

Run: `npm run test -- tests/components/interview/connecting-view.test.tsx`
Expected: PASS.

- [ ] **Step 2: Add `autoStart` to `App` (`app.tsx`)**

In the `Props` interface, add the field:

```tsx
interface Props {
  appConfig: AppConfig
  token: string
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
  /** When true (start path from the wizard's Ready stage), connect immediately
   *  instead of showing an in-session welcome screen. */
  autoStart?: boolean
}
```

Destructure it: `export function App({ appConfig, token, preCheck, mode, autoStart = false }: Props) {`

Add a one-shot auto-start effect after `onStart` is defined (it depends on `onStart`/`session`). Place it just before the `return (`:

```tsx
  const autoStartedRef = useRef(false)
  useEffect(() => {
    if (autoStart && !autoStartedRef.current) {
      autoStartedRef.current = true
      onStart()
    }
  }, [autoStart, onStart])
```

(`useRef`/`useEffect` are already imported in this file.)

Pass `autoStart` down to `ViewController` in the JSX:

```tsx
        <ViewController
          appConfig={appConfig}
          preCheck={preCheck}
          mode={mode}
          autoStart={autoStart}
          outcome={outcome}
          ...
```

- [ ] **Step 3: Branch `ViewController` to the connecting view (`view-controller.tsx`)**

Add `autoStart` to the `Props` interface and destructure it:

```tsx
  autoStart?: boolean
```

Add the import:

```tsx
import { ConnectingView } from './ConnectingView'
```

Replace the `if (!isConnected) { return <WelcomeView ... /> }` block with:

```tsx
  if (!isConnected) {
    // Start path auto-connects from the wizard's Ready stage — show a connecting
    // screen, not a second welcome/start screen. Rejoin keeps its confirm screen.
    if (autoStart) return <ConnectingView />
    return (
      <WelcomeView
        companyName={appConfig.companyName}
        jobTitle={preCheck.job_title}
        durationMinutes={preCheck.duration_minutes}
        startButtonText={appConfig.startButtonText}
        mode={mode}
        onStartCall={onStart}
        isPending={isStartPending}
        proctored={preCheck.proctoring_enabled}
      />
    )
  }
```

- [ ] **Step 4: Update `app.test.tsx` start-path expectation**

Open `tests/components/interview/app.test.tsx`. Any test that mounts `App` with `mode="start"` and asserts the `WelcomeView` ("Start interview" button) appears must be updated: with `autoStart`, the start path shows `ConnectingView` and auto-calls `session.start()`. For the start-path test, pass `autoStart` and assert the connecting copy instead:

```tsx
// (within the start-path test) render with autoStart and assert connecting state
// render(<App appConfig={cfg} token="tok" preCheck={startPreCheck} mode="start" autoStart />)
// expect(await screen.findByText(/connecting you to your interview/i)).toBeInTheDocument()
```

Leave `mode="rejoin"` tests unchanged (they still show `WelcomeView`). If `session.start` is mocked via the LiveKit `useSession` mock in this test file, assert it was called once on mount. Run the file and adjust assertions to match the connecting-view behavior — do not weaken error-path assertions.

- [ ] **Step 5: Run the affected tests**

Run: `npm run test -- tests/components/interview/app.test.tsx tests/components/interview/connecting-view.test.tsx`
Expected: PASS. (welcome-view.test.tsx is untouched and still green — `WelcomeView` still exists for rejoin.)

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/components/interview/app/app.tsx \
  frontend/session/components/interview/app/view-controller.tsx \
  frontend/session/components/interview/app/ConnectingView.tsx \
  frontend/session/tests/components/interview/app.test.tsx \
  frontend/session/tests/components/interview/connecting-view.test.tsx
git commit -m "feat(session): autoStart path — single Start, branded connecting view (no redundant welcome)"
```

---

## Task 12: Remove old wizard files + cleanup, full verification

**Files:**
- Delete: `WelcomeStep.tsx`, `ConsentStep.tsx`, `OtpStep.tsx`, `CameraMicStep.tsx`, `WizardStepper.tsx`
- Delete old tests: `welcome-step.test.tsx`, `CameraMicStep.test.tsx`, `OtpStep.test.tsx`, `wizard-stepper.test.tsx`
- Verify: no `ProjectX` strings remain in the pre-check surface

- [ ] **Step 1: Delete superseded components + their tests**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
git rm app/interview/\[token\]/WelcomeStep.tsx \
       app/interview/\[token\]/ConsentStep.tsx \
       app/interview/\[token\]/OtpStep.tsx \
       app/interview/\[token\]/CameraMicStep.tsx \
       app/interview/\[token\]/WizardStepper.tsx \
       tests/components/interview/welcome-step.test.tsx \
       tests/components/interview/CameraMicStep.test.tsx \
       tests/components/interview/OtpStep.test.tsx \
       tests/components/interview/wizard-stepper.test.tsx
```

- [ ] **Step 2: Check for dangling imports of the deleted files**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
grep -rnE "WelcomeStep|ConsentStep|OtpStep|CameraMicStep|WizardStepper" app components tests || echo "no dangling refs"
```
Expected: `no dangling refs`. If anything appears (other than the new files), fix the import before continuing.

- [ ] **Step 3: Confirm no ProjectX branding remains on the pre-check surface**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
grep -rni "projectx" app components lib app-config.ts || echo "no ProjectX references"
```
Expected: `no ProjectX references`. (The `public/projectx-logo.svg` asset may remain on disk — it's a synced drift-discipline file — but no code should reference it. If `app/layout.tsx` metadata or `app/page.tsx`/`not-found.tsx` reference ProjectX, update those strings to `BinQle.ai`/`brand.name` and re-run.)

- [ ] **Step 4: Lint + type-check + full test suite**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
npm run lint
npm run type-check
npm run test
```
Expected: all pass. Fix any issue surfaced (most likely: an import of a deleted file, or a `motion/react` type mismatch — ensure `motion` is imported from `motion/react`, not `framer-motion`).

- [ ] **Step 5: Production build (bundle sanity)**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
npm run build
```
Expected: build succeeds. The pre-`/start` route stays light (no new deps; illustrations are inline SVG; LiveKit still lazy-loaded via `next/dynamic` in `WizardShell`).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add -A
git commit -m "chore(session): remove superseded wizard steps; verify no ProjectX refs; green build"
```

---

## Task 13: Manual verification (real browser, mobile + desktop)

- [ ] **Step 1: Run the dev server and walk the flow**

```bash
cd /home/ishant/Projects/ProjectX/frontend/session
npm run dev   # localhost:3002
```

With a valid candidate token (`/interview/{token}`), verify against the spec:

- [ ] **Intro stage:** BinQle.ai mark in header; `Company · Role` shown; headline = role; duration chip `~N min`; 4–5 instruction rows, each expands on tap/click with a "why"; the one-time-link row reads in the caution tone; "Privacy & consent" opens the full consent dialog (Esc/✕ closes); hero scene + particles animate.
- [ ] **Consent soft-fold:** clicking **"I'm ready →"** shows a brief pending state, then transitions (scale + blur + fade) to the next stage. Network panel shows exactly one `POST /consent`.
- [ ] **Verify stage (OTP-required JD only):** Send code → 60s cooldown; wrong code shows attempts-remaining (announced); correct code transitions to Ready.
- [ ] **Ready stage:** "Test camera & mic" prompts permission; preview shows; once ready a single **Start →** appears; noisy/multi-display warnings render when applicable; clicking **Start** shows the branded **ConnectingView** then the live interview (no second "Start interview" screen).
- [ ] **Reduced motion:** enable OS "reduce motion" → transitions become a plain crossfade; hero/particles do not animate.
- [ ] **Mobile (375px / DevTools device):** single column, hero compact at top, full-width CTA, no horizontal scroll, text ≥16px, targets ≥44px.
- [ ] **Edge states:** revoked/expired token → "This link isn’t valid"; completed session → completion screen; terminated → proctoring-ended screen.

- [ ] **Step 2: Note any polish items** and address before finishing the branch (per superpowers:finishing-a-development-branch).

---

## Self-Review (completed during planning)

- **Spec coverage:** 2-stage flow (T6/T8/T10) ✓; OTP conditional slide (T7/T10) ✓; consent soft-fold reusing `useConsent` (T6) ✓; scale/blur/fade transition + reduced-motion (T5) ✓; hand-crafted SVG illustrations + interactive list (T2/T3) ✓; BinQle branding (T1, T9, T11, T12) ✓; mobile+desktop (T6/T9 layout, T13 verify) ✓; single Start / no redundant welcome (T11) ✓; edge states preserved (T10) ✓; no backend/dep change ✓; tests at each step ✓.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `Instruction` shape (T3) matches usage in `IntroStage` (T6); `WizardFrame` props (`steps`/`currentIndex`) match `WizardShell` (T10) and the test (T9); `App` gains `autoStart` (T11) and `WizardShell` passes it (T10); `onStart` callback name consistent across `ReadyStage` (T8) and `WizardShell` (T10).
- **Risks honored:** consent advances on server-state flip (not optimistic); transform/opacity/filter-only animation; device tracks released on ReadyStage unmount before LiveKit re-acquires.
