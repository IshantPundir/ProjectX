# LiveKit Frontend Template Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port LiveKit's `agent-starter-react` template into ProjectX's candidate interview surface, plus add graceful disconnect signalling and mid-session rejoin so the candidate UI is correct end-to-end (no `<AlreadyStartedPanel>` dead-end, no false-`completed` outcomes).

**Architecture:**
- **Frontend port** — `app/(interview)/`-only shadcn enclave at `components/{ui,agents-ui,ai-elements}/` co-existing with `components/px/`. The starter's `<AgentSessionProvider>` + `useSession(TokenSource.custom(…))` model replaces the old `<LiveKitRoom>` wrapper. `WizardShell` hands off after cam-mic to the new `<App mode='start'|'rejoin'>` entry point.
- **Backend rejoin** — new `POST /api/candidate-session/{token}/rejoin` mints a fresh LiveKit JWT for an `active` session without re-dispatching the engine.
- **Engine graceful close** — engine publishes `session_outcome` on its participant's attributes before shutdown; frontend reads this to route between `CompletionScreen` and `DisconnectError`.

**Tech Stack:** Next.js 16, React 19, TypeScript strict, Tailwind v4, `@livekit/components-react@2.9.20`, `livekit-client@2.18.8`, shadcn (registry `@agents-ui` + `@ai-elements` + base `@shadcn`), `motion`, `next-themes`, `class-variance-authority`, `@radix-ui/*`, FastAPI 3.12, asyncpg, livekit-agents (Python).

**Branch:** `feat/phase-3c2-interview-engine` — **no new branch / worktree**. Iterating in place per user instruction.

**Spec:** `docs/superpowers/specs/2026-04-30-livekit-frontend-template-port-design.md`

---

## Pre-flight

- [ ] **Read the spec.** Implementation must conform to the design doc. The plan below references it for the "why" — when in doubt, the spec wins.
- [ ] **Confirm clean working tree.** Run `git status` — should show `feat/phase-3c2-interview-engine`, no uncommitted changes besides the spec/plan docs.
- [ ] **Confirm backend services run.** From `backend/nexus/`: `docker compose up --build`. The startup `_assert_rls_completeness` check should pass.
- [ ] **Confirm frontend builds clean.** From `frontend/app/`: `npm run build`. Existing baseline must pass before we start.

---

## Phase 1 — Frontend port (the LiveKit template lands)

End-of-phase verification: candidate completes wizard → cam-mic → sees the new LiveKit-style welcome view → click Start → connects → `<AgentSessionView_01>` renders → speak → engine ends session → `<CompletionScreen>` renders. No engine code changed yet (so any disconnect routes to `CompletionScreen` exactly like today — no regression).

---

### Task 1.1: Install missing deps

**Files:**
- Modify: `frontend/app/package.json`
- Modify: `frontend/app/package-lock.json`

- [ ] **Step 1: Install Tailwind/shadcn-adjacent deps**

```bash
cd frontend/app && npm install \
  motion \
  class-variance-authority \
  next-themes \
  tw-animate-css \
  @phosphor-icons/react
```

- [ ] **Step 2: Install Radix primitives required transitively by `@agents-ui`**

```bash
cd frontend/app && npm install \
  @radix-ui/react-collapsible \
  @radix-ui/react-dialog \
  @radix-ui/react-dropdown-menu \
  @radix-ui/react-hover-card \
  @radix-ui/react-popover \
  @radix-ui/react-progress \
  @radix-ui/react-scroll-area \
  @radix-ui/react-select \
  @radix-ui/react-separator \
  @radix-ui/react-slot \
  @radix-ui/react-toggle \
  @radix-ui/react-tooltip \
  @radix-ui/react-use-controllable-state
```

- [ ] **Step 3: Install AI Elements + chat utilities**

```bash
cd frontend/app && npm install ai cmdk streamdown media-chrome embla-carousel-react
```

- [ ] **Step 4: Install shadcn CLI as a dev dep (used at install-time, not runtime)**

```bash
cd frontend/app && npm install -D shadcn
```

- [ ] **Step 5: Verify the project still builds**

Run from `frontend/app/`: `npm run build`
Expected: PASS — clean build, no other route broken.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/package.json frontend/app/package-lock.json
git commit -m "chore(frontend): install agents-ui shadcn deps"
```

---

### Task 1.2: Add shadcn scaffolding

**Files:**
- Create: `frontend/app/components.json`
- Create: `frontend/app/lib/shadcn/utils.ts`

- [ ] **Step 1: Create `components.json` (mirrors the starter's, paths adjusted to our layout)**

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "app/globals.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "iconLibrary": "lucide",
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/shadcn/utils",
    "ui": "@/components/ui",
    "lib": "@/lib/shadcn",
    "hooks": "@/hooks"
  },
  "registries": {
    "@agents-ui": "https://livekit.io/ui/r/{name}.json",
    "@ai-elements": "https://registry.ai-sdk.dev/{name}.json"
  }
}
```

- [ ] **Step 2: Create the `cn()` helper at `frontend/app/lib/shadcn/utils.ts`**

```ts
import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

- [ ] **Step 3: Verify TypeScript still compiles**

Run from `frontend/app/`: `npm run type-check` (or `npx tsc --noEmit`)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components.json frontend/app/lib/shadcn/utils.ts
git commit -m "chore(frontend): add shadcn scaffolding (components.json + cn helper)"
```

---

### Task 1.3: Run shadcn install for agents-ui + ai-elements

**Files:**
- Create (via CLI): `frontend/app/components/ui/*` — base shadcn primitives (button, toggle, select, etc.)
- Create (via CLI): `frontend/app/components/agents-ui/*` — the Agents UI components
- Create (via CLI): `frontend/app/components/agents-ui/blocks/agent-session-view-01.tsx`
- Create (via CLI): `frontend/app/components/ai-elements/{conversation,message}.tsx`
- Create (via CLI): `frontend/app/hooks/agents-ui/*`

- [ ] **Step 1: Run the shadcn install in non-interactive overwrite mode**

```bash
cd frontend/app && npx shadcn@latest add --yes --overwrite \
  @agents-ui/agent-audio-visualizer-bar \
  @agents-ui/agent-audio-visualizer-grid \
  @agents-ui/agent-audio-visualizer-radial \
  @agents-ui/agent-audio-visualizer-wave \
  @agents-ui/agent-audio-visualizer-aura \
  @agents-ui/agent-control-bar \
  @agents-ui/agent-session-provider \
  @agents-ui/agent-track-control \
  @agents-ui/agent-track-toggle \
  @agents-ui/agent-chat-transcript \
  @agents-ui/agent-chat-indicator \
  @agents-ui/agent-disconnect-button \
  @agents-ui/agent-session-view-01 \
  @agents-ui/start-audio-button \
  @ai-elements/conversation \
  @ai-elements/message
```

> **Important:** do NOT install `@agents-ui/nextjs-api-token-route` — we use `TokenSource.custom`, not the `/api/token` endpoint pattern.

- [ ] **Step 2: Verify the install populated the expected directories**

```bash
ls frontend/app/components/agents-ui/ | head
ls frontend/app/components/agents-ui/blocks/
ls frontend/app/components/ui/
ls frontend/app/components/ai-elements/
ls frontend/app/hooks/agents-ui/
```

Expected: `agent-control-bar.tsx`, `agent-session-provider.tsx`, `agent-disconnect-button.tsx`, etc., visible. `blocks/agent-session-view-01.tsx` exists. `components/ui/button.tsx`, `toggle.tsx`, `select.tsx`, etc. exist. `components/ai-elements/conversation.tsx`, `message.tsx` exist. `hooks/agents-ui/use-agent-control-bar.ts` exists.

- [ ] **Step 3: Build to surface any registry-introduced TS errors early**

