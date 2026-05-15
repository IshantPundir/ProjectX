# Tracker Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `/tracker` surface in the recruiter dashboard that lists live jobs as Rich cards and opens a full-width kanban board per job at `/tracker/[jobId]`. Remove the kanban view from `/candidates` and 308-redirect the legacy URL.

**Architecture:** Two new Next.js routes (`/tracker` landing + `/tracker/[jobId]` detail), a thin `useTrackerJobs` hook over `jobsApi.list`, one new `TrackerJobCard` component, and a `TrackerKanbanPage` wrapper that reuses the existing `CandidateKanbanView` verbatim. The kanban primitives move from `app/(dashboard)/candidates/` to `components/dashboard/tracker/` (Tracker becomes their only consumer). A redirect rule in `proxy.ts` translates `/candidates?jd=<uuid>&view=kanban` → `/tracker/<uuid>`.

**Tech Stack:** Next.js 16 App Router (async params per `frontend/app/AGENTS.md`), TypeScript strict, TanStack Query v5, Tailwind v4, in-house `components/px/` primitives, `@dnd-kit` (already used by the kanban view), Vitest + jsdom.

**Spec:** `docs/superpowers/specs/2026-05-15-tracker-page-design.md`

---

## Task 0: Extract `postedAgo` to `lib/utils.ts`

**Files:**
- Modify: `frontend/app/lib/utils.ts`
- Modify: `frontend/app/app/(dashboard)/jobs/page.tsx` (lines 130–140)
- Test: `frontend/app/tests/lib/posted-ago.test.ts` (new)

`postedAgo()` is currently inline in `jobs/page.tsx`. Tracker is the second consumer, so extract before adding new code. Keep the existing function body verbatim — relative-time format must not change.

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/lib/posted-ago.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'

import { postedAgo } from '@/lib/utils'

describe('postedAgo', () => {
  // Pin "now" so the test isn't time-dependent. 2026-05-15T12:00:00Z.
  const NOW = new Date('2026-05-15T12:00:00.000Z').getTime()

  it('returns "today" for the same day', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-15T08:00:00.000Z')).toBe('today')
  })

  it('returns "1d ago" for one day prior', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-14T08:00:00.000Z')).toBe('1d ago')
  })

  it('returns "Nd ago" for under a month', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-05-01T08:00:00.000Z')).toBe('14d ago')
  })

  it('returns "1mo ago" for one month prior', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-04-10T08:00:00.000Z')).toBe('1mo ago')
  })

  it('returns "Nmo ago" beyond two months', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
    expect(postedAgo('2026-02-01T08:00:00.000Z')).toBe('3mo ago')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/app/`:
```bash
npm run test -- tests/lib/posted-ago.test.ts
```
Expected: FAIL — `postedAgo` is not exported from `@/lib/utils`.

- [ ] **Step 3: Add `postedAgo` to `lib/utils.ts`**

Append to `frontend/app/lib/utils.ts`:

```ts
/**
 * Compact relative-time formatter — "today", "Nd ago", "Nmo ago".
 * Used by the Roles list and Tracker landing card. The buckets are
 * deliberately coarse — the Tracker UI doesn't surface anything sub-day
 * resolution, and this matches the existing /jobs presentation.
 */
export function postedAgo(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const days = Math.floor((now - then) / (1000 * 60 * 60 * 24))
  if (days === 0) return 'today'
  if (days === 1) return '1d ago'
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months === 1) return '1mo ago'
  return `${months}mo ago`
}
```

- [ ] **Step 4: Replace inline copy in `jobs/page.tsx`**

In `frontend/app/app/(dashboard)/jobs/page.tsx`:

1. Add `postedAgo` to the existing `lib/utils` import (or add a new import line if no `@/lib/utils` import exists yet):

```ts
import { postedAgo } from '@/lib/utils'
```

2. Delete the inline `postedAgo` function (lines 130–140 — the comment `/* ─── Posted-ago helper ───────────── */` and the function body).

- [ ] **Step 5: Run test + type-check**

```bash
npm run test -- tests/lib/posted-ago.test.ts
npm run type-check
```
Expected: PASS for both.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/utils.ts \
        frontend/app/app/\(dashboard\)/jobs/page.tsx \
        frontend/app/tests/lib/posted-ago.test.ts
git commit -m "refactor(utils): extract postedAgo helper for reuse by Tracker"
```

---

## Task 1: Move kanban primitives to `components/dashboard/tracker/`

**Files:**
- Move (`git mv`):
  - `frontend/app/app/(dashboard)/candidates/CandidateKanbanView.tsx` → `frontend/app/components/dashboard/tracker/CandidateKanbanView.tsx`
  - `frontend/app/app/(dashboard)/candidates/CandidateKanbanColumn.tsx` → `frontend/app/components/dashboard/tracker/CandidateKanbanColumn.tsx`
  - `frontend/app/app/(dashboard)/candidates/CandidateKanbanCard.tsx` → `frontend/app/components/dashboard/tracker/CandidateKanbanCard.tsx`
- Modify: `frontend/app/app/(dashboard)/candidates/ClientCandidatesPage.tsx` (only the import path — `view=kanban` branch stays until Task 8)

The 3 components are pure props-in / event-out. No behavior change. Their imports of `SendInviteDialog`, `SessionStatusBadge`, `StatusBadge`, `StageTransitionDropdown`, `KanbanColumn` etc. all use `@/...` aliases — those keep working from the new location.

