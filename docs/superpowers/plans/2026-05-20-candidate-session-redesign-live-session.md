# Candidate Session Redesign — Plan 2: Live Session Surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the candidate live-interview UI with the cinematic-glass surface: the `LiquidAura` as the centered hero, the candidate as a small self-view, a floating "Interview Session" transcript panel (minimized by default), a quiet progress/timer chip, the current spoken line as a caption, and a single **End interview** control with a confirmation dialog. Voice-only — no mic/camera/screen-share/keyboard-chat controls.

**Architecture:** A new `components/interview/session/` tree of small, focused components composed by `LiveInterview`. `view-controller.tsx` swaps `ProgressBanner + AgentSessionView_01` for `LiveInterview` while keeping the existing `AgentUIWithLoader` intro gate and `ReconnectingOverlay`. Data binds to the existing hooks (`useStageProgress`, `useSessionMessages`, `useVoiceAssistant`, `useLocalTrackRef`). The End button uses `session.end()`; the existing `OutcomeWatcher` `CLIENT_INITIATED → onCompleted` branch (verified in `app.tsx:240-267`) routes to Completion — Plan 2 adds a regression test locking it. Terminal screens (Completion / Disconnect / Error / Reconnecting / Welcome) are restyled to dark-cinematic.

**Tech Stack:** Next.js 16, React 19, Tailwind v4, `@livekit/components-react` 2.9.20 (`useSessionContext`, `useSessionMessages`, `useVoiceAssistant`, `useLocalTrackRef`/`useLocalParticipant`, `VideoTrack`), `radix-ui` 1.4.3 (`Dialog`), `motion` 12, `lucide-react`, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-20-candidate-session-redesign-design.md`
**Depends on:** Plan 1 (theme tokens, `.px-glass*`, `LiquidAura`) must be merged first.

---

## Conventions for this plan

- All paths relative to `frontend/session/`. Run commands from `frontend/session/`.
- Reuse existing tokens/utilities from Plan 1: `.px-glass`, `.px-glass-strong`, `.px-glass-pill`, `.px-cine-bg`, `LiquidAura` (`size="hero" | "mark"`).
- Do NOT touch `proxy.ts`, `next.config.ts`, `lib/api/candidate-session.ts`, `lib/env.ts`.
- New components live in `components/interview/session/`.
- Run `npm run test` before each commit.

---

## File structure (Plan 2)

| File | Responsibility | Action |
|---|---|---|
| `components/interview/session/format-progress.ts` | Pure helpers: question label + MM:SS | Create |
| `components/interview/session/ProgressChip.tsx` | Glass chip: "Question X of N · MM:SS left" | Create |
| `components/interview/session/SpokenCaption.tsx` | Current AI spoken line (italic serif) | Create |
| `components/interview/session/InterviewSessionPanel.tsx` | Floating glass transcript card, minimized by default | Create |
| `components/interview/session/EndInterviewControl.tsx` | End button + glass confirmation dialog | Create |
| `components/interview/session/SelfView.tsx` | Candidate camera tile | Create |
| `components/interview/session/SessionTopBar.tsx` | Brand + Recording + End | Create |
| `components/interview/session/AuraStage.tsx` | Centered LiquidAura + state label | Create |
| `components/interview/session/useEnsureMediaPublished.ts` | Enable mic+camera on connect (always-on) | Create |
| `components/interview/session/LiveInterview.tsx` | Composes the live surface | Create |
| `components/interview/app/view-controller.tsx` | Render `LiveInterview` instead of banner+block | Modify |
| `components/interview/app/welcome-view.tsx` | Restyle dark-cinematic + aura | Modify |
| `components/interview/app/CompletionScreen.tsx` | Restyle dark-cinematic | Modify |
| `components/interview/app/DisconnectError.tsx` | Restyle dark-cinematic | Modify |
| `components/interview/app/session-error-screen.tsx` | Restyle dark-cinematic | Modify |
| `components/interview/app/ReconnectingOverlay.tsx` | Restyle dark-cinematic | Modify |
| `tests/components/interview/session/*.test.tsx` | Unit tests for the above | Create |
| `tests/components/interview/end-wiring.test.tsx` | OutcomeWatcher CLIENT_INITIATED regression | Create |

---

### Task 1: Progress formatting helpers (pure, TDD)

**Files:**
- Create: `components/interview/session/format-progress.ts`
- Test: `tests/components/interview/session/format-progress.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, expect, it } from 'vitest'
import { formatClock, questionLabel } from '@/components/interview/session/format-progress'

describe('formatClock', () => {
  it('formats seconds as M:SS', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(9)).toBe('0:09')
    expect(formatClock(75)).toBe('1:15')
    expect(formatClock(600)).toBe('10:00')
  })
  it('never returns negative time', () => {
    expect(formatClock(-5)).toBe('0:00')
  })
})

describe('questionLabel', () => {
  it('renders 1-based and clamps to total', () => {
    expect(questionLabel(0, 8)).toBe('Question 1 of 8')
    expect(questionLabel(7, 8)).toBe('Question 8 of 8')
    expect(questionLabel(9, 8)).toBe('Question 8 of 8') // clamp overrun
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- format-progress`
Expected: FAIL ("Failed to resolve import").

- [ ] **Step 3: Implement**

```typescript
/** Format a non-negative seconds count as M:SS. */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
}