Run: `npm run build`
Expected: PASS. If any installed file has a syntax/import error, fix that file *only* — do not edit any of our pre-existing files.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/ui frontend/app/components/agents-ui frontend/app/components/ai-elements frontend/app/hooks/agents-ui
git commit -m "chore(frontend): install @agents-ui + @ai-elements registry components"
```

---

### Task 1.4: jsdom stubs for tests

`@radix-ui/*` and `motion/react` need `ResizeObserver`, `IntersectionObserver`, and `matchMedia` in jsdom. Without these, every test that mounts a component using a Radix primitive crashes.

**Files:**
- Modify: `frontend/app/tests/setup.ts`

- [ ] **Step 1: Read the current setup file to see what's there**

Read `frontend/app/tests/setup.ts`. Note the existing localStorage stub.

- [ ] **Step 2: Add the three browser API stubs at the bottom of the file**

```ts
// jsdom doesn't implement these; Radix + motion/react require them.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}

if (typeof globalThis.IntersectionObserver === 'undefined') {
  globalThis.IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return [] }
    root = null
    rootMargin = ''
    thresholds = []
  } as unknown as typeof IntersectionObserver
}

if (typeof globalThis.matchMedia === 'undefined') {
  globalThis.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList
}
```

- [ ] **Step 3: Run existing tests to confirm no regression**

Run: `npm run test`
Expected: PASS — same green count as before.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/tests/setup.ts
git commit -m "test(frontend): stub ResizeObserver / IntersectionObserver / matchMedia for radix"
```

---

### Task 1.4b: Add `candidateSessionApi.rejoin` client method

The `App` component built in Task 1.14 references this method, so it must exist in Phase 1 even though the backend endpoint lands in Phase 2. Until Phase 2 ships, calling it returns a 404 — that's fine, the call only fires when `mode='rejoin'`, which the WizardShell only triggers on `state='active'` sessions, which won't exist before Phase 2 anyway.

**Files:**
- Modify: `frontend/app/lib/api/candidate-session.ts`

- [ ] **Step 1: Append `rejoin` to the api object**

```ts
export const candidateSessionApi = {
  // ... existing methods unchanged ...
  rejoin: (token: string) =>
    _call<StartSessionResponse>('POST', `/api/candidate-session/${token}/rejoin`),
}
```

- [ ] **Step 2: Build to confirm**

Run: `cd frontend/app && npm run type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/api/candidate-session.ts
git commit -m "feat(api): add candidateSessionApi.rejoin client method"
```

---

### Task 1.5: Create app-config.ts

**Files:**
- Create: `frontend/app/app-config.ts`

- [ ] **Step 1: Port the starter's AppConfig interface + defaults**

```ts
export interface AppConfig {
  pageTitle: string
  pageDescription: string
  companyName: string

  supportsChatInput: boolean
  supportsVideoInput: boolean
  supportsScreenShare: boolean
  isPreConnectBufferEnabled: boolean

  logo: string
  startButtonText: string
  accent?: string
  logoDark?: string
  accentDark?: string

  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura'
  audioVisualizerColor?: `#${string}`
  audioVisualizerColorDark?: `#${string}`
  audioVisualizerColorShift?: number
  audioVisualizerBarCount?: number
  audioVisualizerGridRowCount?: number
  audioVisualizerGridColumnCount?: number
  audioVisualizerRadialBarCount?: number
  audioVisualizerRadialRadius?: number
  audioVisualizerWaveLineWidth?: number

  agentName?: string
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'ProjectX',
  pageTitle: 'ProjectX · Interview',
  pageDescription: 'AI-led interview',
  supportsChatInput: true,
  supportsVideoInput: true,
  supportsScreenShare: true,
  isPreConnectBufferEnabled: true,
  logo: '/projectx-logo.svg',
  startButtonText: 'Start interview',
  accent: '#0E6F63',
  audioVisualizerType: 'bar',
  agentName: undefined,
}
```

- [ ] **Step 2: Add a placeholder logo if the path doesn't exist yet**

```bash
ls frontend/app/public/projectx-logo.svg 2>/dev/null || \
  echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#0E6F63"><circle cx="12" cy="12" r="10"/></svg>' \
  > frontend/app/public/projectx-logo.svg
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/app-config.ts frontend/app/public/projectx-logo.svg
git commit -m "feat(interview): add app-config with ProjectX defaults"
```

---

### Task 1.6: useSessionOutcome hook (graceful-vs-error router)

**Files:**
- Create: `frontend/app/components/interview/app/hooks/use-session-outcome.ts`
- Create: `frontend/app/tests/components/interview/use-session-outcome.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const remoteParticipantsMock = vi.fn()

vi.mock('@livekit/components-react', () => ({
  useRemoteParticipants: () => remoteParticipantsMock(),
}))

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'

function Probe({ onChange }: { onChange: (v: string | null) => void }) {
  const v = useSessionOutcome()
  onChange(v)
  return null
}

describe('useSessionOutcome', () => {
  it('captures the latest session_outcome attribute and returns it after the participant disappears', () => {
    remoteParticipantsMock.mockReturnValue([
      {
        identity: 'agent-stub',
        attributes: { session_outcome: 'completed' },
      },
    ])
    let captured: string | null = null
    const { rerender } = render(<Probe onChange={(v) => { captured = v }} />)
    expect(captured).toBe('completed')

    // Agent disappears (simulating disconnect mid-frame).
    remoteParticipantsMock.mockReturnValue([])
    rerender(<Probe onChange={(v) => { captured = v }} />)
    // Hook still returns 'completed' from its ref.
    expect(captured).toBe('completed')
  })

  it('returns null when no agent is present and none ever was', () => {
    remoteParticipantsMock.mockReturnValue([])
    let captured: string | null = 'init'
    render(<Probe onChange={(v) => { captured = v }} />)
    expect(captured).toBeNull()
  })
})
```

- [ ] **Step 2: Run the test, expect it to fail (file missing)**

Run: `npm run test -- use-session-outcome`
Expected: FAIL — `Cannot find module '@/components/interview/app/hooks/use-session-outcome'`.

- [ ] **Step 3: Implement the hook**

```ts
'use client'

import { useEffect, useRef } from 'react'
import { useRemoteParticipants } from '@livekit/components-react'

/**
 * Reads the agent participant's `session_outcome` attribute and holds it in a ref
 * so the value survives the moment the agent participant is removed from the
 * remote participants list (which happens immediately on Disconnected).
 *
 * Engine writes 'completed' or 'error' before calling shutdown; see
 * docs/superpowers/specs/2026-04-30-livekit-frontend-template-port-design.md
 * § "Graceful disconnect signal".
 */
export function useSessionOutcome(): string | null {
  const remotes = useRemoteParticipants()
  const ref = useRef<string | null>(null)

  useEffect(() => {
    const agent = remotes.find((p) => p.identity.startsWith('agent-'))
    const outcome = agent?.attributes?.['session_outcome']
    if (outcome) ref.current = outcome
  }, [remotes])

  return ref.current
}
```

- [ ] **Step 4: Run the test, expect it to pass**

Run: `npm run test -- use-session-outcome`
Expected: PASS, two tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/interview/app/hooks/use-session-outcome.ts \
        frontend/app/tests/components/interview/use-session-outcome.test.tsx
git commit -m "feat(interview): add useSessionOutcome hook for graceful disconnect routing"
```

---

### Task 1.7: Move ProgressBanner

**Files:**
- Move: `frontend/app/app/(interview)/interview/[token]/LiveSession/ProgressBanner.tsx` → `frontend/app/components/interview/app/ProgressBanner.tsx`
- Move: `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/use-stage-progress.ts` → `frontend/app/components/interview/app/hooks/use-stage-progress.ts`
- Modify: `frontend/app/tests/components/interview/ProgressBanner.test.tsx` — update import path

- [ ] **Step 1: Move both files via git mv**

```bash
cd /home/ishant/Projects/ProjectX
git mv frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/ProgressBanner.tsx \
       frontend/app/components/interview/app/ProgressBanner.tsx
git mv frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/hooks/use-stage-progress.ts \
       frontend/app/components/interview/app/hooks/use-stage-progress.ts
```

- [ ] **Step 2: Update the import inside `ProgressBanner.tsx`**

The file imports `./hooks/use-stage-progress`. After move that's `./hooks/use-stage-progress` (same relative path), no change needed. Verify by reading the file.

- [ ] **Step 3: Update the test file's import**

In `frontend/app/tests/components/interview/ProgressBanner.test.tsx`:

```ts
// before
import { ProgressBanner } from '@/app/(interview)/interview/[token]/LiveSession/ProgressBanner'
// after
import { ProgressBanner } from '@/components/interview/app/ProgressBanner'
```

- [ ] **Step 4: Run the test to confirm it still passes**

Run: `npm run test -- ProgressBanner`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(interview): move ProgressBanner + useStageProgress into components/interview/app/"
```

---

### Task 1.8: Move CompletionScreen

**Files:**
- Move: `frontend/app/app/(interview)/interview/[token]/LiveSession/CompletionScreen.tsx` → `frontend/app/components/interview/app/CompletionScreen.tsx`
- Modify: `frontend/app/tests/components/interview/CompletionScreen.test.tsx` — update import path

- [ ] **Step 1: Move via git mv**

```bash
git mv frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/CompletionScreen.tsx \
       frontend/app/components/interview/app/CompletionScreen.tsx
```

- [ ] **Step 2: Update the test import**

In `frontend/app/tests/components/interview/CompletionScreen.test.tsx`:

```ts
import { CompletionScreen } from '@/components/interview/app/CompletionScreen'
```

- [ ] **Step 3: Run test**

Run: `npm run test -- CompletionScreen`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(interview): move CompletionScreen into components/interview/app/"
```

---

### Task 1.9: Move + extend DisconnectError

**Files:**
- Move: `frontend/app/app/(interview)/interview/[token]/LiveSession/DisconnectError.tsx` → `frontend/app/components/interview/app/DisconnectError.tsx`
- Modify: extend the COPY map with all error codes the spec defines