- [ ] **Step 1: Verify nothing else imports the kanban primitives**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
grep -rn "CandidateKanban" app/ components/ lib/ tests/ 2>/dev/null | grep -v node_modules | grep -v '.next'
```
Expected: only references in the 3 files being moved + `ClientCandidatesPage.tsx`. If anything else turns up, stop and reconcile before proceeding.

- [ ] **Step 2: Move the 3 files via `git mv`**

```bash
mkdir -p components/dashboard/tracker
git mv app/\(dashboard\)/candidates/CandidateKanbanView.tsx     components/dashboard/tracker/CandidateKanbanView.tsx
git mv app/\(dashboard\)/candidates/CandidateKanbanColumn.tsx   components/dashboard/tracker/CandidateKanbanColumn.tsx
git mv app/\(dashboard\)/candidates/CandidateKanbanCard.tsx     components/dashboard/tracker/CandidateKanbanCard.tsx
```

- [ ] **Step 3: Fix the relative imports inside the moved files**

`CandidateKanbanView.tsx` imports `./CandidateKanbanColumn`, `CandidateKanbanColumn.tsx` imports `./CandidateKanbanCard`. The relative paths still resolve at the new location (siblings move together) — no change needed inside those files. Verify by reading each file's import block.

`CandidateKanbanCard.tsx` imports `./SendInviteDialog`, which has NOT moved (`SendInviteDialog` stays under `app/(dashboard)/candidates/`). Update that import:

```ts
// CandidateKanbanCard.tsx — change:
import { SendInviteDialog } from './SendInviteDialog'
// to:
import { SendInviteDialog } from '@/app/(dashboard)/candidates/SendInviteDialog'
```

- [ ] **Step 4: Update the import in `ClientCandidatesPage.tsx`**

In `frontend/app/app/(dashboard)/candidates/ClientCandidatesPage.tsx` (line ~10):

```ts
// before
import CandidateKanbanView from './CandidateKanbanView'
// after
import CandidateKanbanView from '@/components/dashboard/tracker/CandidateKanbanView'
```

- [ ] **Step 5: Type-check + run the existing test suite**

```bash
npm run type-check
npm run test
```
Expected: both PASS. No test should reference the old paths — confirm with `grep "candidates/CandidateKanban" tests/` (should be empty).

- [ ] **Step 6: Manual smoke**

```bash
npm run dev
```
Open `http://localhost:3000/candidates?jd=<any-active-job-uuid>&view=kanban` — kanban must render exactly as before. Drag a card to verify DnD still works.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(tracker): move kanban primitives to components/dashboard/tracker

Tracker is becoming their only consumer. No behavior change."
```

---

## Task 2: Add `useTrackerJobs` hook

**Files:**
- Create: `frontend/app/lib/hooks/use-tracker-jobs.ts`
- Test: `frontend/app/tests/lib/hooks/use-tracker-jobs.test.ts` (new)

Filters `jobsApi.list()` to live jobs only (`pipeline_built` + `active`) and sorts by `updated_at` desc. Reuses the existing `['jobs-list']` query key so the hook doesn't double-fetch when `/jobs` and `/tracker` are both visited.

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/lib/hooks/use-tracker-jobs.test.ts`:

```ts
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'

import type { JobPostingSummary } from '@/lib/api/jobs'

vi.mock('@/lib/api/jobs', async (orig) => {
  const actual = await orig<typeof import('@/lib/api/jobs')>()
  return {
    ...actual,
    jobsApi: {
      list: vi.fn(),
    },
  }
})
vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'tok'),
}))

import { jobsApi } from '@/lib/api/jobs'
import { useTrackerJobs } from '@/lib/hooks/use-tracker-jobs'

function makeJob(over: Partial<JobPostingSummary>): JobPostingSummary {
  return {
    id: 'id',
    title: 'Job',
    org_unit_id: 'ou',
    org_unit_name: 'Acme',
    created_by_email: null,
    updated_by_email: null,
    status: 'active',
    status_error: null,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-15T00:00:00Z',
    signal_count: 0,
    needs_review_count: 0,
    source: 'native',
    external_id: null,
    external_status: null,
    profile_ready: true,
    ...over,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useTrackerJobs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns only pipeline_built + active jobs, sorted by updated_at desc', async () => {
    vi.mocked(jobsApi.list).mockResolvedValue([
      makeJob({ id: 'a', status: 'draft', updated_at: '2026-05-15T10:00:00Z' }),
      makeJob({ id: 'b', status: 'active', updated_at: '2026-05-10T10:00:00Z' }),
      makeJob({ id: 'c', status: 'pipeline_built', updated_at: '2026-05-14T10:00:00Z' }),
      makeJob({ id: 'd', status: 'archived', updated_at: '2026-05-15T11:00:00Z' }),
      makeJob({ id: 'e', status: 'signals_confirmed', updated_at: '2026-05-15T09:00:00Z' }),
    ])

    const { result } = renderHook(() => useTrackerJobs(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.map((j) => j.id)).toEqual(['c', 'b'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- tests/lib/hooks/use-tracker-jobs.test.ts
```
Expected: FAIL — module `@/lib/hooks/use-tracker-jobs` not found.

- [ ] **Step 3: Implement the hook**

Create `frontend/app/lib/hooks/use-tracker-jobs.ts`:

```ts
'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Live jobs for the Tracker landing page — `pipeline_built` or `active`,
 * sorted by `updated_at` desc. Reuses the `['jobs-list']` cache so visiting
 * /jobs and /tracker doesn't double-fetch.
 */
export function useTrackerJobs() {
  return useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token, undefined, { signal })
    },
    select: (jobs) =>
      jobs
        .filter((j) => j.status === 'pipeline_built' || j.status === 'active')
        .sort(
          (a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        ),
  })
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm run test -- tests/lib/hooks/use-tracker-jobs.test.ts
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/hooks/use-tracker-jobs.ts \
        frontend/app/tests/lib/hooks/use-tracker-jobs.test.ts
git commit -m "feat(tracker): useTrackerJobs hook for live-job filter"
```

---

## Task 3: Add `TrackerJobCard` component