/** "Question X of N", 1-based, clamped so it never exceeds the total. */
export function questionLabel(zeroBasedIndex: number, total: number): string {
  const display = Math.min(zeroBasedIndex + 1, total)
  return `Question ${display} of ${total}`
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- format-progress`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/format-progress.ts tests/components/interview/session/format-progress.test.ts
git commit -m "feat(session): progress formatting helpers"
```

---

### Task 2: ProgressChip (TDD)

**Files:**
- Create: `components/interview/session/ProgressChip.tsx`
- Test: `tests/components/interview/session/ProgressChip.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const stageMock = vi.fn()
vi.mock('@/components/interview/app/hooks/use-stage-progress', () => ({
  useStageProgress: () => stageMock(),
}))

import { ProgressChip } from '@/components/interview/session/ProgressChip'

describe('ProgressChip', () => {
  it('renders the question label and clock when progress is available', () => {
    stageMock.mockReturnValue({ currentQuestion: 1, totalQuestions: 8, timeRemainingSeconds: 750 })
    render(<ProgressChip />)
    expect(screen.getByText(/Question 2 of 8/)).toBeInTheDocument()
    expect(screen.getByText(/12:30 left/)).toBeInTheDocument()
  })

  it('renders nothing when there is no progress yet', () => {
    stageMock.mockReturnValue(null)
    const { container } = render(<ProgressChip />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- ProgressChip`
Expected: FAIL ("Failed to resolve import").

- [ ] **Step 3: Implement**

```tsx
'use client'

import { cn } from '@/lib/utils'
import { useStageProgress } from '@/components/interview/app/hooks/use-stage-progress'
import { formatClock, questionLabel } from './format-progress'

export function ProgressChip({ className }: { className?: string }) {
  const p = useStageProgress()
  if (!p) return null
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        'px-glass-pill flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-px-fg-2',
        className,
      )}
    >
      <span className="font-semibold text-px-fg">{questionLabel(p.currentQuestion, p.totalQuestions)}</span>
      <span aria-hidden className="opacity-40">·</span>
      <span className="font-mono tabular-nums">{formatClock(p.timeRemainingSeconds)} left</span>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- ProgressChip`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/ProgressChip.tsx tests/components/interview/session/ProgressChip.test.tsx
git commit -m "feat(session): glass progress chip"
```

---

### Task 3: Transcript message helpers (pure, TDD)

A small mapper turning `ReceivedMessage[]` into the panel/caption view model, isolated for testing.

**Files:**
- Create: `components/interview/session/transcript-model.ts`
- Test: `tests/components/interview/session/transcript-model.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, expect, it } from 'vitest'
import { toTurns, latestSpokenLine, type RawMessage } from '@/components/interview/session/transcript-model'

const msg = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id,
  timestamp: Number(id),
  from: { isLocal },
  message,
})

describe('toTurns', () => {
  it('maps local messages to "you" and remote to "ai", preserving order', () => {
    const turns = toTurns([msg('1', false, 'Hello'), msg('2', true, 'Hi there')])
    expect(turns).toEqual([
      { id: '1', who: 'ai', text: 'Hello' },
      { id: '2', who: 'you', text: 'Hi there' },
    ])
  })
  it('ignores empty/whitespace messages', () => {
    expect(toTurns([msg('1', false, '   ')])).toEqual([])
  })
})