- [ ] **Step 1: Move via git mv**

```bash
git mv frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/DisconnectError.tsx \
       frontend/app/components/interview/app/DisconnectError.tsx
```

- [ ] **Step 2: Extend the COPY map**

Replace the existing `COPY` constant with:

```tsx
const COPY: Record<string, { title: string; body: string }> = {
  AGENT_NO_SHOW: {
    title: "Interviewer didn't connect",
    body: "We couldn't reach the interviewer. Please try again later or contact your recruiter.",
  },
  MEDIA_LOST: {
    title: 'Camera or microphone unavailable',
    body: 'Your camera or microphone is no longer accessible. Please reconnect to continue.',
  },
  SESSION_ALREADY_STARTED: {
    title: 'This session has already started',
    body: "You've already started this interview. If you were disconnected, please contact your recruiter.",
  },
  SESSION_START_FAILED: {
    title: 'Could not start the interview',
    body: 'Something went wrong starting your interview. Please refresh and try again.',
  },
  ENGINE_ERROR: {
    title: 'The interviewer encountered an error',
    body: 'Your interview was interrupted. Please contact your recruiter.',
  },
  UNEXPECTED_DISCONNECT: {
    title: 'Connection lost',
    body: "We lost the connection unexpectedly. Please contact your recruiter if this persists.",
  },
  RECONNECT_FAILED: {
    title: 'Connection lost',
    body: "We tried to reconnect but couldn't. Please contact your recruiter.",
  },
  SESSION_ALREADY_COMPLETED: {
    title: 'This interview is already complete',
    body: 'Your interview has already finished. Thank you — we will be in touch.',
  },
  DUPLICATE_SESSION: {
    title: 'Disconnected — another tab took over',
    body: 'Your interview is now running in another browser tab. Close other tabs and try again from there.',
  },
  TOKEN_EXPIRED: {
    title: 'This invite link has expired',
    body: 'Your invite link is no longer valid. Please contact your recruiter for a new link.',
  },
  REJOIN_RATE_LIMITED: {
    title: 'Too many rejoin attempts',
    body: "You've tried to rejoin too many times in a short window. Please wait a few minutes and try again.",
  },
  REJOIN_REJECTED: {
    title: 'Could not rejoin the interview',
    body: 'We could not rejoin your interview. Please contact your recruiter.',
  },
}
```

- [ ] **Step 3: Run the existing CompletionScreen / ProgressBanner tests to confirm no regression**