**Files:**
- Create: `frontend/app/components/dashboard/tracker/TrackerJobCard.tsx`
- Test: `frontend/app/tests/components/TrackerJobCard.test.tsx` (new)

Rich card per the spec: title, org, status pill, stacked stage bar, per-stage labels, total count + last activity. Whole card is a `<Link>` to `/tracker/[jobId]`. Calls `useKanbanBoard(job.id)` for the per-stage breakdown — shows shimmer placeholder until the response arrives.

- [ ] **Step 1: Write the failing composition test**

Create `frontend/app/tests/components/TrackerJobCard.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'

import type { JobPostingSummary } from '@/lib/api/jobs'
import type { KanbanBoardResponse } from '@/lib/api/candidates'

vi.mock('@/lib/hooks/use-kanban-board', () => ({
  useKanbanBoard: vi.fn(),
}))

import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { TrackerJobCard } from '@/components/dashboard/tracker/TrackerJobCard'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const JOB: JobPostingSummary = {
  id: 'job-123',
  title: 'Senior Backend Engineer',
  org_unit_id: 'ou',
  org_unit_name: 'Acme · Platform Team',
  created_by_email: null,
  updated_by_email: null,
  status: 'active',
  status_error: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-14T00:00:00Z',
  signal_count: 7,
  needs_review_count: 0,
  source: 'native',
  external_id: null,
  external_status: null,
  profile_ready: true,
}

const BOARD: KanbanBoardResponse = {
  job_posting_id: 'job-123',
  stages: [
    { stage_id: 's1', stage_name: 'Intake', position: 0, candidates: Array(3).fill({}) as never },
    { stage_id: 's2', stage_name: 'Phone', position: 1, candidates: Array(4).fill({}) as never },
    { stage_id: 's3', stage_name: 'AI Screen', position: 2, candidates: Array(2).fill({}) as never },
    { stage_id: 's4', stage_name: 'Human Iv', position: 3, candidates: Array(2).fill({}) as never },
    { stage_id: 's5', stage_name: 'Debrief', position: 4, candidates: Array(1).fill({}) as never },
  ],
}

describe('TrackerJobCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders title, org, status pill, stage counts, and total', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: BOARD,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })

    // Title + org
    expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument()
    expect(screen.getByText(/Acme · Platform Team/)).toBeInTheDocument()
    // Status pill
    expect(screen.getByText('active')).toBeInTheDocument()
    // Per-stage labels
    expect(screen.getByText(/Intake/)).toBeInTheDocument()
    expect(screen.getByText(/Phone/)).toBeInTheDocument()
    expect(screen.getByText(/Debrief/)).toBeInTheDocument()
    // Total = 3+4+2+2+1 = 12
    expect(screen.getByText(/12/)).toBeInTheDocument()
    // Linked to detail
    expect(screen.getByRole('link')).toHaveAttribute('href', '/tracker/job-123')
  })

  it('renders the empty-board message when stages are empty', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: { job_posting_id: 'job-123', stages: [] },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })
    expect(screen.getByText(/No candidates yet/i)).toBeInTheDocument()
  })

  it('renders a shimmer placeholder while the board is loading', () => {
    vi.mocked(useKanbanBoard).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useKanbanBoard>)

    render(<TrackerJobCard job={JOB} />, { wrapper })
    expect(screen.getByTestId('tracker-card-bar-loading')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- tests/components/TrackerJobCard.test.tsx
```
Expected: FAIL — module `@/components/dashboard/tracker/TrackerJobCard` not found.

- [ ] **Step 3: Implement `TrackerJobCard`**

Create `frontend/app/components/dashboard/tracker/TrackerJobCard.tsx`:

```tsx
'use client'

import Link from 'next/link'

import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import type { JobPostingSummary, JobStatus } from '@/lib/api/jobs'
import { postedAgo } from '@/lib/utils'

interface Props {
  job: JobPostingSummary
}

// Cycled palette for the stacked stage bar — keeps stages visually
// distinct without depending on a stage-type → color map (which would
// need to grow whenever pipeline-stage v6 ships).
const BAR_COLORS = [
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#f59e0b', // amber
  '#10b981', // emerald
  '#ec4899', // pink
  '#6b7280', // gray
] as const

function statusPillStyle(status: JobStatus): { label: string; bg: string; fg: string } {
  if (status === 'active') {
    return { label: 'active', bg: 'rgba(16,185,129,0.12)', fg: '#10b981' }
  }
  // pipeline_built — only other status this card receives (filtered upstream).
  return { label: 'pipeline ready', bg: 'rgba(59,130,246,0.12)', fg: '#3b82f6' }
}

function shortStageLabel(name: string): string {
  // 3-letter abbreviation for the per-stage label row. Keeps the row
  // single-line up to 6 stages wide on a 320px card.
  return name.slice(0, 4)
}

export function TrackerJobCard({ job }: Props) {
  const board = useKanbanBoard(job.id)
  const pill = statusPillStyle(job.status)
  const stages = board.data?.stages ?? []
  const total = stages.reduce((sum, s) => sum + s.candidates.length, 0)
  const hasAny = total > 0

  return (
    <Link
      href={`/tracker/${job.id}`}
      className="flex min-h-[180px] flex-col gap-2.5 rounded-[10px] border p-4 transition-shadow hover:shadow-sm"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div
            className="flex items-center gap-2 truncate text-[14.5px] font-semibold"
            style={{ color: 'var(--px-fg)', lineHeight: 1.3 }}
          >
            <span className="truncate">{job.title}</span>
          </div>
          <div
            className="mt-0.5 truncate text-[11.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            {job.org_unit_name ?? '—'}
          </div>
        </div>
        <span
          className="inline-flex items-center rounded-full px-2 text-[9.5px] font-medium uppercase"
          style={{
            height: 18,
            letterSpacing: '0.4px',
            background: pill.bg,
            color: pill.fg,
          }}
        >
          {pill.label}
        </span>
      </div>

      {/* Stacked bar */}
      {board.isLoading ? (
        <div
          data-testid="tracker-card-bar-loading"
          className="animate-pulse rounded"
          style={{ height: 6, background: 'var(--px-surface-2)' }}
        />
      ) : (
        <div
          className="flex overflow-hidden rounded"
          style={{ height: 6, background: 'var(--px-surface-2)' }}
          aria-label={`Candidate distribution: ${total} total`}
        >
          {hasAny ? (
            stages.map((s, i) => {
              const w = (s.candidates.length / total) * 100
              if (w === 0) return null
              return (
                <div
                  key={s.stage_id}
                  style={{
                    width: `${w}%`,
                    background: BAR_COLORS[i % BAR_COLORS.length],
                  }}
                  title={`${s.stage_name}: ${s.candidates.length}`}
                />
              )
            })
          ) : (
            <div className="w-full" style={{ background: 'var(--px-hairline)' }} />
          )}
        </div>
      )}

      {/* Per-stage labels */}
      {stages.length > 0 && (
        <div
          className="flex items-center gap-2 truncate text-[10px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          {stages.map((s) => (
            <span key={s.stage_id} className="truncate">
              {shortStageLabel(s.stage_name)} {s.candidates.length}
            </span>
          ))}
        </div>
      )}

      <div className="flex-1" />

      {/* Footer */}
      <div
        className="flex items-center justify-between border-t pt-2 text-[11.5px]"
        style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
      >
        {hasAny ? (
          <span>
            <b
              className="px-mono"
              style={{ color: 'var(--px-fg)', fontVariantNumeric: 'tabular-nums' }}
            >
              {total}
            </b>{' '}
            candidates
          </span>
        ) : (
          <span style={{ color: 'var(--px-fg-4)' }}>No candidates yet</span>
        )}
        <span style={{ color: 'var(--px-fg-4)' }}>
          moved {postedAgo(job.updated_at)}
        </span>
      </div>
    </Link>
  )
}
```

- [ ] **Step 4: Run test + type-check**

```bash
npm run test -- tests/components/TrackerJobCard.test.tsx
npm run type-check
```
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/tracker/TrackerJobCard.tsx \
        frontend/app/tests/components/TrackerJobCard.test.tsx
git commit -m "feat(tracker): TrackerJobCard with stacked stage bar + counts"
```

---

## Task 4: Add `/tracker` landing page

**Files:**
- Create: `frontend/app/app/(dashboard)/tracker/page.tsx`
- Create: `frontend/app/app/(dashboard)/tracker/ClientTrackerLandingPage.tsx`

Server `page.tsx` is a thin wrapper around the client component (matches the `candidates/page.tsx` pattern). Landing renders header + filter chips (All / Active / Pipeline ready) + grid of `TrackerJobCard`s. No backend test needed for the page — the hook + card already have coverage; this is composition.

- [ ] **Step 1: Create the server page**

Create `frontend/app/app/(dashboard)/tracker/page.tsx`:

```tsx
import ClientTrackerLandingPage from './ClientTrackerLandingPage'

export default function TrackerPage() {
  return <ClientTrackerLandingPage />
}
```

- [ ] **Step 2: Create the client landing page**

Create `frontend/app/app/(dashboard)/tracker/ClientTrackerLandingPage.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'

import { TrackerJobCard } from '@/components/dashboard/tracker/TrackerJobCard'
import { useTrackerJobs } from '@/lib/hooks/use-tracker-jobs'
import type { JobStatus } from '@/lib/api/jobs'

type FilterId = 'all' | 'active' | 'pipeline_built'

