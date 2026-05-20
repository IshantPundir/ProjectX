# Candidate Session Redesign — Plan 3: Pre-join Wizard + Standalone Pages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the pre-join experience into the split two-pane cinematic-glass layout (left pane: aura + reassuring copy + stepper; right pane: the task card), add a Welcome step as the calm entry point, and restyle the standalone token-error and root landing pages. Collapses to a single column on mobile.

**Architecture:** Extract the inline `WizardFrame`/`StepProgress` from `WizardShell.tsx` into dedicated, testable components (`WizardFrame.tsx`, `WizardStepper.tsx`) rebuilt as the split two-pane shell. A new client-only `WelcomeStep` gates the flow before consent (no backend change — server state still drives consent→otp→cam-mic). The existing step components (`ConsentStep`, `OtpStep`, `CameraMicStep`) already render via `var(--px-*)` tokens, so Plan 1's theme switch makes them dark automatically — Plan 3 only moves the page chrome into the frame and applies light token fixes. The human-review-gated permission logic inside `CameraMicStep`/`OtpStep`/`ConsentStep` is **not** changed.

**Tech Stack:** Next.js 16 App Router, React 19, Tailwind v4, `LiquidAura` (Plan 1), Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-20-candidate-session-redesign-design.md`
**Depends on:** Plan 1 (theme + `LiquidAura` + `.px-glass*`/`.px-cine-bg`). Plan 2 not strictly required, but normally merged first.

---

## Conventions for this plan

- All paths relative to `frontend/session/`. Run commands from `frontend/session/`.
- Do NOT change permission/getUserMedia/consent/OTP logic — only layout/styling and the new Welcome gate. The "Human Review Required For: any change to OTP, consent, or camera/mic step flow" rule applies; keep edits to those three files limited to the token/styling touch-ups specified here.
- Reuse Plan 1 utilities: `.px-cine-bg`, `.px-glass`, `LiquidAura`.
- Run `npm run test` before each commit.

---

## File structure (Plan 3)

| File | Responsibility | Action |
|---|---|---|
| `app/interview/[token]/WizardStepper.tsx` | Linear stepper (Consent → Verify → Camera & mic) | Create |
| `app/interview/[token]/WizardFrame.tsx` | Split two-pane cinematic shell | Create |
| `app/interview/[token]/WelcomeStep.tsx` | Calm entry card with "Begin" CTA | Create |
| `app/interview/[token]/WizardShell.tsx` | Use new frame; add intro gate | Modify |
| `app/interview/[token]/CameraMicStep.tsx` | Token fix (amber → caution) only | Modify |
| `app/interview/[token]/error/page.tsx` | Restyle dark-cinematic | Modify |
| `app/page.tsx` | Restyle dark-cinematic | Modify |
| `tests/components/interview/wizard-stepper.test.tsx` | Stepper states | Create |
| `tests/components/interview/welcome-step.test.tsx` | Welcome CTA | Create |

---

### Task 1: WizardStepper (TDD)

**Files:**
- Create: `app/interview/[token]/WizardStepper.tsx`
- Test: `tests/components/interview/wizard-stepper.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WizardStepper } from '@/app/interview/[token]/WizardStepper'