Run: `npm run test`
Expected: same green count as before (the tests don't touch DisconnectError directly).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(interview): move DisconnectError + extend error COPY map"
```

---

### Task 1.10: Move use-agent-grace-timeout hook

**Files:**
- Move: `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/use-agent-grace-timeout.ts` → `frontend/app/components/interview/app/hooks/use-agent-grace-timeout.ts`

- [ ] **Step 1: Move**

```bash
git mv frontend/app/app/\(interview\)/interview/\[token\]/LiveSession/hooks/use-agent-grace-timeout.ts \
       frontend/app/components/interview/app/hooks/use-agent-grace-timeout.ts
```

- [ ] **Step 2: Confirm the file's imports still resolve**

Read the file. The only import is `useRemoteParticipants` from `@livekit/components-react` — unaffected by the move.

- [ ] **Step 3: Build to confirm**

Run: `npm run type-check`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(interview): move use-agent-grace-timeout hook"
```

---

### Task 1.11: ReconnectingOverlay component

**Files:**
- Create: `frontend/app/components/interview/app/ReconnectingOverlay.tsx`
- Create: `frontend/app/tests/components/interview/ReconnectingOverlay.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const useSessionContextMock = vi.fn()

vi.mock('@livekit/components-react', () => ({
  useSessionContext: () => useSessionContextMock(),
}))

import { ReconnectingOverlay } from '@/components/interview/app/ReconnectingOverlay'

describe('ReconnectingOverlay', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })

  it('renders when reconnecting and clears when reconnected', () => {
    useSessionContextMock.mockReturnValue({ state: 'reconnecting' })
    const { rerender } = render(<ReconnectingOverlay onTimeout={() => {}} />)
    expect(screen.getByText(/Reconnecting/i)).toBeInTheDocument()

    useSessionContextMock.mockReturnValue({ state: 'connected' })
    rerender(<ReconnectingOverlay onTimeout={() => {}} />)
    expect(screen.queryByText(/Reconnecting/i)).toBeNull()
  })

  it('fires onTimeout after 30 seconds of reconnecting', () => {
    useSessionContextMock.mockReturnValue({ state: 'reconnecting' })
    const onTimeout = vi.fn()
    render(<ReconnectingOverlay onTimeout={onTimeout} />)
    act(() => { vi.advanceTimersByTime(30_000) })
    expect(onTimeout).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

Run: `npm run test -- ReconnectingOverlay`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
'use client'

import { useEffect, useRef } from 'react'
import { useSessionContext } from '@livekit/components-react'

interface Props {
  onTimeout: () => void
  timeoutMs?: number
}

/**
 * Renders a dimming overlay while the LiveKit session is in 'reconnecting' state.
 * Fires onTimeout after timeoutMs (default 30s) of continuous reconnect, so the
 * caller can route to DisconnectError with code RECONNECT_FAILED.
 *
 * Outcome state is owned by the parent <App>; this component is purely UI + a
 * timer. The state shape returned by useSessionContext is verified at runtime
 * — we read the literal string 'reconnecting' loosely.
 */
export function ReconnectingOverlay({ onTimeout, timeoutMs = 30_000 }: Props) {
  const ctx = useSessionContext() as unknown as { state?: string }
  const isReconnecting = ctx?.state === 'reconnecting'
  const firedRef = useRef(false)

  useEffect(() => {
    if (!isReconnecting) {
      firedRef.current = false
      return
    }
    const t = setTimeout(() => {
      if (firedRef.current) return
      firedRef.current = true
      onTimeout()
    }, timeoutMs)
    return () => clearTimeout(t)
  }, [isReconnecting, timeoutMs, onTimeout])

  if (!isReconnecting) return null

  return (
    <div
      role="alert"
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 backdrop-blur-sm"
    >
      <div className="rounded-xl bg-white p-8 text-center shadow-lg">
        <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-4 border-zinc-300 border-t-zinc-900" />
        <p className="text-sm font-medium text-zinc-900">Reconnecting…</p>
        <p className="mt-1 text-xs text-zinc-500">Please don&apos;t close this tab.</p>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run, expect PASS**

Run: `npm run test -- ReconnectingOverlay`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/interview/app/ReconnectingOverlay.tsx \
        frontend/app/tests/components/interview/ReconnectingOverlay.test.tsx
git commit -m "feat(interview): add ReconnectingOverlay for transient drops"
```

---

### Task 1.12: WelcomeView component

**Files:**
- Create: `frontend/app/components/interview/app/welcome-view.tsx`

- [ ] **Step 1: Implement**

```tsx
'use client'

import { Button } from '@/components/ui/button'

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
  const heading =
    mode === 'rejoin' ? 'Rejoin your interview' : "You're ready to begin"

  const body =
    mode === 'rejoin'
      ? 'You were disconnected. Click rejoin to continue where you left off.'
      : `${companyName} · ${jobTitle} · ${durationMinutes} minutes`

  return (
    <section className="grid min-h-screen place-items-center bg-background p-6">
      <div className="max-w-md text-center">
        <h1 className="text-3xl font-semibold text-foreground">{heading}</h1>
        <p className="mt-3 text-sm text-muted-foreground">{body}</p>
        <Button
          size="lg"
          onClick={onStartCall}
          disabled={isPending}
          className="mt-8 w-64 rounded-full font-mono text-xs font-bold uppercase tracking-wider"
        >
          {isPending
            ? mode === 'rejoin'
              ? 'Rejoining…'
              : 'Starting…'
            : mode === 'rejoin'
              ? 'Rejoin interview'
              : startButtonText}
        </Button>
      </div>
    </section>
  )
}
```

- [ ] **Step 2: Build to confirm**

Run: `npm run type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/interview/app/welcome-view.tsx
git commit -m "feat(interview): add WelcomeView with start + rejoin modes"
```

---

### Task 1.13: ViewController component

**Files:**
- Create: `frontend/app/components/interview/app/view-controller.tsx`

The view controller branches on outcome + connection state, and overlays `ProgressBanner`, `useAgentGraceTimeout`, and `ReconnectingOverlay`.

- [ ] **Step 1: Implement**

```tsx
'use client'

import { useSessionContext } from '@livekit/components-react'
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01'
import type { AppConfig } from '@/app-config'
import type { PreCheckResponse } from '@/lib/api/candidate-session'
import { CompletionScreen } from './CompletionScreen'
import { DisconnectError } from './DisconnectError'
import { ProgressBanner } from './ProgressBanner'
import { ReconnectingOverlay } from './ReconnectingOverlay'
import { WelcomeView } from './welcome-view'
import { useAgentGraceTimeout } from './hooks/use-agent-grace-timeout'

export type Outcome = 'live' | 'completed' | 'error'

interface Props {
  appConfig: AppConfig
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
  outcome: Outcome
  errorCode: string | null
  isStartPending: boolean
  onStart: () => void
  onError: (code: string) => void
}

export function ViewController({
  appConfig,
  preCheck,
  mode,
  outcome,
  errorCode,
  isStartPending,
  onStart,
  onError,
}: Props) {
  const ctx = useSessionContext() as unknown as { isConnected?: boolean }
  const isConnected = !!ctx?.isConnected

  // 30s no-show timer — only meaningful when connected.
  useAgentGraceTimeout(() => onError('AGENT_NO_SHOW'), { graceMs: 30_000 })

  if (outcome === 'completed') return <CompletionScreen />
  if (outcome === 'error' && errorCode) {
    return <DisconnectError code={errorCode} />
  }

  if (!isConnected) {
    return (
      <WelcomeView
        companyName={appConfig.companyName}
        jobTitle={preCheck.job_title}
        durationMinutes={preCheck.duration_minutes}
        startButtonText={appConfig.startButtonText}
        mode={mode}
        onStartCall={onStart}
        isPending={isStartPending}
      />
    )
  }

  return (
    <>
      <ProgressBanner />
      <AgentSessionView_01
        supportsChatInput={appConfig.supportsChatInput}
        supportsVideoInput={appConfig.supportsVideoInput}
        supportsScreenShare={appConfig.supportsScreenShare}
        isPreConnectBufferEnabled={appConfig.isPreConnectBufferEnabled}
        audioVisualizerType={appConfig.audioVisualizerType}
        audioVisualizerColor={appConfig.audioVisualizerColor}
        audioVisualizerBarCount={appConfig.audioVisualizerBarCount}
        audioVisualizerGridRowCount={appConfig.audioVisualizerGridRowCount}
        audioVisualizerGridColumnCount={appConfig.audioVisualizerGridColumnCount}
        audioVisualizerRadialBarCount={appConfig.audioVisualizerRadialBarCount}
        audioVisualizerRadialRadius={appConfig.audioVisualizerRadialRadius}
        audioVisualizerWaveLineWidth={appConfig.audioVisualizerWaveLineWidth}
        saveUserChoices={false}
        className="fixed inset-0"
      />
      <ReconnectingOverlay onTimeout={() => onError('RECONNECT_FAILED')} />
    </>
  )
}
```

> **Note:** `AgentSessionView_01`'s exact prop names should be verified against `frontend/app/components/agents-ui/blocks/agent-session-view-01.tsx` after Task 1.3 installs it. Adjust if the registry's prop names differ slightly. The ones above match the starter's `view-controller.tsx`.

- [ ] **Step 2: Build to confirm**

Run: `npm run type-check`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/interview/app/view-controller.tsx
git commit -m "feat(interview): add ViewController orchestrating welcome / session / outcome"
```

---

### Task 1.14: App entry point with TokenSource.custom

**Files:**
- Create: `frontend/app/components/interview/app/app.tsx`

- [ ] **Step 1: Implement**

```tsx
'use client'

import { ThemeProvider } from 'next-themes'
import { TokenSource } from 'livekit-client'
import { useSession } from '@livekit/components-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import type { AppConfig } from '@/app-config'
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider'
import { StartAudioButton } from '@/components/agents-ui/start-audio-button'
import {
  candidateSessionApi,
  type CandidateSessionError,
  type PreCheckResponse,
} from '@/lib/api/candidate-session'
import { useSessionOutcome } from './hooks/use-session-outcome'
import { ViewController, type Outcome } from './view-controller'

interface Props {
  appConfig: AppConfig
  token: string
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
}

export function App({ appConfig, token, preCheck, mode }: Props) {
  const [outcome, setOutcomeState] = useState<Outcome>('live')
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const [isStartPending, setIsStartPending] = useState(false)
  const credsRef = useRef<{ serverUrl: string; participantToken: string } | null>(null)

  const setError = useCallback((code: string) => {
    setErrorCode(code)
    setOutcomeState('error')
  }, [])

  const tokenSource = useMemo(
    () =>
      TokenSource.custom(async () => {
        if (credsRef.current) return credsRef.current
        try {
          setIsStartPending(true)
          const creds =
            mode === 'rejoin'
              ? await candidateSessionApi.rejoin(token)
              : await candidateSessionApi.start(token)
          credsRef.current = {
            serverUrl: creds.livekit_url,
            participantToken: creds.livekit_token,
          }
          return credsRef.current
        } catch (err) {
          const ce = err as CandidateSessionError
          if (mode === 'start' && (ce?.status === 409 || ce?.code === 'TOKEN_ALREADY_USED')) {
            setError('SESSION_ALREADY_STARTED')
          } else if (mode === 'rejoin' && ce?.status === 409) {
            setError('SESSION_ALREADY_COMPLETED')
          } else if (mode === 'rejoin' && ce?.status === 401) {
            setError('TOKEN_EXPIRED')
          } else if (mode === 'rejoin' && ce?.status === 429) {
            setError('REJOIN_RATE_LIMITED')
          } else if (mode === 'rejoin') {
            setError('REJOIN_REJECTED')
          } else {
            setError('SESSION_START_FAILED')
          }
          throw err
        } finally {
          setIsStartPending(false)
        }
      }),
    [token, mode, setError],
  )

  const session = useSession(tokenSource)

  // Engine-published session_outcome attribute → route on disconnect.
  const lastOutcome = useSessionOutcome()

  const onDisconnect = useCallback(
    (reason?: string | number) => {
      if (lastOutcome === 'completed') {
        setOutcomeState('completed')
      } else if (lastOutcome === 'error') {
        setError('ENGINE_ERROR')
      } else if (typeof reason === 'string' && reason === 'CLIENT_INITIATED') {
        setOutcomeState('completed')
      } else if (typeof reason === 'string' && reason === 'DUPLICATE_IDENTITY') {
        setError('DUPLICATE_SESSION')
      } else {
        setError('UNEXPECTED_DISCONNECT')
      }
    },
    [lastOutcome, setError],
  )

  // Wire the session's disconnect callback. The exact API depends on what
  // useSession returns — pattern: subscribe via .on('disconnected', ...) on
  // the underlying room, falling back to a useEffect that watches isConnected
  // transitioning from true→false.
  // (Verified during implementation against installed @livekit/components-react.)
  // Below is the safe minimal wiring — adapt if useSession exposes a callback prop.
  // The agents-ui AgentDisconnectButton already calls session.end() which triggers
  // the disconnected event; reason will arrive via the event listener.
  // Implementation note: `session` exposes `.events` or similar; use whatever the
  // installed version provides. Falling back to a useEffect on session state if
  // events aren't exposed.

  const onStart = useCallback(() => {
    void session.start().catch(() => {
      // Already routed in TokenSource.custom; nothing else to do here.
    })
  }, [session])

  return (
    <ThemeProvider attribute="class" forcedTheme="light">
      <AgentSessionProvider session={session}>
        <ViewController
          appConfig={appConfig}
          preCheck={preCheck}
          mode={mode}
          outcome={outcome}
          errorCode={errorCode}
          isStartPending={isStartPending}
          onStart={onStart}
          onError={setError}
        />
        <StartAudioButton label="Start audio" />
      </AgentSessionProvider>
    </ThemeProvider>
  )
}
```

> **Verification at implementation time:**
>
> 1. The exact mechanism for hooking a `'disconnected'` callback on `useSession()`'s return depends on the installed package. Inspect the local copy of `node_modules/@livekit/components-react/dist/...` after install. Likely options:
>    - `session.on('disconnected', cb)` if the session object emits events
>    - A `useEffect` watching `session.state` for `'disconnected'` and reading `session.lastDisconnectReason`
>    - A callback prop on `useSession(tokenSource, { onDisconnected })`
>
>    Wire `onDisconnect` through whichever surface is real. The function body above is the contract — input is `reason?`, output is the `setOutcome` / `setError` calls.
>
> 2. The exact name of the `CLIENT_INITIATED` / `DUPLICATE_IDENTITY` constants comes from `livekit-client`'s `DisconnectReason` enum. Import and use the enum constants instead of string literals if available.

- [ ] **Step 2: Build**

Run: `npm run type-check`
Expected: PASS (or surface any type mismatches that need a small adjustment to the disconnect-wiring shape).

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/interview/app/app.tsx
git commit -m "feat(interview): add App entry point with TokenSource.custom"
```

---

### Task 1.15: Wire WizardShell to mount the new App

**Files:**
- Modify: `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx` — drop StartStep + creds; render `<App mode="start">` after cam-mic
- Delete: `frontend/app/app/(interview)/interview/[token]/StartStep.tsx`
- Delete: `frontend/app/app/(interview)/interview/[token]/LiveSession/` (entire directory — only the LiveSessionShell + AgentTile + CandidateSelfView + TranscriptPane + use-agent-state remain to delete)
- Delete: `frontend/app/tests/components/interview/StartStep.test.tsx`
- Delete: `frontend/app/tests/components/interview/LiveSessionShell.test.tsx`

- [ ] **Step 1: Replace `WizardShell.tsx` with the new wiring**

Open `frontend/app/app/(interview)/interview/[token]/WizardShell.tsx` and replace the import block + branch logic + dynamic import:

```tsx
'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'

import { APP_CONFIG_DEFAULTS, type AppConfig } from '@/app-config'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { CameraMicStep } from './CameraMicStep'
import { ConsentStep } from './ConsentStep'
import { OtpStep } from './OtpStep'

const App = dynamic(
  () => import('@/components/interview/app/app').then((m) => m.App),
  {
    ssr: false,
    loading: () => (
      <div
        className="grid min-h-screen place-items-center text-[14px]"
        style={{ color: 'var(--px-fg-2)' }}
      >
        Connecting…
      </div>
    ),
  },
)

type WizardStepKey = 'consent' | 'otp' | 'cam-mic' | 'error'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)
  const [camMicPassed, setCamMicPassed] = useState(false)

  const currentStep = useMemo<WizardStepKey>(() => {
    if (!data) return 'error'
    if (data.state === 'cancelled' || data.state === 'error') return 'error'
    if (data.state === 'created' || data.state === 'pre_check') return 'consent'
    if (data.state === 'consented') {
      if (data.otp_required && !data.otp_verified_at) return 'otp'
      return 'cam-mic'
    }
    return 'cam-mic'
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

  if (isLoading) {
    return (
      <WizardFrame companyName="" jobTitle="" stageName="">
        <p className="text-center text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Loading…
        </p>
      </WizardFrame>
    )
  }

  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" stageName="">
        <div className="mx-auto max-w-[600px] py-16 text-center">
          <h1
            className="px-serif m-0 text-[40px] font-normal"
            style={{ letterSpacing: '-1px', color: 'var(--px-fg)' }}
          >
            This link isn&apos;t valid
          </h1>
          <p
            className="mx-auto mt-4 max-w-md text-[15px]"
            style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
          >
            The invite may have been revoked, replaced, or expired. Please
            contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  if (!data) return null

  // Active session → rejoin path. Bypasses cam-mic + consent (already passed).
  if (data.state === 'active') {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="rejoin" />
  }

  // Cam-mic passed → start path.
  if (currentStep === 'cam-mic' && camMicPassed) {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="start" />
  }

  return (
    <WizardFrame
      companyName={data.company_name}
      jobTitle={data.job_title}
      stageName={data.stage_name}
    >
      <StepProgress current={currentStep} otpRequired={data.otp_required} />

      <div className="mb-2 text-[11px] font-semibold uppercase" style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}>
        {data.stage_name} · {data.duration_minutes} minutes
      </div>
      <h1
        className="px-serif m-0 mb-4 text-[44px] font-normal"
        style={{ letterSpacing: '-1.1px', lineHeight: 1.08, color: 'var(--px-fg)' }}
      >
        Pre-interview check
      </h1>
      <p
        className="mb-8 text-[15px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
      >
        A few quick steps so we know you&apos;re ready and your setup works.
        Take your time — you can only move forward once each step is complete.
      </p>

      {currentStep === 'consent' && (
        <ConsentStep token={token} consentText={data.consent_text} />
      )}
      {currentStep === 'otp' && (
        <OtpStep token={token} otpIssuedAt={data.otp_issued_at} />
      )}
      {currentStep === 'cam-mic' && !camMicPassed && (
        <CameraMicStep onPass={() => setCamMicPassed(true)} />
      )}
    </WizardFrame>
  )
}

function WizardFrame({
  companyName,
  jobTitle,
  stageName: _stageName,
  children,
}: {
  companyName: string
  jobTitle: string
  stageName: string
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <div
        className="flex h-14 flex-shrink-0 items-center gap-3 border-b px-8"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="flex h-6 w-6 items-center justify-center rounded-[5px]"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        >
          <svg width="11" height="11" viewBox="0 0 12 12">
            <path d="M3 2v8l5-4z" fill="#fff" />
          </svg>
        </div>
        <div className="text-[13px]" style={{ color: 'var(--px-fg)' }}>
          <b style={{ fontWeight: 600 }}>{companyName || 'ProjectX'}</b>
          {jobTitle && (
            <span style={{ color: 'var(--px-fg-4)' }}> · {jobTitle}</span>
          )}
        </div>
        <div className="flex-1" />
      </div>

      <div className="flex-1 overflow-auto px-8 py-12">
        <div className="mx-auto max-w-[640px]">{children}</div>
      </div>
    </div>
  )
}

function StepProgress({
  current,
  otpRequired,
}: {
  current: WizardStepKey
  otpRequired: boolean
}) {
  const steps: { key: WizardStepKey; label: string }[] = [
    { key: 'consent', label: 'Consent' },
    ...(otpRequired ? [{ key: 'otp' as const, label: 'Verify' }] : []),
    { key: 'cam-mic', label: 'Camera & mic' },
  ]
  const currentIdx = steps.findIndex((s) => s.key === current)

  return (
    <div className="mb-12 flex gap-2">
      {steps.map((s, i) => {
        const done = i < currentIdx
        const active = i === currentIdx
        return (
          <div key={s.key} className="flex-1">
            <div
              className="h-[3px] rounded-[2px]"
              style={{
                background: done
                  ? 'var(--px-ok)'
                  : active
                    ? 'var(--px-accent)'
                    : 'var(--px-surface-3)',
              }}
            />
            <div
              className="mt-2 text-[11.5px]"
              style={{
                color: active ? 'var(--px-fg)' : 'var(--px-fg-4)',
                fontWeight: active ? 500 : 400,
              }}
            >
              {s.label}
            </div>
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Delete the obsolete files**

```bash
rm frontend/app/app/\(interview\)/interview/\[token\]/StartStep.tsx
rm -r frontend/app/app/\(interview\)/interview/\[token\]/LiveSession
rm frontend/app/tests/components/interview/StartStep.test.tsx
rm frontend/app/tests/components/interview/LiveSessionShell.test.tsx
```

- [ ] **Step 3: Build + remaining tests**

Run: `npm run type-check && npm run test`
Expected: PASS. The deleted tests are gone; the relocated ones (ProgressBanner, CompletionScreen, useSessionOutcome, ReconnectingOverlay) all green.

- [ ] **Step 4: Phase 1 checkpoint commit**

```bash
git add -A
git commit -m "feat(interview): port livekit agent-starter-react template into candidate surface

WizardShell hands off to <App> after cam-mic; mode='start' for fresh
sessions, mode='rejoin' for active sessions. StartStep and the old
LiveSession/ tree are removed; AgentSessionView_01 is the new in-room UI.

Phase 1 of livekit-frontend-template-port (spec 2026-04-30)."
```

- [ ] **Step 5: Manual smoke test (Phase 1 done)**

With backend running:
1. Open the frontend at `localhost:3000`, navigate to a candidate interview link.
2. Walk through Consent → (OTP) → Camera & mic.
3. After cam-mic passes, the welcome view should render with the warm-light shadcn palette: ProjectX-logo, "You're ready to begin", `companyName · jobTitle · X minutes`, "Start interview" button.
4. Click Start. The room connects, the engine joins. `AgentSessionView_01` renders with the bar visualizer, transcript, and full control bar.
5. Click End Call → `CompletionScreen` shows.

If anything is broken at this checkpoint, fix in-place before continuing to Phase 2.

---

## Phase 2 — Backend rejoin endpoint

End-of-phase verification: `pytest backend/nexus/tests/test_session_rejoin.py` passes; `curl POST /api/candidate-session/{token}/rejoin` against an `active` session returns a fresh LiveKit token; against a `completed` session returns 409.

---

### Task 2.1: Add rejoin schema

**Files:**
- Modify: `backend/nexus/app/modules/session/schemas.py`

- [ ] **Step 1: Read the existing `StartSessionResponse` schema**

```bash
grep -n "StartSessionResponse" backend/nexus/app/modules/session/schemas.py
```

- [ ] **Step 2: Reuse `StartSessionResponse` for rejoin (same shape)**

No new schema needed — the rejoin endpoint returns the same `{ livekit_url, livekit_token, room_name, session_id }` payload as `/start`. Confirm by reading the schema. Skip to Task 2.2 if so.

If for some reason you want a distinct type (e.g. for OpenAPI clarity), add:

```python
class RejoinResponse(StartSessionResponse):
    """Response from POST /api/candidate-session/{token}/rejoin.

    Same shape as StartSessionResponse — defined separately to allow
    future divergence (e.g. last-known-question-index hint).
    """
```

Otherwise reuse the existing class. **Recommended: reuse.**

- [ ] **Step 3: No commit yet** — schemas only changes if a new class was added; otherwise skip.

---

### Task 2.2: Add rejoin error type

**Files:**
- Modify: `backend/nexus/app/modules/session/errors.py`

- [ ] **Step 1: Add the typed error**

Append to `errors.py`:

```python
class SessionNotRejoinableError(Exception):
    """Session is not in 'active' state — rejoin is not allowed."""

    def __init__(self, current_state: str):
        self.current_state = current_state
        super().__init__(f"Session not rejoinable in state {current_state}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/session/errors.py
git commit -m "feat(session): add SessionNotRejoinableError"
```

---

### Task 2.3: Add rejoin service function (TDD)

**Files:**
- Modify: `backend/nexus/app/modules/session/service.py`
- Create: `backend/nexus/tests/test_session_rejoin.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the rejoin path — fresh LiveKit token mint without re-dispatch.

Reuses the _seed_ready_session helper from test_session_router.py to scaffold
a session at a controllable state.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.modules.session.service import rejoin_session
from app.modules.session.errors import SessionNotRejoinableError
from tests.test_session_router import _seed_ready_session, http_client


@pytest.mark.asyncio
async def test_rejoin_active_session_returns_fresh_lk_token(db, http_client):
    _t, _c, sess, _tok, token_str = await _seed_ready_session(db, state="active")
    sess.livekit_room_name = "lk-room-stub"
    sess.candidate_lk_identity = "candidate-stub"
    await db.flush()

    with patch(
        "app.modules.session.service._mint_candidate_lk_token",
        return_value="new-lk-token",
    ) as mint:
        response = await rejoin_session(db, session_id=sess.id)

    assert response.livekit_token == "new-lk-token"
    assert response.room_name == "lk-room-stub"
    mint.assert_called_once()


@pytest.mark.asyncio
async def test_rejoin_rejects_completed_session(db):
    _t, _c, sess, _tok, _ts = await _seed_ready_session(db, state="completed")
    with pytest.raises(SessionNotRejoinableError):
        await rejoin_session(db, session_id=sess.id)


@pytest.mark.asyncio
async def test_rejoin_endpoint_returns_200_for_active(db, http_client):
    _t, _c, sess, _tok, token_str = await _seed_ready_session(db, state="active")
    sess.livekit_room_name = "lk-room-stub"
    sess.candidate_lk_identity = "candidate-stub"
    await db.flush()

    with patch(
        "app.modules.session.service._mint_candidate_lk_token",
        return_value="new-lk-token",
    ):
        r = await http_client.post(
            f"/api/candidate-session/{token_str}/rejoin",
        )
    assert r.status_code == 200
    body = r.json()
    assert body["livekit_token"] == "new-lk-token"


@pytest.mark.asyncio
async def test_rejoin_endpoint_409_for_completed(db, http_client):
    _t, _c, sess, _tok, token_str = await _seed_ready_session(db, state="completed")
    r = await http_client.post(
        f"/api/candidate-session/{token_str}/rejoin",
    )
    assert r.status_code == 409
    assert r.json()["code"] == "SESSION_NOT_REJOINABLE"
```

- [ ] **Step 2: Run, expect FAIL**

```bash
docker compose run --rm nexus pytest tests/test_session_rejoin.py -v
```

Expected: FAIL — `rejoin_session` doesn't exist; endpoint 404.

- [ ] **Step 3: Read the surrounding code to align signatures**

Before implementing, read these to align with existing conventions:

```bash
grep -nE "mint_candidate_lk_token|livekit_room_name|candidate_lk_identity|start_session" \
  backend/nexus/app/modules/session/{service,livekit,router,schemas}.py \
  backend/nexus/app/models.py
```

Specifically confirm:
1. The exact function name and signature of the candidate LK token mint helper (likely `mint_candidate_lk_token` in `session/livekit.py`).
2. The actual identity scheme used at `/start` time — the column name for the candidate's LK identity may NOT be `candidate_lk_identity` (migration 0024 added 7 columns and that wasn't one of them). It might be derived (`f"candidate-{session.id}"`) or live elsewhere. Use whatever scheme `start_session` uses so the rejoin token has the **same identity** (otherwise LiveKit treats the rejoin as a different participant — DUPLICATE_IDENTITY won't fire correctly and engine state won't reattach cleanly).
3. The audit-log writer interface — likely `app.modules.audit.service.write_log` or similar. Match the call shape used elsewhere (e.g. in `start_session`).
4. The `StartSessionResponse` schema field names (`livekit_url` / `livekit_token` / `room_name` / `session_id`).

- [ ] **Step 4: Implement `rejoin_session` in `service.py`**

Add this function near `start_session`. **Adjust identity derivation, audit-log call, and import names based on Step 3 findings.** The structure stays the same; only the helper-call surface adapts.

```python
async def rejoin_session(
    db: AsyncSession,
    *,
    session_id: UUID,
) -> StartSessionResponse:
    """Mint a fresh LiveKit access token for a candidate rejoining an active session.

    Differences from start_session:
      * No engine dispatch — engine is already in the room.
      * No candidate-token state machine consume — that already happened on /start.
      * No state transition — session stays 'active'.
      * Idempotent on repeat calls within the JWT lifetime, gated by rate limit.

    Raises:
        SessionNotRejoinableError: session.state != 'active'.
    """
    result = await db.execute(
        select(SessionRow).where(SessionRow.id == session_id),
    )
    session = result.scalar_one()
    if session.state != "active":
        raise SessionNotRejoinableError(current_state=session.state)

    # IDENTITY SCHEME: must match exactly what start_session used. Read
    # start_session to confirm — likely f"candidate-{session.id}" or similar.
    candidate_identity = _candidate_identity_for(session)  # adapt name

    new_lk_token = mint_candidate_lk_token(
        identity=candidate_identity,
        room_name=session.livekit_room_name,
    )

    # Audit log — match the call surface used in start_session / etc.
    await audit_log_writer.log(  # adjust import + call shape
        db,
        action="candidate_session.rejoin",
        actor_id=None,
        tenant_id=session.tenant_id,
        resource_type="session",
        resource_id=session.id,
        correlation_id=session.correlation_id,
    )

    return StartSessionResponse(
        livekit_url=settings.livekit_url,
        livekit_token=new_lk_token,
        room_name=session.livekit_room_name,
        session_id=session.id,
    )
```

- [ ] **Step 5: Run service-layer test, expect PASS**

```bash
docker compose run --rm nexus pytest tests/test_session_rejoin.py::test_rejoin_active_session_returns_fresh_lk_token \
                                          tests/test_session_rejoin.py::test_rejoin_rejects_completed_session -v
```

Expected: PASS.

- [ ] **Step 6: Add the endpoint in `router.py`**

```python
@candidate_session_router.post(
    "/rejoin",
    response_model=StartSessionResponse,
    summary="Rejoin an active interview session",
)
async def post_rejoin_endpoint(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_tenant_db),
) -> StartSessionResponse:
    """Mint a fresh LiveKit access token for a candidate rejoining mid-session.

    Rate limit: 5/hour per token, 3/min per IP.
    """
    return await session_service.rejoin_session(
        db, session_id=_candidate_session_id(request),
    )
```

- [ ] **Step 6b: Add the rate-limit declaration**

Find an existing candidate-session endpoint that already declares a rate limit (e.g. the OTP endpoints — they're documented as `3/hour` per CLAUDE.md). Mirror the same decorator/middleware pattern at the new `post_rejoin_endpoint` definition. Target limits per the spec:

- 5/hour per token (token-scoped)
- 3/min per IP (IP-scoped)

If Nexus uses `slowapi`, the pattern is `@limiter.limit("5/hour", key_func=...)`. If it's a different library, follow the existing convention. **Failing to declare a rate limit blocks merge per root `CLAUDE.md` § "Rate Limiting & Abuse Posture".**

- [ ] **Step 7: Run all rejoin tests**

```bash
docker compose run --rm nexus pytest tests/test_session_rejoin.py -v
```

Expected: PASS, all four tests green.

- [ ] **Step 8: Map the typed error to a 409 response**

Open `backend/nexus/app/main.py` and locate the existing exception handlers for the session module. Add a handler for `SessionNotRejoinableError`:

```python
from app.modules.session.errors import SessionNotRejoinableError

@app.exception_handler(SessionNotRejoinableError)
async def session_not_rejoinable_handler(_request: Request, exc: SessionNotRejoinableError):
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "code": "SESSION_NOT_REJOINABLE",
            "current_state": exc.current_state,
        },
    )