export default function ClientTrackerLandingPage() {
  const { data, isLoading, error } = useTrackerJobs()
  const [filter, setFilter] = useState<FilterId>('all')

  const counts = useMemo(() => {
    const all = data ?? []
    return {
      all: all.length,
      active: all.filter((j) => j.status === 'active').length,
      pipeline_built: all.filter((j) => j.status === 'pipeline_built').length,
    }
  }, [data])

  const visible = useMemo(() => {
    const all = data ?? []
    if (filter === 'all') return all
    return all.filter((j) => j.status === (filter as JobStatus))
  }, [data, filter])

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-[22px]">
      {/* Header */}
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Tracker
          </h1>
          <p
            className="mt-1 text-[12.5px]"
            style={{ color: 'var(--px-fg-3)' }}
          >
            Live boards. Pick a role to see candidates and move them through stages.
          </p>
        </div>
      </div>

      {/* Filter chips */}
      <div className="mb-3.5 flex items-center gap-1.5">
        {(
          [
            { id: 'all' as const, label: 'All', n: counts.all },
            { id: 'active' as const, label: 'Active', n: counts.active },
            {
              id: 'pipeline_built' as const,
              label: 'Pipeline ready',
              n: counts.pipeline_built,
            },
          ]
        ).map((p) => {
          const active = filter === p.id
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setFilter(p.id)}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border text-[12px] transition-colors"
              style={{
                height: 26,
                padding: '0 10px',
                borderColor: active ? 'var(--px-fg-2)' : 'transparent',
                background: active ? 'var(--px-surface)' : 'transparent',
                color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                fontWeight: active ? 500 : 400,
              }}
            >
              {p.label}
              {p.n > 0 && (
                <span
                  className="px-mono text-[10.5px]"
                  style={{
                    color: 'var(--px-fg-4)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {p.n}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Body */}
      {isLoading ? (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="animate-pulse rounded-[10px] border"
              style={{
                height: 180,
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
              }}
            />
          ))}
        </div>
      ) : error ? (
        <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
          Error: {(error as Error).message}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState />
      ) : (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}
        >
          {visible.map((job) => (
            <TrackerJobCard key={job.id} job={job} />
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyState() {
  return (
    <div
      className="rounded-[10px] border p-12 text-center"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <h2
        className="px-serif m-0 mb-2 text-xl"
        style={{ color: 'var(--px-fg)' }}
      >
        No live boards yet
      </h2>
      <p
        className="mx-auto mb-6 max-w-md text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Confirm signals and build a pipeline on a role to make it live.
      </p>
      <Link href="/jobs" className="px-btn primary sm inline-block">
        View roles →
      </Link>
    </div>
  )
}
```

- [ ] **Step 3: Type-check + smoke**

```bash
npm run type-check
npm run dev
```
Open `http://localhost:3000/tracker`. Expected: page renders, shows live jobs (or the empty state if none). Filter chips switch between subsets. Click a card → 404 for now (Task 5 adds the detail route). That's fine — `/tracker` itself must work.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/app/\(dashboard\)/tracker/page.tsx \
        frontend/app/app/\(dashboard\)/tracker/ClientTrackerLandingPage.tsx
git commit -m "feat(tracker): /tracker landing page with filter chips + Rich card grid"
```

---

## Task 5: Add `/tracker/[jobId]` route + `TrackerKanbanPage`

**Files:**
- Create: `frontend/app/app/(dashboard)/tracker/[jobId]/page.tsx`
- Create: `frontend/app/components/dashboard/tracker/TrackerKanbanPage.tsx`

Per `frontend/app/AGENTS.md`: Next 16 has breaking changes. **Before writing the route, read `frontend/app/node_modules/next/dist/docs/` for the App Router params convention** — recent versions made `params` a Promise in server components. The code below assumes async params; verify against the installed docs and adjust if the convention has shifted again.

The page is a thin wrapper. `TrackerKanbanPage` renders the header + tip banner + `<CandidateKanbanView />`. No new tests — the kanban view's behavior is unchanged and composition is trivial.

- [ ] **Step 1: Verify the Next 16 params convention**

```bash
ls frontend/app/node_modules/next/dist/docs/ 2>/dev/null | head
grep -rn "params:" frontend/app/app/\(dashboard\)/jobs/\[jobId\]/page.tsx | head -3
```
Inspect how the existing `/jobs/[jobId]/page.tsx` handles params and mirror that exactly. If it `await`s `params`, do the same below; if it destructures synchronously, drop the `await`.

- [ ] **Step 2: Create the server route**

Create `frontend/app/app/(dashboard)/tracker/[jobId]/page.tsx` — adjust the `params` shape if Step 1 showed a different convention:

```tsx
import { TrackerKanbanPage } from '@/components/dashboard/tracker/TrackerKanbanPage'

export default async function TrackerBoardPage({
  params,
}: {
  params: Promise<{ jobId: string }>
}) {
  const { jobId } = await params
  return <TrackerKanbanPage jobId={jobId} />
}
```

- [ ] **Step 3: Create `TrackerKanbanPage`**

Create `frontend/app/components/dashboard/tracker/TrackerKanbanPage.tsx`:

```tsx
'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

import CandidateKanbanView from '@/components/dashboard/tracker/CandidateKanbanView'
import { useJob } from '@/lib/hooks/use-job'
import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { postedAgo } from '@/lib/utils'

const TIP_KEY = 'tracker-board-tip-dismissed'

interface Props {
  jobId: string
}

export function TrackerKanbanPage({ jobId }: Props) {
  const job = useJob(jobId)
  const board = useKanbanBoard(jobId)

  const [tipDismissed, setTipDismissed] = useState(true)
  useEffect(() => {
    // Read after mount to avoid SSR/CSR mismatch.
    setTipDismissed(localStorage.getItem(TIP_KEY) === '1')
  }, [])

  const total =
    board.data?.stages.reduce((sum, s) => sum + s.candidates.length, 0) ?? 0
  const inMotion =
    board.data?.stages
      .filter((s) => s.candidates.length > 0)
      .reduce((sum, s) => sum + s.candidates.length, 0) ?? 0

  if (job.error) {
    return (
      <div className="mx-auto max-w-[800px] px-8 pt-12 text-center">
        <h2
          className="px-serif text-2xl"
          style={{ color: 'var(--px-fg)' }}
        >
          This role no longer exists
        </h2>
        <Link href="/tracker" className="px-btn primary sm mt-6 inline-block">
          ← Back to Tracker
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] px-8 pb-10 pt-5">
      {/* Header */}
      <div className="mb-3 flex items-end gap-3">
        <div className="min-w-0">
          <h1
            className="px-serif m-0 truncate text-[24px] font-normal"
            style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
          >
            {job.data?.title ?? 'Loading…'}
          </h1>
          <div
            className="mt-1 flex items-center gap-2 text-[11.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            {job.data?.org_unit_name && <span>{job.data.org_unit_name}</span>}
            {job.data?.org_unit_name && <span>·</span>}
            <span>{total} candidates</span>
            <span>·</span>
            <span>{inMotion} in motion</span>
            {job.data?.updated_at && (
              <>
                <span>·</span>
                <span>last move {postedAgo(job.data.updated_at)}</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tip banner */}
      {!tipDismissed && (
        <div
          className="mb-4 flex items-center gap-3 rounded-md border px-3 py-2 text-[12px]"
          style={{
            background: 'var(--px-surface-2)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-3)',
          }}
        >
          <span className="flex-1">
            Drag a card across columns to advance a candidate. Click a card to
            open their profile.
          </span>
          <button
            type="button"
            onClick={() => {
              localStorage.setItem(TIP_KEY, '1')
              setTipDismissed(true)
            }}
            className="px-btn ghost xs"
            aria-label="Dismiss tip"
          >
            Got it
          </button>
        </div>
      )}

      <CandidateKanbanView jobId={jobId} />
    </div>
  )
}
```

- [ ] **Step 4: Type-check + smoke**

```bash
npm run type-check
npm run dev
```
Open `http://localhost:3000/tracker/<live-job-uuid>` — expected: header + tip banner + kanban board. DnD works. Click a candidate card → routes to `/candidates/[candidateId]`. Dismissing the tip persists across reload.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/app/\(dashboard\)/tracker/\[jobId\]/page.tsx \
        frontend/app/components/dashboard/tracker/TrackerKanbanPage.tsx
git commit -m "feat(tracker): /tracker/[jobId] kanban detail page"
```

---

## Task 6: Add Tracker entry to the sidebar nav

**Files:**
- Modify: `frontend/app/components/dashboard/AppShell.tsx`

Insert nav entry between Candidates and Pipeline, swap `/pipeline` to a new layered glyph so the kanban-board glyph belongs to Tracker, add the breadcrumb label.

- [ ] **Step 1: Add the new icon glyph**

In `frontend/app/components/dashboard/AppShell.tsx`, find the `NI` object (line ~49). Add `layers` after `kanban`:

```ts
kanban: ["M3 3h7v18H3zM14 3h7v12h-7z"] as const,
layers: [
  "M12 2L2 7l10 5 10-5-10-5z",
  "M2 17l10 5 10-5",
  "M2 12l10 5 10-5",
] as const,
```

- [ ] **Step 2: Update PRIMARY_NAV — insert Tracker, swap Pipeline icon**

In the same file, find `PRIMARY_NAV` (line ~123):

```ts
const PRIMARY_NAV: readonly NavItem[] = [
  { href: "/", label: "Home", icon: NI.home, kbd: "H" },
  { href: "/jobs", label: "Roles", icon: NI.briefcase, kbd: "R" },
  { href: "/candidates", label: "Candidates", icon: NI.users, kbd: "C" },
  { href: "/tracker", label: "Tracker", icon: NI.kanban, kbd: "T" },
  { href: "/pipeline", label: "Pipeline", icon: NI.layers, kbd: "P" },
  { href: "/questions", label: "Question bank", icon: NI.book, kbd: "Q" },
  { href: "/reports", label: "Reports", icon: NI.chart },
] as const;
```

(Tracker added between Candidates and Pipeline; Pipeline icon flipped from `NI.kanban` to `NI.layers`.)

- [ ] **Step 3: Add the breadcrumb label**

In the same file, find `PATH_LABELS` (line ~156). Add a `tracker` entry:

```ts
const PATH_LABELS: Record<string, string> = {
  "": "Home",
  jobs: "Roles",
  new: "New",
  candidates: "Candidates",
  tracker: "Tracker",
  pipeline: "Pipeline",
  // …rest unchanged
};
```

- [ ] **Step 4: Type-check + smoke**

```bash
npm run type-check
npm run dev
```
Sidebar shows Tracker between Candidates and Pipeline with the kanban glyph; Pipeline shows the new layered glyph. Pressing `T` (no — it's not bound; `kbd` is decorative only). Visit `/tracker` and confirm the breadcrumb reads `Tracker` (and `Tracker › Detail` on the detail page — UUID renders as `Detail` per `humanizeSlug`, that's expected).

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/AppShell.tsx
git commit -m "feat(tracker): sidebar nav entry + dedicated /pipeline glyph"
```

---

## Task 7: Add legacy-URL redirect to `proxy.ts` + tests

**Files:**
- Modify: `frontend/app/proxy.ts`
- Test: `frontend/app/tests/proxy/legacy-redirect.test.ts` (new)

Extract a pure helper `checkLegacyRedirect(url: URL): URL | null` so the redirect logic is testable without instantiating a `NextRequest` (which depends on Edge Runtime and is awkward in jsdom). The proxy then calls the helper and wraps the result in a `NextResponse.redirect(..., 308)`.

- [ ] **Step 1: Write the failing test**

Create `frontend/app/tests/proxy/legacy-redirect.test.ts`:

```ts
import { describe, expect, it } from 'vitest'

import { checkLegacyRedirect } from '@/proxy'

describe('checkLegacyRedirect', () => {
  it('redirects /candidates?jd=<uuid>&view=kanban to /tracker/<uuid>', () => {
    const u = new URL(
      'http://localhost:3000/candidates?jd=488d1ded-0990-4aca-8bf4-2b6e6287d08c&view=kanban',
    )
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker/488d1ded-0990-4aca-8bf4-2b6e6287d08c')
    expect(target?.search).toBe('')
  })

  it('redirects /candidates?view=kanban (no jd) to /tracker', () => {
    const u = new URL('http://localhost:3000/candidates?view=kanban')
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker')
  })

  it('redirects to /tracker (not /tracker/<garbage>) when jd is not a UUID', () => {
    const u = new URL(
      'http://localhost:3000/candidates?jd=https://evil.example.com&view=kanban',
    )
    const target = checkLegacyRedirect(u)
    expect(target?.pathname).toBe('/tracker')
  })

  it('returns null for /candidates without view=kanban', () => {
    const u = new URL('http://localhost:3000/candidates?jd=anything')
    expect(checkLegacyRedirect(u)).toBeNull()
  })

  it('returns null for unrelated paths', () => {
    expect(checkLegacyRedirect(new URL('http://localhost:3000/jobs'))).toBeNull()
    expect(checkLegacyRedirect(new URL('http://localhost:3000/tracker'))).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm run test -- tests/proxy/legacy-redirect.test.ts
```
Expected: FAIL — `checkLegacyRedirect` not exported from `@/proxy`.

- [ ] **Step 3: Add `checkLegacyRedirect` to `proxy.ts` and wire it into `proxy()`**

Edit `frontend/app/proxy.ts`. Add the helper just below the imports (above `PUBLIC_PATHS`):

```ts
const UUID_RE = /^[0-9a-f-]{36}$/i

/**
 * Translate the legacy `/candidates?jd=<uuid>&view=kanban` URL to the new
 * `/tracker/<uuid>` surface. Returns the target URL when a redirect should
 * fire, or null otherwise.
 *
 * The UUID regex guards against open-redirect via crafted `jd` values.
 * Mirrors the redirect-allowlist pattern used by `app/(auth)/invite/page.tsx`.
 *
 * Exported so the rule is unit-testable without spinning up a NextRequest.
 */
export function checkLegacyRedirect(url: URL): URL | null {
  if (
    url.pathname === '/candidates' &&
    url.searchParams.get('view') === 'kanban'
  ) {
    const jd = url.searchParams.get('jd') ?? ''
    const target = UUID_RE.test(jd) ? `/tracker/${jd}` : '/tracker'
    return new URL(target, url)
  }
  return null
}
```

Then near the top of `proxy()` — immediately after `const path = request.nextUrl.pathname;` (line ~30) — insert:

```ts
  const legacy = checkLegacyRedirect(request.nextUrl)
  if (legacy) {
    return NextResponse.redirect(legacy, 308)
  }
```

(The redirect runs before the auth gate: a logged-out user landing on the legacy URL still gets routed correctly; the dashboard guard then kicks in on `/tracker/<uuid>` if they're not authenticated.)

- [ ] **Step 4: Run test to verify it passes**

```bash
npm run test -- tests/proxy/legacy-redirect.test.ts
```
Expected: PASS.

- [ ] **Step 5: Manual smoke — exercise the redirect end-to-end**

```bash
npm run dev
```
Open `http://localhost:3000/candidates?jd=<live-uuid>&view=kanban`. Expected: browser address bar lands on `/tracker/<live-uuid>` (308). Open `http://localhost:3000/candidates?view=kanban` (no jd). Expected: lands on `/tracker`. Open `http://localhost:3000/candidates?jd=not-a-uuid&view=kanban`. Expected: lands on `/tracker` (NOT `/tracker/not-a-uuid`).

- [ ] **Step 6: Commit**

```bash
git add frontend/app/proxy.ts frontend/app/tests/proxy/legacy-redirect.test.ts
git commit -m "feat(proxy): 308 redirect /candidates?view=kanban → /tracker"
```

---

## Task 8: Strip kanban from `/candidates` + update assignment-tab link

**Files:**
- Modify: `frontend/app/app/(dashboard)/candidates/ClientCandidatesPage.tsx`
- Modify: `frontend/app/app/(dashboard)/candidates/[candidateId]/CandidateAssignmentsTab.tsx` (line 273)

`/candidates` becomes list-only. The view toggle, kanban branch, and `view` URL param go away. The deep link from the candidate detail page swings over to `/tracker/[jobId]`.

- [ ] **Step 1: Rewrite `ClientCandidatesPage.tsx`**

Open `frontend/app/app/(dashboard)/candidates/ClientCandidatesPage.tsx` and replace the entire file with:

```tsx
'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useMemo, useState } from 'react'

import { JdPicker } from '@/components/dashboard/candidates/JdPicker'
import { Button } from '@/components/px'

import AddCandidateDialog from './AddCandidateDialog'
import CandidateListView from './CandidateListView'

export default function ClientCandidatesPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const jd = searchParams.get('jd')
  const q = searchParams.get('q') ?? ''
  const status = searchParams.get('status') ?? ''
  const stageId = searchParams.get('stage_id') ?? ''
  const offsetRaw = searchParams.get('offset')
  const offset = offsetRaw ? Math.max(0, Number.parseInt(offsetRaw, 10) || 0) : 0

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString())
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === '') params.delete(key)
        else params.set(key, value)
      }
      const qs = params.toString()
      router.replace(`/candidates${qs ? `?${qs}` : ''}`, { scroll: false })
    },
    [router, searchParams],
  )

  const listFilters = useMemo(
    () => ({
      q,
      status,
      jobId: jd ?? '',
      stageId,
      offset,
    }),
    [q, status, jd, stageId, offset],
  )

  const [showAddDialog, setShowAddDialog] = useState(false)

  return (
    <div className="mx-auto max-w-[1600px] px-8 pb-10 pt-5">
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Candidates
          </h1>
          <p
            className="mt-1 text-[12.5px]"
            style={{ color: 'var(--px-fg-3)' }}
          >
            Search and triage candidates across roles. Open Tracker to see the
            board view per role.
          </p>
        </div>
        <Button size="sm" onClick={() => setShowAddDialog(true)}>
          + Add candidate
        </Button>
      </div>

      <AddCandidateDialog
        open={showAddDialog}
        onOpenChange={setShowAddDialog}
      />

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label
            htmlFor="candidates-jd-picker"
            className="text-[12px] font-medium"
            style={{ color: 'var(--px-fg-2)' }}
          >
            Role:
          </label>
          <JdPicker
            value={jd}
            onChange={(next) => {
              updateParams({ jd: next })
            }}
          />
        </div>
      </div>

      <CandidateListView
        filters={listFilters}
        onFiltersChange={updateParams}
      />
    </div>
  )
}
```

- [ ] **Step 2: Update the assignment-tab link**

In `frontend/app/app/(dashboard)/candidates/[candidateId]/CandidateAssignmentsTab.tsx`, find line 273 (the existing `<Link href={\`/candidates?jd=...&view=kanban\`}>`):

```tsx
// before
<Link
  href={`/candidates?jd=${assignment.job_posting_id}&view=kanban`}
  className="text-zinc-900 hover:text-zinc-700 hover:underline"
>
  {assignment.job_title || 'Untitled job'}
</Link>

// after
<Link
  href={`/tracker/${assignment.job_posting_id}`}
  className="text-zinc-900 hover:text-zinc-700 hover:underline"
>
  {assignment.job_title || 'Untitled job'}
</Link>
```

- [ ] **Step 3: Verify no leftover references**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
grep -rn "view=kanban\|view: 'kanban'" app/ components/ lib/ tests/ 2>/dev/null | grep -v node_modules | grep -v '.next'
```
Expected: only matches inside `proxy.ts` and `tests/proxy/legacy-redirect.test.ts` (the redirect rule and its tests). Anything else means a leftover reference — fix before continuing.

- [ ] **Step 4: Type-check + run full test suite**

```bash
npm run type-check
npm run test
```
Expected: both PASS.

- [ ] **Step 5: Manual smoke**

```bash
npm run dev
```
- `/candidates` shows list view, no view-toggle buttons, subtitle reads new copy.
- Open any candidate's detail page → Assignments tab → click a job title. Expected: lands on `/tracker/<jobId>`.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/app/\(dashboard\)/candidates/ClientCandidatesPage.tsx \
        frontend/app/app/\(dashboard\)/candidates/\[candidateId\]/CandidateAssignmentsTab.tsx
git commit -m "refactor(candidates): drop kanban view; link assignments to /tracker"
```

---

## Task 9: Final gates — lint, type-check, tests, build, smoke

**Files:** none — verification only.

The whole feature must pass every CI gate from `frontend/app/CLAUDE.md`. Anything red means the implementation isn't done.

- [ ] **Step 1: Lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run lint
```
Expected: zero errors. Fix any warning that surfaces as error before continuing.

- [ ] **Step 2: Type-check**

```bash
npm run type-check
```
Expected: zero errors.

- [ ] **Step 3: Full test suite**

```bash
npm run test
```
Expected: zero failures. The new tests added in this plan:
- `tests/lib/posted-ago.test.ts` (5 cases)
- `tests/lib/hooks/use-tracker-jobs.test.ts` (1 case)
- `tests/components/TrackerJobCard.test.tsx` (3 cases)
- `tests/proxy/legacy-redirect.test.ts` (5 cases)

- [ ] **Step 4: Production build**

```bash
npm run build
```
Expected: build succeeds. New routes `/tracker` and `/tracker/[jobId]` show up in the route summary printed at the end. First-load JS budget for both must stay under 250 KB gzipped (per CLAUDE.md). If either exceeds, file a follow-up — the only realistic culprit would be re-importing something heavy on the landing page; verify by reading the build's bundle analysis output.

- [ ] **Step 5: End-to-end manual smoke**

```bash
npm run dev
```

Walk through the full user journey:

1. Sidebar shows **Tracker** between Candidates and Pipeline (kanban glyph). `/pipeline` shows the new layered glyph.
2. Click Tracker → `/tracker` lists live jobs as Rich cards. Filter chips switch All / Active / Pipeline ready.
3. Click any card → `/tracker/<jobId>`. Header shows title + org + counts. Tip banner present (dismissible, persists across reload).
4. Drag a candidate card across columns → optimistic move + toast on failure (already covered by the existing `useTransitionCandidate`).
5. Click a candidate avatar → `/candidates/<candidateId>`. Open Assignments tab, click a job title. Expected: lands on `/tracker/<jobId>`, NOT `/candidates?jd=...&view=kanban`.
6. Manually paste `http://localhost:3000/candidates?jd=<live-uuid>&view=kanban` into the URL bar. Expected: 308 redirect to `/tracker/<live-uuid>`.
7. Manually paste `http://localhost:3000/candidates?jd=not-a-uuid&view=kanban`. Expected: redirect to `/tracker` (landing).
8. Open `/candidates`. Expected: no view-toggle buttons. List view only. Subtitle reads new copy.

- [ ] **Step 6: No commit needed for verification**

If anything in steps 1–5 failed, return to the relevant earlier task and fix. Do not paper over with a follow-up commit unless it's a typo discovered during smoke.

---

## Self-review notes (post-write)

Spec coverage check vs. `docs/superpowers/specs/2026-05-15-tracker-page-design.md`:

| Spec section | Covered by |
|---|---|
| Architecture / file layout | Tasks 1–5, 8 |
| `useTrackerJobs` data flow | Task 2 |
| Per-card kanban roll-up note | Task 3 (one `useKanbanBoard` per card; documented in spec as soft spot) |
| Landing header + filter chips + grid | Task 4 |
| Landing states (loading / error / empty / no candidates) | Task 4 (loading skeleton + EmptyState component) + Task 3 (no-candidates branch) |
| Detail page shell + 404 / 403 | Task 5 (404 branch via `useJob.error`; 403 falls through to existing dashboard `<AccessDenied>` pattern — no extra wiring needed because `useJob` surfaces the error) |
| Header + metadata strip + tip banner | Task 5 |
| Reuse of `CandidateKanbanView` verbatim | Task 5 (imported as-is post-Task-1 move) |
| Sidebar nav + glyph swap + breadcrumb | Task 6 |
| `proxy.ts` redirect (308, UUID guard, fallbacks) + tests | Task 7 |
| `/candidates` cleanup + assignment-tab link | Task 8 |
| Test coverage gates from spec | Tasks 0, 2, 3, 7 |
| Build sequence in spec | Tasks ordered to match (extract → move → hook → card → landing → detail → nav → proxy → cleanup → verify) |

No placeholders. No TBDs. No "similar to Task N" hand-waves. Type names (`JobPostingSummary`, `JobStatus`, `KanbanBoardResponse`, `KanbanColumn`) are consistent across tasks and match the existing `lib/api/*` definitions.