describe('WizardStepper', () => {
  it('omits Verify when OTP is not required', () => {
    render(<WizardStepper current="consent" otpRequired={false} />)
    expect(screen.getByText('Consent')).toBeInTheDocument()
    expect(screen.getByText('Camera & mic')).toBeInTheDocument()
    expect(screen.queryByText('Verify')).not.toBeInTheDocument()
  })

  it('includes Verify when OTP is required', () => {
    render(<WizardStepper current="otp" otpRequired={true} />)
    expect(screen.getByText('Verify')).toBeInTheDocument()
  })

  it('marks the current step with aria-current', () => {
    render(<WizardStepper current="cam-mic" otpRequired={false} />)
    const active = screen.getByText('Camera & mic').closest('[data-step]')
    expect(active).toHaveAttribute('aria-current', 'step')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- wizard-stepper`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { cn } from '@/lib/utils'

export type WizardStepKey = 'consent' | 'otp' | 'cam-mic'

export function WizardStepper({
  current,
  otpRequired,
  className,
}: {
  current: WizardStepKey | 'welcome'
  otpRequired: boolean
  className?: string
}) {
  const steps: { key: WizardStepKey; label: string }[] = [
    { key: 'consent', label: 'Consent' },
    ...(otpRequired ? [{ key: 'otp' as const, label: 'Verify' }] : []),
    { key: 'cam-mic', label: 'Camera & mic' },
  ]
  const currentIdx = steps.findIndex((s) => s.key === current) // -1 for 'welcome'

  return (
    <ol className={cn('flex items-center gap-2', className)} aria-label="Setup progress">
      {steps.map((s, i) => {
        const done = currentIdx > -1 && i < currentIdx
        const active = i === currentIdx
        return (
          <li
            key={s.key}
            data-step={s.key}
            aria-current={active ? 'step' : undefined}
            className="flex items-center gap-2"
          >
            <span
              className={cn(
                'grid size-[18px] place-items-center rounded-full text-[10px] font-semibold',
                done && 'bg-px-accent text-white',
                active && 'bg-px-accent-soft text-[#04211d]',
                !done && !active && 'bg-px-surface-3 text-px-fg-4',
              )}
            >
              {done ? '✓' : i + 1}
            </span>
            <span
              className={cn(
                'text-[11px] font-medium',
                active ? 'text-px-fg' : 'text-px-fg-4',
              )}
            >
              {s.label}
            </span>
            {i < steps.length - 1 && <span className="h-px w-5 bg-px-hairline" aria-hidden />}
          </li>
        )
      })}
    </ol>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- wizard-stepper`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add "app/interview/[token]/WizardStepper.tsx" tests/components/interview/wizard-stepper.test.tsx
git commit -m "feat(session): wizard stepper component"
```

---

### Task 2: WelcomeStep (TDD)

**Files:**
- Create: `app/interview/[token]/WelcomeStep.tsx`
- Test: `tests/components/interview/welcome-step.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WelcomeStep } from '@/app/interview/[token]/WelcomeStep'

describe('WelcomeStep', () => {
  it('sets expectations and begins on CTA click', async () => {
    const user = userEvent.setup()
    const onBegin = vi.fn()
    render(<WelcomeStep durationMinutes={20} onBegin={onBegin} />)
    expect(screen.getByText(/no trick questions/i)).toBeInTheDocument()
    expect(screen.getByText(/20 minutes/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /begin/i }))
    expect(onBegin).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- welcome-step`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { Button } from '@/components/px'

export function WelcomeStep({
  durationMinutes,
  onBegin,
}: {
  durationMinutes: number
  onBegin: () => void
}) {
  return (
    <section
      className="rounded-[14px] border p-6"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <div
        className="mb-2 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
      >
        Welcome
      </div>
      <h2
        className="px-serif m-0 mb-3 text-[26px] font-normal"
        style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
      >
        A calm, conversational interview
      </h2>
      <ul className="mb-6 space-y-2 text-[14px]" style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}>
        <li>• Speak naturally — it&apos;s a conversation, with no trick questions.</li>
        <li>• Take your time. You can pause to think before answering.</li>
        <li>• It takes about {durationMinutes} minutes. You&apos;ll see your progress as you go.</li>
      </ul>
      <Button size="lg" onClick={onBegin}>
        Begin →
      </Button>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- welcome-step`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add "app/interview/[token]/WelcomeStep.tsx" tests/components/interview/welcome-step.test.tsx
git commit -m "feat(session): wizard welcome step"
```

---

### Task 3: WizardFrame — split two-pane shell

**Files:**
- Create: `app/interview/[token]/WizardFrame.tsx`

- [ ] **Step 1: Implement**

```tsx
'use client'

import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { LiquidAura } from '@/components/agents-ui/liquid-aura'
import { WizardStepper, type WizardStepKey } from './WizardStepper'

interface WizardFrameProps {
  companyName: string
  jobTitle: string
  current: WizardStepKey | 'welcome'
  otpRequired: boolean
  accent?: string
  children: ReactNode
}

function Brand({ companyName, jobTitle }: { companyName: string; jobTitle: string }) {
  return (
    <div className="flex items-center gap-2 text-[13px] text-px-fg">
      <span className="grid size-6 place-items-center rounded-[6px] bg-px-accent text-[11px] font-bold text-white">
        {(companyName || 'P').slice(0, 1).toUpperCase()}
      </span>
      <span>
        <b className="font-semibold">{companyName || 'ProjectX'}</b>
        {jobTitle && <span className="text-px-fg-4"> · {jobTitle}</span>}
      </span>
    </div>
  )
}

export function WizardFrame({
  companyName,
  jobTitle,
  current,
  otpRequired,
  accent,
  children,
}: WizardFrameProps) {
  return (
    <div
      className="px-cine-bg min-h-screen"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <div className="grid min-h-screen lg:grid-cols-2">
        {/* Left pane — reassurance (desktop only) */}
        <aside className="relative hidden flex-col justify-center gap-7 px-12 py-10 lg:flex">
          <Brand companyName={companyName} jobTitle={jobTitle} />
          <LiquidAura state="listening" audioTrack={undefined} size="hero" className="size-[120px]" />
          <div>
            <h1 className="font-serif text-[34px] font-medium leading-[1.1] text-px-fg">
              Meet your<br />interviewer
            </h1>
            <p className="mt-3 max-w-[300px] text-[13px] leading-relaxed text-px-fg-3">
              A calm, conversational AI screen. Take your time — there are no trick questions.
            </p>
          </div>
          <WizardStepper current={current} otpRequired={otpRequired} />
        </aside>

        {/* Right pane — the task */}
        <main className="flex items-center justify-center px-5 py-10 sm:px-8">
          <div className="w-full max-w-md">
            {/* Mobile header (left-pane condensed) */}
            <div className="mb-6 flex flex-col gap-4 lg:hidden">
              <Brand companyName={companyName} jobTitle={jobTitle} />
              <WizardStepper current={current} otpRequired={otpRequired} />
            </div>
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: no errors (file not yet imported anywhere — that happens in Task 4).

- [ ] **Step 3: Commit**

```bash
git add "app/interview/[token]/WizardFrame.tsx"
git commit -m "feat(session): split two-pane wizard frame"
```

---

### Task 4: Rewire WizardShell to the new frame + intro gate

Replace the inline `WizardFrame`/`StepProgress` and the centered "Pre-interview check" chrome with the new split frame, and add the Welcome gate before consent.

**Files:**
- Modify: `app/interview/[token]/WizardShell.tsx`

- [ ] **Step 1: Replace the imports + add intro state**

Change the imports (lines 1-12) to:

```tsx
'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'

import { APP_CONFIG_DEFAULTS, type AppConfig } from '@/app-config'
import { CompletionScreen } from '@/components/interview/app/CompletionScreen'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { CameraMicStep } from './CameraMicStep'
import { ConsentStep } from './ConsentStep'
import { OtpStep } from './OtpStep'
import { WelcomeStep } from './WelcomeStep'
import { WizardFrame } from './WizardFrame'
```

Delete the inline `WizardFrame` (old lines 148-191) and `StepProgress` (old lines 193-238) function definitions entirely — they are replaced by the imported `WizardFrame`/`WizardStepper`.

- [ ] **Step 2: Add the intro-seen state**

Inside `WizardShell`, after the existing `const [camMicPassed, setCamMicPassed] = useState(false)`:

```tsx
  const [introSeen, setIntroSeen] = useState(false)
```

- [ ] **Step 3: Replace the loading + error branches** to use the new frame

Replace the `if (isLoading)` block with:

```tsx
  if (isLoading) {
    return (
      <WizardFrame companyName="" jobTitle="" current="welcome" otpRequired={false}>
        <div
          className="rounded-[14px] border p-6 text-center text-sm"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
        >
          Loading…
        </div>
      </WizardFrame>
    )
  }
```

Replace the `if (error)` block with:

```tsx
  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" current="welcome" otpRequired={false}>
        <div
          className="rounded-[14px] border p-8 text-center"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <h1 className="px-serif m-0 text-[28px] font-normal" style={{ color: 'var(--px-fg)' }}>
            This link isn&apos;t valid
          </h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px]" style={{ color: 'var(--px-fg-3)', lineHeight: 1.7 }}>
            The invite may have been revoked, replaced, or expired. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }
```

- [ ] **Step 4: Replace the final `return (<WizardFrame …>)` block** (old lines 107-145) with the new frame + welcome gate

```tsx
  const stepperCurrent = !introSeen && currentStep === 'consent' ? 'welcome' : currentStep

  return (
    <WizardFrame
      companyName={data.company_name}
      jobTitle={data.job_title}
      current={stepperCurrent}
      otpRequired={data.otp_required}
      accent={appConfig.accent}
    >
      {currentStep === 'consent' && !introSeen && (
        <WelcomeStep durationMinutes={data.duration_minutes} onBegin={() => setIntroSeen(true)} />
      )}
      {currentStep === 'consent' && introSeen && (
        <ConsentStep token={token} consentText={data.consent_text} />
      )}
      {currentStep === 'otp' && <OtpStep token={token} otpIssuedAt={data.otp_issued_at} />}
      {currentStep === 'cam-mic' && !camMicPassed && (
        <CameraMicStep onPass={() => setCamMicPassed(true)} />
      )}
    </WizardFrame>
  )
```

Leave the `completed` / `active` / `cam-mic passed → App` branches above unchanged.

- [ ] **Step 5: Type-check + test + build**

Run: `npm run type-check && npm run test && npm run build`
Expected: no type errors; all tests pass; build succeeds. (If an existing `WizardShell` test asserted the old "Pre-interview check" headline, update it to assert the Welcome copy or the brand — note the change in the commit.)

- [ ] **Step 6: Manual check**

Run `npm run dev`, open a valid interview link — confirm the split layout (aura + copy + stepper on the left at ≥ lg; stacked on mobile), Welcome card first, then Consent after Begin. Stop the server.

- [ ] **Step 7: Commit**

```bash
git add "app/interview/[token]/WizardShell.tsx"
git commit -m "feat(session): split two-pane wizard + welcome gate"
```

---

### Task 5: CameraMicStep token fix (noisy warning)

The noisy-environment warning uses a hardcoded light-theme amber that reads poorly on dark. Switch to the caution token. **No logic change.**

**Files:**
- Modify: `app/interview/[token]/CameraMicStep.tsx` (the `noisy` warning, around lines 151-160)

- [ ] **Step 1: Replace the warning paragraph**

Change:

```tsx
          <p
            className="mt-3 text-[13px] text-amber-700"
            style={{ lineHeight: 1.6 }}
            role="status"
          >
```

to:

```tsx
          <p
            className="mt-3 text-[13px]"
            style={{ lineHeight: 1.6, color: 'var(--px-caution)' }}
            role="status"
          >
```

- [ ] **Step 2: Type-check + test**

Run: `npm run type-check && npm run test`
Expected: green (no behavior change).

- [ ] **Step 3: Commit**

```bash
git add "app/interview/[token]/CameraMicStep.tsx"
git commit -m "fix(session): noisy-env warning uses caution token on dark"
```

---

### Task 6: Restyle standalone pages (token-error + root landing)

**Files:**
- Modify: `app/interview/[token]/error/page.tsx` (JSX only — keep the `MESSAGES` map + logic)
- Modify: `app/page.tsx`

- [ ] **Step 1: error/page.tsx — replace the returned JSX** (lines 45-67)

```tsx
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-[28px] font-normal text-px-fg" style={{ letterSpacing: '-0.5px' }}>
          {m.title}
        </h1>
        <p className="mx-auto mt-3 max-w-sm text-[15px] text-px-fg-3" style={{ lineHeight: 1.7 }}>
          {m.body}
        </p>
        <Link href="/" className="mt-8 inline-block text-sm text-px-fg-4 underline">
          Go to homepage
        </Link>
      </div>
    </div>
  )
```

- [ ] **Step 2: app/page.tsx — full replacement**

```tsx
export default function HomePage() {
  return (
    <main className="px-cine-bg grid min-h-screen place-items-center px-6 py-12">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">Private interview link</h1>
        <p className="mt-3 text-sm text-px-fg-3">
          Please use the interview link sent to your email. If you don&apos;t have a link, contact your recruiter.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 3: Type-check + build**

Run: `npm run type-check && npm run build`
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add "app/interview/[token]/error/page.tsx" app/page.tsx
git commit -m "feat(session): restyle token-error + landing pages to dark-cinematic"
```

---

### Task 7: Mobile + accessibility + reduced-motion verification

A verification task (no new code unless a check fails).

- [ ] **Step 1: Mobile layout (≤ 375px and 320px)**

Run `npm run dev`, open a valid link, and with devtools device emulation at 320px and 375px confirm:
- Wizard: single column — brand + stepper above the card; card not clipped; CTA reachable.
- Live (if Plan 2 merged): aura centered, self-view not overlapping the End control, panel pill tappable, caption readable.

If anything overflows, adjust only the Tailwind responsive classes on the affected container (e.g. `w-[min(360px,86vw)]` on the panel) — no logic changes.

- [ ] **Step 2: Keyboard navigation**

Tab through each wizard step: Welcome `Begin`, Consent checkbox + Continue, OTP send/input/verify, CameraMic test/continue. Every control must be focusable with a visible focus ring and operable via Enter/Space.

- [ ] **Step 3: Reduced motion**

In devtools, enable "Emulate prefers-reduced-motion: reduce" and confirm the aura is static (no morph/spin/breathe), the Recording dot doesn't pulse, and the reconnect spinner is static. (These are guaranteed by Plan 1's `@media (prefers-reduced-motion: reduce)` block and `motion-reduce:animate-none` utilities — this step confirms them.)

- [ ] **Step 4: Screen-reader sanity**

Confirm: the aura exposes `role="img"` + `aria-label="AI interviewer"`; the progress chip has `role="status" aria-live="polite"`; the OTP error keeps `aria-live="polite"`; the stepper marks the active step with `aria-current="step"`.

- [ ] **Step 5: Header gate (security regression)**

Confirm the security headers are untouched:

Run: `npm run build && npm run start &` then in another shell `curl -sI http://localhost:3002/ | grep -iE "x-frame-options|referrer-policy|strict-transport|x-content-type"` ; stop the server.
Expected: all four headers present (unchanged from before this redesign).

- [ ] **Step 6: Commit any fixes**

If Steps 1-5 required class adjustments:

```bash
git add -A
git commit -m "fix(session): mobile/a11y polish for cinematic wizard"
```

If no changes were needed, note "verification only — no changes" and skip the commit.

---

## Self-review notes (verified while writing)

- **Spec coverage:** split two-pane wizard with left-pane reassurance + stepper (WizardFrame + WizardStepper), Welcome entry step (WelcomeStep + intro gate), mobile single-column (WizardFrame `lg:` breakpoints + mobile header), restyled standalone error/landing pages, and the a11y/reduced-motion/mobile/header-gate verification pass. The consent/OTP/cam-mic *flows* are unchanged (only chrome moved out + one token fix), honoring the human-review gate.
- **Type consistency:** `WizardStepKey` is exported from `WizardStepper.tsx` and imported by `WizardFrame.tsx`; `WizardShell` computes `current: WizardStepKey | 'welcome'`. `PreCheckResponse` fields used (`company_name`, `job_title`, `duration_minutes`, `consent_text`, `otp_required`, `otp_issued_at`) match `lib/api/candidate-session.ts` (verified). `WelcomeStep`/`ConsentStep`/`OtpStep`/`CameraMicStep` props match their definitions (verified).
- **Constraints:** no Supabase, no API/security/header changes, no getUserMedia/OTP/consent logic changes (only the `WizardShell` chrome + a CameraMicStep color token), lazy `App` import preserved (pre-join bundle stays light).

## Done — full redesign

With Plans 1-3 merged, the candidate surface is the cinematic-glass experience end to end: dark theme + bespoke Liquid aurora (Plan 1), the live interview surface with the Interview Session panel and single End control (Plan 2), and the split two-pane pre-join wizard (Plan 3). The branch `feat/session-cinematic-glass-redesign` is ready for final review and merge.