```

If the existing handler pattern uses a different style (registry, decorator, etc.), follow that instead.

- [ ] **Step 8b: Run the endpoint tests**

```bash
docker compose run --rm nexus pytest tests/test_session_rejoin.py -v
```

Expected: PASS — including the 409 case.

- [ ] **Step 9: Run full session test suite (regression)**

```bash
docker compose run --rm nexus pytest tests/test_session_router.py tests/test_session_service.py tests/test_session_rejoin.py -v
```

Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
git add backend/nexus/app/modules/session/service.py \
        backend/nexus/app/modules/session/router.py \
        backend/nexus/app/modules/session/errors.py \
        backend/nexus/app/main.py \
        backend/nexus/tests/test_session_rejoin.py
git commit -m "feat(session): add /rejoin endpoint for active-session reconnect

Mints a fresh LiveKit access token for the same room without
re-dispatching the engine. Audit-logged, rate-limited
(5/hour per token, 3/min per IP). Returns 409 when session
is not in 'active' state.

Phase 2 of livekit-frontend-template-port (spec 2026-04-30)."
```

---

### Task 2.4: Phase 2 end-of-phase verification

The frontend `candidateSessionApi.rejoin` method already exists (added in Task 1.4b). Now that the backend endpoint exists, end-to-end the rejoin call will succeed for `active` sessions.