describe('latestSpokenLine', () => {
  it('returns the most recent AI (remote) message text', () => {
    expect(latestSpokenLine([msg('1', false, 'First'), msg('2', true, 'me'), msg('3', false, 'Second')]))
      .toBe('Second')
  })
  it('returns null when there is no AI message', () => {
    expect(latestSpokenLine([msg('1', true, 'me')])).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- transcript-model`
Expected: FAIL.

- [ ] **Step 3: Implement**

```typescript
export interface RawMessage {
  id: string
  timestamp: number
  from?: { isLocal?: boolean }
  message: string
}

export interface Turn {
  id: string
  who: 'ai' | 'you'
  text: string
}

/** Map LiveKit ReceivedMessages to ordered turns, dropping empties. */
export function toTurns(messages: RawMessage[]): Turn[] {
  const turns: Turn[] = []
  for (const m of messages) {
    const text = (m.message ?? '').trim()
    if (!text) continue
    turns.push({ id: m.id, who: m.from?.isLocal ? 'you' : 'ai', text })
  }
  return turns
}

/** The most recent AI (remote) line — used for the spoken caption. */
export function latestSpokenLine(messages: RawMessage[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (!m.from?.isLocal) {
      const text = (m.message ?? '').trim()
      if (text) return text
    }
  }
  return null
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- transcript-model`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/transcript-model.ts tests/components/interview/session/transcript-model.test.ts
git commit -m "feat(session): transcript view-model helpers"
```

---

### Task 4: SpokenCaption (TDD)

**Files:**
- Create: `components/interview/session/SpokenCaption.tsx`
- Test: `tests/components/interview/session/SpokenCaption.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SpokenCaption } from '@/components/interview/session/SpokenCaption'
import type { RawMessage } from '@/components/interview/session/transcript-model'

const m = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id, timestamp: Number(id), from: { isLocal }, message,
})

describe('SpokenCaption', () => {
  it('shows the latest AI line', () => {
    render(<SpokenCaption messages={[m('1', false, 'Tell me about a project.')]} />)
    expect(screen.getByText('Tell me about a project.')).toBeInTheDocument()
  })
  it('renders nothing when there is no AI line', () => {
    const { container } = render(<SpokenCaption messages={[m('1', true, 'me')]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- SpokenCaption`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { cn } from '@/lib/utils'
import { latestSpokenLine, type RawMessage } from './transcript-model'

export function SpokenCaption({
  messages,
  className,
}: {
  messages: RawMessage[]
  className?: string
}) {
  const line = latestSpokenLine(messages)
  if (!line) return null
  return (
    <div
      className={cn(
        'px-glass max-w-[min(54ch,90vw)] rounded-2xl px-4 py-3 text-center',
        className,
      )}
    >
      <p className="font-serif text-[15px] italic leading-snug text-px-fg">{line}</p>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- SpokenCaption`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/SpokenCaption.tsx tests/components/interview/session/SpokenCaption.test.tsx
git commit -m "feat(session): spoken caption (latest AI line)"
```

---

### Task 5: InterviewSessionPanel (TDD — minimized by default, expand on tap)

**Files:**
- Create: `components/interview/session/InterviewSessionPanel.tsx`
- Test: `tests/components/interview/session/InterviewSessionPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { InterviewSessionPanel } from '@/components/interview/session/InterviewSessionPanel'
import type { RawMessage } from '@/components/interview/session/transcript-model'

const m = (id: string, isLocal: boolean, message: string): RawMessage => ({
  id, timestamp: Number(id), from: { isLocal }, message,
})

const messages = [m('1', false, 'Welcome — introduce yourself.'), m('2', true, 'I am John.')]

describe('InterviewSessionPanel', () => {
  it('is minimized by default: shows the title pill but not the transcript bubbles', () => {
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    expect(screen.getByText('Interview Session')).toBeInTheDocument()
    expect(screen.queryByText('I am John.')).not.toBeInTheDocument()
  })

  it('expands to show the conversation when the toggle is clicked', async () => {
    const user = userEvent.setup()
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    await user.click(screen.getByRole('button', { name: /open transcript/i }))
    expect(screen.getByText('Welcome — introduce yourself.')).toBeInTheDocument()
    expect(screen.getByText('I am John.')).toBeInTheDocument()
  })

  it('collapses again when the minimize button is clicked', async () => {
    const user = userEvent.setup()
    render(<InterviewSessionPanel messages={messages} agentState="listening" />)
    await user.click(screen.getByRole('button', { name: /open transcript/i }))
    await user.click(screen.getByRole('button', { name: /minimize transcript/i }))
    expect(screen.queryByText('I am John.')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- InterviewSessionPanel`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { useState } from 'react'
import { Minus } from 'lucide-react'
import type { AgentState } from '@livekit/components-react'

import { cn } from '@/lib/utils'
import { LiquidAura } from '@/components/agents-ui/liquid-aura'
import { toTurns, type RawMessage } from './transcript-model'

function LivePill() {
  return (
    <span className="flex items-center gap-1.5 rounded-full border border-px-ok-line bg-px-ok-bg px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-px-ok">
      <span className="size-1.5 rounded-full bg-px-ok" aria-hidden />
      Live
    </span>
  )
}

export function InterviewSessionPanel({
  messages,
  agentState,
  className,
}: {
  messages: RawMessage[]
  agentState?: AgentState
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const turns = toTurns(messages)

  if (!open) {
    return (
      <button
        type="button"
        aria-label="Open transcript"
        onClick={() => setOpen(true)}
        className={cn(
          'px-glass-pill flex items-center gap-2 px-3 py-2 text-px-fg transition-colors hover:bg-px-glass-bg-strong',
          className,
        )}
      >
        <LiquidAura state={agentState} audioTrack={undefined} size="mark" aria-hidden />
        <span className="font-serif text-sm italic">Interview Session</span>
        <LivePill />
      </button>
    )
  }

  return (
    <section
      aria-label="Interview Session transcript"
      className={cn('px-glass flex flex-col overflow-hidden rounded-2xl', className)}
    >
      <header className="flex items-center gap-2 border-b border-px-hairline px-3 py-2.5">
        <LiquidAura state={agentState} audioTrack={undefined} size="mark" aria-hidden />
        <span className="font-serif text-sm italic text-px-fg">Interview Session</span>
        <LivePill />
        <button
          type="button"
          aria-label="Minimize transcript"
          onClick={() => setOpen(false)}
          className="ml-auto grid size-6 place-items-center rounded-md border border-px-hairline text-px-fg-3 hover:text-px-fg"
        >
          <Minus className="size-3.5" />
        </button>
      </header>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-3">
        {turns.map((t) => (
          <div key={t.id} className={cn('flex', t.who === 'you' && 'justify-end')}>
            <div
              className={cn(
                'max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-snug',
                t.who === 'ai'
                  ? 'rounded-bl-sm bg-px-surface text-px-fg'
                  : 'rounded-br-sm border border-px-accent-line bg-px-accent-tint text-px-fg',
              )}
            >
              <div className="mb-0.5 text-[9px] font-bold uppercase tracking-wide opacity-60">
                {t.who === 'ai' ? 'Interviewer' : 'You (heard)'}
              </div>
              {t.text}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- InterviewSessionPanel`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/InterviewSessionPanel.tsx tests/components/interview/session/InterviewSessionPanel.test.tsx
git commit -m "feat(session): floating Interview Session transcript panel"
```

---

### Task 6: EndInterviewControl + confirmation dialog (TDD)

**Files:**
- Create: `components/interview/session/EndInterviewControl.tsx`
- Test: `tests/components/interview/session/EndInterviewControl.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EndInterviewControl } from '@/components/interview/session/EndInterviewControl'

describe('EndInterviewControl', () => {
  it('opens a confirmation dialog and only ends after confirm', async () => {
    const user = userEvent.setup()
    const onEnd = vi.fn()
    render(<EndInterviewControl onEnd={onEnd} />)

    await user.click(screen.getByRole('button', { name: /end interview/i }))
    expect(screen.getByText(/you won't be able to rejoin/i)).toBeInTheDocument()
    expect(onEnd).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /^end$/i }))
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('does not end when cancelled', async () => {
    const user = userEvent.setup()
    const onEnd = vi.fn()
    render(<EndInterviewControl onEnd={onEnd} />)
    await user.click(screen.getByRole('button', { name: /end interview/i }))
    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onEnd).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- EndInterviewControl`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { Dialog } from 'radix-ui'
import { PhoneOff } from 'lucide-react'

import { cn } from '@/lib/utils'

export function EndInterviewControl({
  onEnd,
  className,
}: {
  onEnd: () => void
  className?: string
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button
          type="button"
          aria-label="End interview"
          className={cn(
            'px-glass-pill flex items-center gap-2 px-3.5 py-2 text-xs font-semibold text-px-fg',
            'border-px-danger-line hover:bg-px-danger-bg hover:text-px-danger transition-colors',
            className,
          )}
        >
          <PhoneOff className="size-3.5" />
          <span className="hidden sm:inline">End interview</span>
          <span className="sm:hidden">End</span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className={cn(
            'px-glass-strong fixed left-1/2 top-1/2 z-[101] w-[min(420px,92vw)] -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-px-hairline p-6 text-center shadow-[var(--px-shadow-lg)]',
          )}
        >
          <Dialog.Title className="font-serif text-xl text-px-fg">End the interview?</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-px-fg-3">
            You won&apos;t be able to rejoin once the interview ends.
          </Dialog.Description>
          <div className="mt-6 flex justify-center gap-3">
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded-lg border border-px-hairline px-4 py-2 text-sm font-medium text-px-fg hover:bg-px-surface-2"
              >
                Cancel
              </button>
            </Dialog.Close>
            <Dialog.Close asChild>
              <button
                type="button"
                onClick={onEnd}
                className="rounded-lg bg-px-danger px-5 py-2 text-sm font-semibold text-white hover:opacity-90"
              >
                End
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- EndInterviewControl`
Expected: PASS. If radix portal content isn't found, ensure `tests/setup.ts` is loaded (it is, per vitest config) — radix renders into `document.body` which jsdom provides.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/EndInterviewControl.tsx tests/components/interview/session/EndInterviewControl.test.tsx
git commit -m "feat(session): End interview control + confirmation dialog"
```

---

### Task 7: SelfView, SessionTopBar, AuraStage (presentational)

Three small presentational components. SelfView shows the candidate camera (always on); falls back to a calm placeholder if no track.

**Files:**
- Create: `components/interview/session/SelfView.tsx`, `SessionTopBar.tsx`, `AuraStage.tsx`
- Test: `tests/components/interview/session/SessionTopBar.test.tsx`

- [ ] **Step 1: Create `SelfView.tsx`**

```tsx
'use client'

import { VideoTrack, useLocalParticipant } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { useMemo } from 'react'
import type { TrackReference } from '@livekit/components-react'

import { cn } from '@/lib/utils'

export function SelfView({ className }: { className?: string }) {
  const { localParticipant } = useLocalParticipant()
  const publication = localParticipant.getTrackPublication(Track.Source.Camera)
  const trackRef = useMemo<TrackReference | undefined>(
    () => (publication ? { source: Track.Source.Camera, participant: localParticipant, publication } : undefined),
    [publication, localParticipant],
  )
  const live = trackRef && !trackRef.publication.isMuted

  return (
    <div
      className={cn(
        'relative aspect-[4/3] w-[clamp(96px,18vw,176px)] overflow-hidden rounded-xl border border-px-hairline-strong bg-px-surface-2 shadow-[var(--px-shadow-md)]',
        className,
      )}
    >
      {live ? (
        <VideoTrack trackRef={trackRef} className="size-full object-cover" />
      ) : (
        <div className="grid size-full place-items-center text-[10px] text-px-fg-4">Camera starting…</div>
      )}
      <span className="absolute bottom-1.5 left-2 rounded-md bg-black/50 px-1.5 py-0.5 text-[9px] font-semibold text-white backdrop-blur-sm">
        You
      </span>
    </div>
  )
}
```

- [ ] **Step 2: Create `SessionTopBar.tsx`**

```tsx
'use client'

import { cn } from '@/lib/utils'
import { EndInterviewControl } from './EndInterviewControl'

export function SessionTopBar({
  companyName,
  jobTitle,
  logo,
  onEnd,
  className,
}: {
  companyName: string
  jobTitle: string
  logo?: string
  onEnd: () => void
  className?: string
}) {
  return (
    <header className={cn('flex items-center justify-between gap-3', className)}>
      <div className="flex items-center gap-2 text-xs font-semibold text-px-fg">
        {logo ? (
          <img src={logo} alt="" className="size-5 rounded-md" />
        ) : (
          <span className="grid size-5 place-items-center rounded-md bg-px-accent text-[10px] font-bold text-white">
            {companyName.slice(0, 1).toUpperCase()}
          </span>
        )}
        <span className="max-w-[40vw] truncate">
          {companyName} · {jobTitle}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="px-glass-pill flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-semibold text-px-fg-2">
          <span className="size-1.5 animate-pulse rounded-full bg-px-danger motion-reduce:animate-none" aria-hidden />
          Recording
        </span>
        <EndInterviewControl onEnd={onEnd} />
      </div>
    </header>
  )
}
```

- [ ] **Step 3: Create `AuraStage.tsx`**

```tsx
'use client'

import type { AgentState } from '@livekit/components-react'
import type { LocalAudioTrack, RemoteAudioTrack } from 'livekit-client'
import type { TrackReferenceOrPlaceholder } from '@livekit/components-react'

import { cn } from '@/lib/utils'
import { LiquidAura } from '@/components/agents-ui/liquid-aura'

const STATE_LABEL: Partial<Record<AgentState, string>> = {
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
}

export function AuraStage({
  state,
  audioTrack,
  className,
}: {
  state?: AgentState
  audioTrack?: LocalAudioTrack | RemoteAudioTrack | TrackReferenceOrPlaceholder
  className?: string
}) {
  const label = state ? STATE_LABEL[state] : undefined
  return (
    <div className={cn('flex flex-col items-center justify-center gap-4', className)}>
      <LiquidAura state={state} audioTrack={audioTrack} size="hero" />
      {label && <p className="text-xs tracking-wide text-px-accent-soft">{label}</p>}
    </div>
  )
}
```

- [ ] **Step 4: Write a smoke test for SessionTopBar**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionTopBar } from '@/components/interview/session/SessionTopBar'

describe('SessionTopBar', () => {
  it('shows company, role, recording indicator and an End control', () => {
    render(<SessionTopBar companyName="Acme" jobTitle="Senior Engineer" onEnd={vi.fn()} />)
    expect(screen.getByText(/Acme · Senior Engineer/)).toBeInTheDocument()
    expect(screen.getByText(/Recording/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /end interview/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 5: Run tests + type-check**

Run: `npm run test -- SessionTopBar && npm run type-check`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add components/interview/session/SelfView.tsx components/interview/session/SessionTopBar.tsx components/interview/session/AuraStage.tsx tests/components/interview/session/SessionTopBar.test.tsx
git commit -m "feat(session): self-view, top bar, aura stage"
```

---

### Task 8: `useEnsureMediaPublished` — mic + camera always on

> **HUMAN REVIEW REQUIRED** (root + session CLAUDE.md: "any change to … camera/mic step flow"). This enables the candidate's mic + camera on connect and is the mechanism that makes them non-toggleable (there is no toggle UI). It does not touch the pre-join `CameraMicStep` permission flow.

**Files:**
- Create: `components/interview/session/useEnsureMediaPublished.ts`
- Test: `tests/components/interview/session/useEnsureMediaPublished.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useEnsureMediaPublished } from '@/components/interview/session/useEnsureMediaPublished'

function makeRoom(connected: boolean) {
  return {
    state: connected ? 'connected' : 'disconnected',
    localParticipant: {
      setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
      setCameraEnabled: vi.fn().mockResolvedValue(undefined),
    },
  }
}

describe('useEnsureMediaPublished', () => {
  it('enables mic and camera once the room is connected', async () => {
    const room = makeRoom(true)
    renderHook(() => useEnsureMediaPublished(room as never))
    await waitFor(() => {
      expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true)
      expect(room.localParticipant.setCameraEnabled).toHaveBeenCalledWith(true)
    })
  })

  it('does nothing while disconnected', () => {
    const room = makeRoom(false)
    renderHook(() => useEnsureMediaPublished(room as never))
    expect(room.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled()
  })

  it('tolerates a missing room', () => {
    expect(() => renderHook(() => useEnsureMediaPublished(undefined))).not.toThrow()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- useEnsureMediaPublished`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { useEffect, useRef } from 'react'
import type { Room } from 'livekit-client'

/**
 * Mic + camera are always on for the candidate (no toggle UI). Once the room
 * is connected, enable both publications. Idempotent — runs at most once per
 * connect. Camera failure is swallowed (SelfView shows a placeholder); mic
 * failure is logged but does not crash the session. Audio constraints come
 * from room.options.audioCaptureDefaults (set in app.tsx from /start hints) —
 * do NOT pass capture options here.
 */
export function useEnsureMediaPublished(room: Room | undefined): void {
  const doneRef = useRef(false)

  useEffect(() => {
    if (!room || doneRef.current) return
    if (room.state !== 'connected') return
    doneRef.current = true
    void (async () => {
      try {
        await room.localParticipant.setMicrophoneEnabled(true)
      } catch (err) {
        console.warn('[interview] failed to enable microphone', err)
      }
      try {
        await room.localParticipant.setCameraEnabled(true)
      } catch {
        // SelfView renders a calm placeholder; do not surface an error.
      }
    })()
  }, [room, room?.state])
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- useEnsureMediaPublished`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/useEnsureMediaPublished.ts tests/components/interview/session/useEnsureMediaPublished.test.tsx
git commit -m "feat(session): enable mic + camera on connect (always-on)"
```

---

### Task 9: LiveInterview — compose the surface

**Files:**
- Create: `components/interview/session/LiveInterview.tsx`
- Test: `tests/components/interview/session/LiveInterview.test.tsx`

- [ ] **Step 1: Write the failing smoke test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => ({ room: { state: 'connected', localParticipant: {
    setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined),
    setCameraEnabled: vi.fn().mockResolvedValue(undefined),
    getTrackPublication: () => undefined,
  } } }),
  useSessionMessages: () => ({ messages: [{ id: '1', timestamp: 1, from: { isLocal: false }, message: 'Tell me about a project.' }] }),
  useVoiceAssistant: () => ({ state: 'speaking', audioTrack: undefined }),
  useLocalParticipant: () => ({ localParticipant: { getTrackPublication: () => undefined } }),
}))
vi.mock('@/components/interview/app/hooks/use-stage-progress', () => ({
  useStageProgress: () => ({ currentQuestion: 1, totalQuestions: 8, timeRemainingSeconds: 750 }),
}))

import { LiveInterview } from '@/components/interview/session/LiveInterview'

describe('LiveInterview', () => {
  it('renders the aura hero, progress, caption, panel pill and End control', () => {
    render(<LiveInterview companyName="Acme" jobTitle="Senior Engineer" onEnd={vi.fn()} />)
    expect(screen.getByRole('img', { name: /ai interviewer/i })).toBeInTheDocument()
    expect(screen.getByText(/Question 2 of 8/)).toBeInTheDocument()
    expect(screen.getByText('Tell me about a project.')).toBeInTheDocument() // caption
    expect(screen.getByText('Interview Session')).toBeInTheDocument()        // panel pill
    expect(screen.getByRole('button', { name: /end interview/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm run test -- LiveInterview`
Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { useSessionContext, useSessionMessages, useVoiceAssistant } from '@livekit/components-react'

import { AuraStage } from './AuraStage'
import { InterviewSessionPanel } from './InterviewSessionPanel'
import { ProgressChip } from './ProgressChip'
import { SelfView } from './SelfView'
import { SessionTopBar } from './SessionTopBar'
import { SpokenCaption } from './SpokenCaption'
import { useEnsureMediaPublished } from './useEnsureMediaPublished'
import type { RawMessage } from './transcript-model'

export interface LiveInterviewProps {
  companyName: string
  jobTitle: string
  logo?: string
  /** Tenant accent (CSS color); applied to --px-accent on the surface root. */
  accent?: string
  onEnd: () => void
}

export function LiveInterview({ companyName, jobTitle, logo, accent, onEnd }: LiveInterviewProps) {
  const session = useSessionContext()
  const { messages } = useSessionMessages(session)
  const { state, audioTrack } = useVoiceAssistant()
  useEnsureMediaPublished(session.room)

  const rawMessages = messages as unknown as RawMessage[]

  return (
    <div
      className="px-cine-bg fixed inset-0 overflow-hidden"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <SessionTopBar
        companyName={companyName}
        jobTitle={jobTitle}
        logo={logo}
        onEnd={onEnd}
        className="absolute inset-x-0 top-0 z-30 px-4 py-3"
      />

      <ProgressChip className="absolute left-1/2 top-16 z-20 -translate-x-1/2" />

      <div className="absolute inset-0 z-0 grid place-items-center">
        <AuraStage state={state} audioTrack={audioTrack} />
      </div>

      <SelfView className="absolute bottom-5 left-4 z-20" />

      <SpokenCaption
        messages={rawMessages}
        className="absolute bottom-6 left-1/2 z-20 -translate-x-1/2"
      />

      <InterviewSessionPanel
        messages={rawMessages}
        agentState={state}
        className="absolute right-4 top-16 z-30 max-h-[70vh] w-[min(360px,86vw)]"
      />
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm run test -- LiveInterview`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add components/interview/session/LiveInterview.tsx tests/components/interview/session/LiveInterview.test.tsx
git commit -m "feat(session): compose LiveInterview surface"
```

---

### Task 10: Wire `view-controller.tsx` to `LiveInterview`

Replace `ProgressBanner + AgentSessionView_01` with `LiveInterview`, keeping `AgentUIWithLoader` (intro gate) and `ReconnectingOverlay`. `onEnd` calls `session.end()` (via the existing disconnect path).

**Files:**
- Modify: `components/interview/app/view-controller.tsx`

- [ ] **Step 1: Replace the connected branch**

Change the imports block (lines 3-13) to:

```tsx
import { useSessionContext } from '@livekit/components-react'
import type { AppConfig } from '@/app-config'
import type { PreCheckResponse } from '@/lib/api/candidate-session'
import { AgentUIWithLoader } from '../agent-ui-with-loader'
import { LiveInterview } from '../session/LiveInterview'
import { CompletionScreen } from './CompletionScreen'
import { DisconnectError } from './DisconnectError'
import { ReconnectingOverlay } from './ReconnectingOverlay'
import { WelcomeView } from './welcome-view'
import { useAgentGraceTimeout } from './hooks/use-agent-grace-timeout'
```

Replace the connected `return (...)` block (lines 64-84) with:

```tsx
  return (
    <AgentUIWithLoader>
      <LiveInterview
        companyName={appConfig.companyName}
        jobTitle={preCheck.job_title}
        logo={appConfig.logo}
        accent={appConfig.accent}
        onEnd={() => ctx.end?.()}
      />
      <ReconnectingOverlay onTimeout={() => onError('RECONNECT_FAILED')} />
    </AgentUIWithLoader>
  )
```

Update the `ctx` cast (line 38) to also expose `end`:

```tsx
  const ctx = useSessionContext() as unknown as { isConnected?: boolean; end?: () => void }
```

- [ ] **Step 2: Delete the now-unused `ProgressBanner` import**

Confirm `ProgressBanner` and `AgentSessionView_01` are no longer imported in this file (they were removed in Step 1). Leave `components/interview/app/ProgressBanner.tsx` on disk for now (deleted in Task 12 cleanup).

- [ ] **Step 3: Type-check + test**

Run: `npm run type-check && npm run test`
Expected: no type errors; all tests pass.

- [ ] **Step 4: Commit**

```bash
git add components/interview/app/view-controller.tsx
git commit -m "feat(session): mount LiveInterview in the view controller"
```

---

### Task 11: End-wiring regression test (lock CLIENT_INITIATED → onCompleted)

Characterization test on the existing `OutcomeWatcher` (exported from `app.tsx`). It must keep routing a candidate-initiated disconnect (no engine outcome) to `onCompleted`.

**Files:**
- Create: `tests/components/interview/end-wiring.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// No engine outcome published → lastOutcome stays null.
vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => [],
}))

import { OutcomeWatcher } from '@/components/interview/app/app'

/** Minimal Room stub with a Disconnected event we can fire. */
function makeRoom() {
  const handlers: Record<string, ((...a: unknown[]) => void)[]> = {}
  return {
    on(evt: string, cb: (...a: unknown[]) => void) { (handlers[evt] ??= []).push(cb) },
    off(evt: string, cb: (...a: unknown[]) => void) {
      handlers[evt] = (handlers[evt] ?? []).filter((h) => h !== cb)
    },
    disconnect: vi.fn(),
    emit(evt: string, ...args: unknown[]) { (handlers[evt] ?? []).forEach((h) => h(...args)) },
  }
}

describe('OutcomeWatcher — End-interview wiring', () => {
  it('routes a CLIENT_INITIATED disconnect (no engine outcome) to onCompleted', () => {
    const room = makeRoom()
    const onCompleted = vi.fn()
    const onError = vi.fn()
    render(<OutcomeWatcher room={room as never} onCompleted={onCompleted} onError={onError} />)

    // DisconnectReason.CLIENT_INITIATED === 1 (proto enum)
    room.emit('disconnected', 1)

    expect(onCompleted).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify it passes**

Run: `npm run test -- end-wiring`
Expected: PASS. (Locks the existing behavior; if a future change breaks it, this fails.) If `RoomEvent.Disconnected` does not equal the string `'disconnected'` in the installed `livekit-client`, adjust the emitted event name to match `RoomEvent.Disconnected`'s value (import it in the test and use it as the key).

- [ ] **Step 3: Commit**

```bash
git add tests/components/interview/end-wiring.test.tsx
git commit -m "test(session): lock End-interview CLIENT_INITIATED → completion routing"
```

---

### Task 12: Restyle terminal + welcome screens to dark-cinematic

Replace the light (`bg-zinc-50`, `text-zinc-*`) styling with the dark-cinematic tokens. Behavior and props unchanged.

**Files:**
- Modify: `components/interview/app/CompletionScreen.tsx`, `DisconnectError.tsx`, `session-error-screen.tsx`, `ReconnectingOverlay.tsx`, `welcome-view.tsx`
- Delete: `components/interview/app/ProgressBanner.tsx` (replaced by `ProgressChip`)

- [ ] **Step 1: CompletionScreen.tsx — full replacement**

```tsx
'use client'

export function CompletionScreen() {
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">Thanks — your interview&apos;s complete.</h1>
        <p className="mt-3 text-sm text-px-fg-3">
          You can close this tab now. We&apos;ll be in touch with next steps soon.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: DisconnectError.tsx — replace only the returned JSX** (keep the `COPY` map untouched)

Replace the `return (...)` (lines 60-68) with:

```tsx
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-xl text-px-fg">{c.title}</h1>
        <p className="mt-2 text-sm text-px-fg-3">{c.body}</p>
        <p className="mt-6 font-mono text-xs text-px-fg-4">Error code: {code}</p>
      </div>
    </div>
  )
```

- [ ] **Step 3: session-error-screen.tsx — replace only the returned JSX**

Replace the `return (...)` (lines 27-43) with:

```tsx
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">{headline}</h1>
        <p className="mt-3 text-sm text-px-fg-3">{body}</p>
        <p className="mt-6 text-xs text-px-fg-4">
          You can close this window. If you need help, reach out to your recruiter and include this
          reference: <span className="font-mono">{sessionId}</span>.
        </p>
      </div>
    </div>
  )
```

- [ ] **Step 4: ReconnectingOverlay.tsx — replace only the returned JSX** (keep the hook logic, lines 1-40)

Replace the final `return (...)` (lines 42-53) with:

```tsx
  return (
    <div role="alert" className="fixed inset-0 z-50 grid place-items-center bg-black/55 backdrop-blur-sm">
      <div className="px-glass-strong rounded-2xl px-8 py-7 text-center">
        <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-4 border-px-hairline border-t-px-accent-soft motion-reduce:animate-none" />
        <p className="text-sm font-medium text-px-fg">Reconnecting…</p>
        <p className="mt-1 text-xs text-px-fg-4">Please don&apos;t close this tab.</p>
      </div>
    </div>
  )
```

- [ ] **Step 5: welcome-view.tsx — full replacement** (dark cinematic + faint aura; same props/behavior)

```tsx
'use client'

import { Button } from '@/components/ui/button'
import { LiquidAura } from '@/components/agents-ui/liquid-aura'

interface Props {
  companyName: string
  jobTitle: string
  durationMinutes: number
  startButtonText: string
  mode: 'start' | 'rejoin'
  onStartCall: () => void
  isPending?: boolean
}

export function WelcomeView({
  companyName,
  jobTitle,
  durationMinutes,
  startButtonText,
  mode,
  onStartCall,
  isPending = false,
}: Props) {
  const heading = mode === 'rejoin' ? 'Rejoin your interview' : "You're ready to begin"
  const body =
    mode === 'rejoin'
      ? 'You were disconnected. Click rejoin to continue where you left off.'
      : `${companyName} · ${jobTitle} · ${durationMinutes} minutes`
  const buttonLabel = isPending
    ? mode === 'rejoin' ? 'Rejoining…' : 'Starting…'
    : mode === 'rejoin' ? 'Rejoin interview' : startButtonText

  return (
    <section className="px-cine-bg grid min-h-screen place-items-center p-6">
      <div className="flex max-w-md flex-col items-center text-center">
        <LiquidAura state="listening" audioTrack={undefined} size="hero" className="mb-6 size-[200px]" />
        <h1 className="font-serif text-3xl text-px-fg">{heading}</h1>
        <p className="mt-3 text-sm text-px-fg-3">{body}</p>
        <Button
          size="lg"
          onClick={onStartCall}
          disabled={isPending}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          {buttonLabel}
        </Button>
      </div>
    </section>
  )
}
```

- [ ] **Step 6: Delete ProgressBanner**

```bash
git rm components/interview/app/ProgressBanner.tsx
```

Confirm nothing else imports it:

Run: `grep -rn "ProgressBanner" --include="*.tsx" --include="*.ts" . ; echo "exit:$?"`
Expected: no matches (exit:1 from grep). If a match remains, remove that import.

- [ ] **Step 7: Type-check + full test + build**

Run: `npm run type-check && npm run test && npm run build`
Expected: no type errors; all tests pass; build succeeds.

- [ ] **Step 8: Commit**

```bash
git add components/interview/app/CompletionScreen.tsx components/interview/app/DisconnectError.tsx components/interview/app/session-error-screen.tsx components/interview/app/ReconnectingOverlay.tsx components/interview/app/welcome-view.tsx
git commit -m "feat(session): restyle terminal + welcome screens to dark-cinematic"
```

---

### Task 13: Remove the dead AgentSessionView_01 / stock-aura code

Now that `LiveInterview` replaces the block, remove the unused enclave files (verify no imports first).

**Files:**
- Delete (after verification): `components/agents-ui/blocks/agent-session-view-01/` (whole dir), `components/agents-ui/agent-control-bar.tsx`, `components/agents-ui/agent-audio-visualizer-aura.tsx`, `components/agents-ui/react-shader-toy.tsx`

- [ ] **Step 1: Verify no remaining imports**

Run:
```bash
grep -rn "agent-session-view-01\|agent-control-bar\|agent-audio-visualizer-aura\|react-shader-toy" --include="*.ts" --include="*.tsx" . | grep -v node_modules
echo "exit:$?"
```
Expected: no matches outside the files themselves. If `agent-control-bar` still appears only inside `agent-session-view-01` (which we're deleting), that's fine.

- [ ] **Step 2: Delete the dead files**

```bash
git rm -r components/agents-ui/blocks/agent-session-view-01
git rm components/agents-ui/agent-control-bar.tsx components/agents-ui/agent-audio-visualizer-aura.tsx components/agents-ui/react-shader-toy.tsx
```

> If any of `agent-track-control.tsx`, `agent-track-toggle.tsx`, `agent-chat-transcript.tsx`, `hooks/agents-ui/use-agent-control-bar.ts`, or the unused `agent-audio-visualizer-{grid,radial,wave}.tsx` become orphaned, delete them too only after a clean `grep` shows zero importers. Keep `agent-audio-visualizer-bar.tsx` only if still referenced; otherwise remove it. Do not delete `agent-session-provider.tsx`, `start-audio-button.tsx`, or `liquid-aura.tsx` — those are still used.

- [ ] **Step 3: Type-check + build + test**

Run: `npm run type-check && npm run build && npm run test`
Expected: all green. If type-check flags a missing import, restore that file (it was still in use) and re-grep.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(session): remove dead AgentSessionView_01 + stock aura enclave"
```

---

## Self-review notes (verified while writing)

- **Spec coverage:** aura hero (AuraStage + LiquidAura), self-view always-on (SelfView + useEnsureMediaPublished), Interview Session panel minimized-by-default (InterviewSessionPanel), top bar with Recording + sole End control (SessionTopBar + EndInterviewControl), progress chip from room attributes (ProgressChip + use-stage-progress), spoken caption (SpokenCaption), End-wiring via existing CLIENT_INITIATED branch (Task 11 regression), removal of mic/camera/screen-share/keyboard-chat (Task 13 deletes the control bar). Mobile + a11y polish is **Plan 3** scope.
- **Type consistency:** `LiquidAura` props match Plan 1. `useStageProgress()` returns `{ currentQuestion, totalQuestions, timeRemainingSeconds }` (verified). `useSessionMessages(session)` → `{ messages }` with `{ id, timestamp, from.isLocal, message }` (verified in `agent-session-block.tsx`/`agent-chat-transcript.tsx`). `session.end` / `session.room` / `session.isConnected` exist on the `useSession`/`useSessionContext` return (verified in `app.tsx`/`agent-disconnect-button.tsx`).
- **Constraints:** no Supabase, no API/security changes, voice-only (no chat input), camera/mic non-toggleable (no toggle UI; always-on hook flagged human-review), audio constraints untouched (capture defaults set in `app.tsx`).
- **Open risk noted in-task:** `RoomEvent.Disconnected` string value (Task 11) — instruction included to use the enum value if `'disconnected'` doesn't match.

## Notes for Plan 3

- `LiveInterview` reads `appConfig.accent` and applies `--px-accent` to its root; the wizard (Plan 3) should do the same on its frame so the whole journey honors the tenant accent.
- The dark-cinematic restyle pattern (`.px-cine-bg` + `.px-glass` card) used here is the template for the wizard frame and steps.