- [ ] **Step 1: Boot the frontend and trigger a real rejoin**

```bash
# Backend already up from Phase 1.
cd frontend/app && npm run dev
```

Open a candidate URL where the session is in `active` state (you can scaffold this manually by running through Phase 1's smoke test and then refreshing the browser).

- [ ] **Step 2: Confirm the rejoin path works**

The wizard pre-check should return `state='active'`, the App should mount in `mode='rejoin'`, the welcome view shows "Rejoin your interview", and clicking the button connects to the same room with a fresh LK token. Rate-limit testing is deferred to manual smoke in Task 4.3.

- [ ] **Step 3: Phase 2 checkpoint commit (no code change)**

```bash
git commit --allow-empty -m "chore: phase 2 of livekit port verified — /rejoin endpoint live"
```

---

## Phase 3 — Engine graceful close signal

End-of-phase verification: engine test asserts `set_attributes` is called with `session_outcome='completed'` before the agent's lifecycle ends.

---

### Task 3.1: Engine test for graceful close

**Files:**
- Create: `backend/interview_engine/tests/test_graceful_close.py`

- [ ] **Step 1: Write the failing test**

```python
"""Verifies the agent publishes session_outcome='completed' on Action.CLOSE
before the lifecycle ends.

Mirrors the patch surface used by tests/test_progress_attributes.py — the
engine's _publish_progress_attributes test — patching the same
session.room_io.room.local_participant.set_attributes call.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.interviewer import InterviewerAgent
from state_machine import Action


@pytest.mark.asyncio
async def test_close_action_publishes_session_outcome_completed():
    agent = _make_agent()

    # Spy on set_attributes — the engine writes progress attrs each turn,
    # so we filter for the session_outcome key on the final call.
    set_attrs = AsyncMock()
    agent.session.room_io.room.local_participant.set_attributes = set_attrs

    # Drive the state machine through to CLOSE.
    agent.state_machine.execute_action = MagicMock(return_value="goodbye-text")
    agent.state_machine.decide_next_action = MagicMock(return_value=Action.CLOSE)
    agent._build_session_result = MagicMock(return_value=_session_result_stub())
    agent._persist_result = AsyncMock()

    await agent.record_observation(
        context=MagicMock(),
        answer_summary="answer",
        signals_demonstrated=[],
        wants_to_probe=False,
        candidate_disengaged=False,
        notes="",
    )

    # Find the call(s) carrying session_outcome.
    outcome_calls = [
        c for c in set_attrs.call_args_list
        if isinstance(c.args[0], dict) and "session_outcome" in c.args[0]
    ]
    assert len(outcome_calls) == 1
    assert outcome_calls[0].args[0]["session_outcome"] == "completed"


def _make_agent() -> "InterviewerAgent":
    """Build an InterviewerAgent with stubbed session/state machine.

    Mirrors the construction pattern from tests/test_progress_attributes.py;
    if that file evolves, port it here.
    """
    # Reuse the helper from the sibling test if it's exposed; otherwise inline.
    # See backend/interview_engine/tests/test_progress_attributes.py for the
    # canonical pattern.
    raise NotImplementedError(
        "Wire this constructor by mirroring test_progress_attributes._make_agent"
    )


def _session_result_stub():
    return MagicMock()
```

> **Step 1.5:** Open `backend/interview_engine/tests/test_progress_attributes.py` and adapt the existing `_make_agent` helper there. Inline it into the new test file (or extract into a shared `tests/_factories.py` if you prefer). The new test must construct an `InterviewerAgent` whose `session.room_io.room.local_participant` is a stub.

- [ ] **Step 2: Run, expect FAIL**

```bash
docker compose run --rm --entrypoint bash interview-engine \
  -c "uv pip install --python /venv/engine/bin/python pytest pytest-asyncio respx --quiet \
      && cd /app/interview_engine \
      && PYTHONPATH=/app/interview_engine:/app/nexus /venv/engine/bin/python -m pytest tests/test_graceful_close.py -v"
```

Expected: FAIL — engine never calls `set_attributes` with `session_outcome`.

---

### Task 3.2: Implement graceful close in interviewer.py

**Files:**
- Modify: `backend/interview_engine/agents/interviewer.py`

- [ ] **Step 1: Add the publish call in the CLOSE branch of `record_observation`**

Locate this block in `interviewer.py:201-205`:

```python
if action == Action.CLOSE:
    result = self._build_session_result()
    await self._persist_result(result)

return context_injection
```

Replace with:

```python
if action == Action.CLOSE:
    result = self._build_session_result()
    await self._persist_result(result)
    await self._publish_session_outcome("completed")

return context_injection
```

- [ ] **Step 2: Add the helper method below `_publish_progress_attributes`**

```python
async def _publish_session_outcome(self, outcome: str) -> None:
    """Publish the session outcome on the agent's local participant.

    The candidate's frontend reads this attribute on the Disconnected event
    to route between CompletionScreen ('completed') and DisconnectError
    with code ENGINE_ERROR ('error'). Best-effort — a failure here must
    not abort shutdown; the candidate will still see UNEXPECTED_DISCONNECT
    in that case.
    """
    try:
        room = self.session.room_io.room
        await room.local_participant.set_attributes({"session_outcome": outcome})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview.outcome.publish_failed",
            outcome=outcome,
            error=str(exc),
            error_type=type(exc).__name__,
        )
```

- [ ] **Step 3: Run the test, expect PASS**

```bash
docker compose run --rm --entrypoint bash interview-engine \
  -c "PYTHONPATH=/app/interview_engine:/app/nexus /venv/engine/bin/python -m pytest tests/test_graceful_close.py -v"
```

Expected: PASS.

- [ ] **Step 4: Run the full engine test suite to verify no regression**

```bash
docker compose run --rm --entrypoint bash interview-engine \
  -c "PYTHONPATH=/app/interview_engine:/app/nexus /venv/engine/bin/python -m pytest tests/ -v"
```

Expected: PASS, all green.

- [ ] **Step 5: Phase 3 checkpoint commit**

```bash
git add backend/interview_engine/agents/interviewer.py \
        backend/interview_engine/tests/test_graceful_close.py
git commit -m "feat(engine): publish session_outcome attribute before CLOSE shutdown

Engine writes session_outcome='completed' on its local participant via
set_attributes before persisting the result and ending the lifecycle.
Frontend reads this on the Disconnected event to distinguish a graceful
end from a network drop.

Phase 3 of livekit-frontend-template-port (spec 2026-04-30)."
```

---

## Phase 4 — Wiring, docs, end-to-end smoke

End-of-phase verification: full candidate flow end-to-end, including refresh-mid-session rejoin and intentional engine kill.

---

### Task 4.1: Update CLAUDE.md / AGENTS.md files

**Files:**
- Modify: `frontend/app/CLAUDE.md` — note the shadcn enclave
- Modify: `frontend/app/AGENTS.md` — same
- Modify: `backend/interview_engine/AGENTS.md` — drop graceful-close + rejoin from "Out of scope"
- Modify: root `CLAUDE.md` — Phase 3C.2 line update

- [ ] **Step 1: `frontend/app/CLAUDE.md` — add a new section under "Component Library — In-House `px/` Primitives"**

Find the heading "### Component Library — In-House `px/` Primitives" and insert immediately after it (or just before "**Base UI ecosystem rules**"):

```markdown
**Candidate-surface shadcn enclave (Phase 3C.2 LiveKit port).** The candidate
interview surface (`app/(interview)/`) imports from a sealed shadcn enclave at
`components/{ui,agents-ui,ai-elements}/` plus `hooks/agents-ui/`. These are
populated by the shadcn CLI from the `@agents-ui` and `@ai-elements`
registries — you own the source. **The dashboard surface MUST NOT import from
this enclave.** `globals.css` already maps the shadcn token namespace
(`--background`, `--foreground`, `--primary`, …) to the `--px-*` palette so
visual coherence is automatic. To update components, run:

```bash
npx shadcn@latest add @agents-ui/<component-name>
```
```

- [ ] **Step 2: `frontend/app/AGENTS.md` — keep brief**

Append after the "This is NOT the Next.js you know" warning:

```markdown
## Candidate surface uses shadcn

`app/(interview)/` imports from `components/{ui,agents-ui,ai-elements}/` —
shadcn primitives + LiveKit Agents UI components. The dashboard surface
must not import from these directories.
```

- [ ] **Step 3: `backend/interview_engine/AGENTS.md` — update the out-of-scope list**

Find the "Out of scope (Phase 3D follow-ups)" block and remove these two lines (the rest stay):

```diff
- - Graceful-vs-error disconnect signal back to the frontend (so the
-   candidate sees DisconnectError vs CompletionScreen correctly).
- - Mid-session rejoin if the candidate's network drops.
```

- [ ] **Step 4: Root `CLAUDE.md` — update Phase 3C.2 status row**

Find the row in the phase status table:

```
| 3C.2 | LiveKit room + token provisioning on `/start`; `interview_runtime` internal API; engine worker container with structured-interview state machine; candidate live UI (LiveSessionShell, hooks, transcript, progress banner) | ✅ done |
```

Replace with:

```
| 3C.2 | LiveKit room + token provisioning on `/start`; `interview_runtime` internal API; engine worker container with structured-interview state machine; candidate live UI (LiveKit agent-starter-react template via @agents-ui shadcn enclave) + graceful close signal + mid-session rejoin endpoint | ✅ done |
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md frontend/app/CLAUDE.md frontend/app/AGENTS.md backend/interview_engine/AGENTS.md
git commit -m "docs: note shadcn enclave + graceful close + rejoin"
```

---

### Task 4.2: Optional — App test (outcome routing smoke)

**Files:**
- Create: `frontend/app/tests/components/interview/app.test.tsx`

A focused test on the new `App` component that exercises outcome routing without spinning up the whole LiveKit stack.

- [ ] **Step 1: Write a happy-path render test**

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-react', () => ({
  useSession: () => ({ start: vi.fn(), end: vi.fn(), state: 'idle' }),
  useSessionContext: () => ({ isConnected: false, state: 'idle', start: vi.fn(), end: vi.fn() }),
  useRemoteParticipants: () => [],
  SessionProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  RoomAudioRenderer: () => null,
  useChat: () => ({ chatMessages: [], send: vi.fn() }),
}))

vi.mock('livekit-client', () => ({
  TokenSource: { custom: () => ({}) },
}))

vi.mock('@/components/agents-ui/agent-session-provider', () => ({
  AgentSessionProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/agents-ui/start-audio-button', () => ({
  StartAudioButton: () => null,
}))

vi.mock('@/components/agents-ui/blocks/agent-session-view-01', () => ({
  AgentSessionView_01: () => <div data-testid="session-view" />,
}))

vi.mock('next-themes', () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import { App } from '@/components/interview/app/app'
import { APP_CONFIG_DEFAULTS } from '@/app-config'
import type { PreCheckResponse } from '@/lib/api/candidate-session'

const PRE_CHECK: PreCheckResponse = {
  session_id: 'sess-1',
  company_name: 'Acme',
  job_title: 'Senior Engineer',
  stage_name: 'AI Interview',
  duration_minutes: 30,
  consent_text: 'consent',
  state: 'consented',
  otp_required: false,
  otp_verified_at: null,
  otp_issued_at: null,
}

describe('App', () => {
  it('renders the welcome view in start mode when not connected', () => {
    render(<App appConfig={APP_CONFIG_DEFAULTS} token="t" preCheck={PRE_CHECK} mode="start" />)
    expect(screen.getByRole('button', { name: /start interview/i })).toBeInTheDocument()
  })

  it('renders the rejoin welcome copy in rejoin mode when not connected', () => {
    render(<App appConfig={APP_CONFIG_DEFAULTS} token="t" preCheck={PRE_CHECK} mode="rejoin" />)
    expect(screen.getByRole('button', { name: /rejoin interview/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run, expect PASS** (the App is already implemented)

Run: `npm run test -- app.test`
Expected: PASS — both rendering paths.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/tests/components/interview/app.test.tsx
git commit -m "test(interview): add App outcome-routing smoke test"
```

---

### Task 4.3: End-to-end smoke (manual verification — final gate)

- [ ] **Step 1: Bring up the full local stack**

```bash
cd backend/nexus && docker compose up --build -d
cd ../../frontend/app && npm run dev
```

Wait for both services to be ready.

- [ ] **Step 2: Walk the happy path**

1. From the recruiter UI, send an invite to a test candidate.
2. Open the resulting candidate URL.
3. Walk the wizard: Consent → (OTP if required) → Camera & mic.
4. Confirm the new shadcn-enclave welcome view renders with the warm-light palette.
5. Click *Start interview*. The room connects, the engine joins, the bar visualizer animates, the transcript renders.
6. Speak. Observe the engine respond. Observe `ProgressBanner` updating ("Q2 of 9 · X min remaining").
7. Click End Call. Confirm `CompletionScreen` renders.

- [ ] **Step 3: Walk the graceful-close path**

1. Send a fresh invite, walk through, get into the session.
2. Have the engine drive through to `Action.CLOSE` (let the interview run to completion or — for speed — set `max_questions=1` in the engine config and answer the question).
3. Confirm `CompletionScreen` renders (not `DisconnectError`).

- [ ] **Step 4: Walk the rejoin path**

1. Send a fresh invite, walk through, get into the session.
2. Mid-session, hit the browser refresh button (Cmd+R / Ctrl+R).
3. Wizard reloads, sees `state='active'`, mounts `<App mode='rejoin'>`.
4. Welcome view shows "Rejoin your interview" copy + "Rejoin interview" button.
5. Click Rejoin. Room reconnects. Engine is still in the room. `ProgressBanner` shows the current question (not Q1).
6. Continue and end normally — `CompletionScreen` renders.

- [ ] **Step 5: Walk the unexpected-disconnect path**

1. Send a fresh invite, walk through, get into the session.
2. Mid-session, kill the engine container: `docker compose stop interview-engine`.
3. Within ~30s (the agent's grace timeout, which now fires post-connect), the candidate should see `DisconnectError` with `UNEXPECTED_DISCONNECT` (or `AGENT_NO_SHOW` if the engine never published a partial outcome).

> The exact code depends on whether the engine had time to write `session_outcome='error'` before being killed. Both are acceptable — the test is that we **don't** see `CompletionScreen`.

- [ ] **Step 6: Walk the duplicate-tab path**

1. Send a fresh invite, walk through, get into the session.
2. Open the same URL in a second browser tab. The wizard sees `state='active'` and mounts `<App mode='rejoin'>`.
3. Click Rejoin in the second tab. The first tab should disconnect with `DUPLICATE_SESSION` (LiveKit emits `DUPLICATE_IDENTITY`).

- [ ] **Step 7: Final commit (no code change — bookmark)**

```bash
git commit --allow-empty -m "chore: end-to-end smoke verified for livekit frontend port"
```

---

## Self-review checklist (before declaring done)

- [ ] All four phases committed; no `WIP` or `TODO` files left.
- [ ] `npm run build`, `npm run lint`, `npm run type-check`, `npm run test` all pass in `frontend/app/`.
- [ ] `docker compose run nexus pytest` passes in `backend/nexus/`.
- [ ] Engine test suite passes (`pytest` invocation in Task 3.1).
- [ ] No imports from `components/{ui,agents-ui,ai-elements}/` outside `app/(interview)/`. Quick grep:

```bash
grep -rE "from '@/components/(ui|agents-ui|ai-elements)" frontend/app/app/ \
  | grep -v "(interview)" || echo "clean"
```

Expected: `clean`.

- [ ] All four manual smoke paths in Task 4.3 walked successfully.
- [ ] `docs/superpowers/specs/2026-04-30-livekit-frontend-template-port-design.md` § "Acceptance criteria" — every bullet checked.

---

## Notes / known follow-ups

- **`AgentSessionView_01` prop names** were copied from the starter's `view-controller.tsx`. After Task 1.3 installs the registry version, verify against the locally-installed source. If a name differs, fix in `view-controller.tsx`.
- **Disconnect callback wiring** in `app.tsx` is the most fragile part — `useSession`'s exact event surface depends on the installed package. Inspect the installed module after Task 1.3 and wire whichever surface is real (event emitter, callback prop, or state-watch effect). The contract in `onDisconnect` does not change.
- **`DisconnectReason` enum** — prefer `import { DisconnectReason } from 'livekit-client'` over string literals if the enum is exported.
- **Tenant logo + accent** — the `appConfig.logo` is a static path. Wiring tenant logo + accent through the pre-check API is a separate ticket per the spec's "Out of scope" section.
- **Trim controls** — once the candidate UI is settled, switch `AgentSessionView_01`'s `supports*` props to `false` for the proctored-interview minimum (mute / camera toggle / screen-share / chat input off; only End Call remains). Spec § "Decisions captured" item 3.
