# Reports Hub (`/reports` index) Implementation Plan

> Follow-on to the A2 recruiter report UI. REQUIRED SUB-SKILL: subagent-driven-development.

**Goal:** Turn the `/reports` placeholder into a usable hub: a table of completed **v2** interview sessions for the tenant, each showing report status + verdict and linking to (or generating) its per-session report.

**Architecture:** One new read-only backend endpoint `GET /api/reports` (reporting module) joins session → candidate → job → stage + report status, tenant-scoped (explicit `WHERE tenant_id` + RLS), RBAC = `reports.view`/super-admin. Frontend adds `reportsApi.list` + `useReportsIndex` and replaces `app/(dashboard)/reports/page.tsx` with a table that reuses the A2 components (VerdictChip, tones). Generate is super-admin-only and reuses `useRegenerateReport`.

**Decision (user, 2026-05-26):** `/reports` becomes the hub; the funnel/time-to-hire analytics placeholder is removed (a later separate page).

**Backend facts (verified):** `sessions` has `state`, `completed_at`, `created_at`, `tenant_id`, `assignment_id`, `stage_id`. `candidate_job_assignments(id, candidate_id, job_posting_id)`, `candidates(id, name)`, `job_postings(id, title, interview_engine_version)`, `job_pipeline_stages(id, name)`, `session_reports(session_id, status, verdict, overall_score, id)`. All tenant-scoped + RLS. The reporting router uses a `_require_reports_view(user)` helper and a global rate-limit (no per-route decorator). Tests live in `tests/reporting/test_router.py` and use `seed_minimal_session(db, state=...)` + `_seed_report(...)` + `_setup_test_context(...)`.

---

### Task H1 (backend): `GET /api/reports` index endpoint

**Files:**
- Modify: `backend/nexus/app/modules/reporting/schemas.py` (add 2 schemas)
- Modify: `backend/nexus/app/modules/reporting/router.py` (add endpoint)
- Modify: `backend/nexus/tests/reporting/test_router.py` (add 3 tests)

**Test command:** `docker compose run --rm -e PYTHONPATH=/app nexus pytest tests/reporting/test_router.py -q` (a one-off container — NOT the live `nexus`; avoids the `--reload`/reaper wedge). Run from `backend/nexus`.

- [ ] **Step 1: Add schemas** to `schemas.py` (after `ReportRead`):

```python
class ReportIndexItem(BaseModel):
    """One row in the /reports hub: a completed session + its report status."""
    session_id: str
    candidate_id: str | None = None
    candidate_name: str | None = None
    job_title: str | None = None
    stage_name: str | None = None
    completed_at: str | None = None
    report_status: str  # none | pending | generating | ready | failed
    verdict: Verdict | None = None
    overall_score: int | None = None


class ReportIndexPage(BaseModel):
    items: list[ReportIndexItem]
    total: int
    offset: int
    limit: int
```

(`Verdict` is already imported in schemas.py from `app.modules.reporting.scoring.types`.)

- [ ] **Step 2: Add the failing tests** to `tests/reporting/test_router.py` (append):

```python
# ---------------------------------------------------------------------------
# Tests: GET /api/reports (index)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_index_lists_completed_with_report(db: AsyncSession):
    """GET /api/reports → completed session with a ready report appears."""
    session_row, tenant_id = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(db, user_row, tenant_id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers)
    finally:
        restore()

    assert r.status_code == 200, r.text
    body = r.json()
    item = next(
        (it for it in body["items"] if it["session_id"] == str(session_row.id)),
        None,
    )
    assert item is not None, body
    assert item["report_status"] == "ready"
    assert item["verdict"] == "advance"
    assert item["overall_score"] == 85


@pytest.mark.asyncio
async def test_report_index_missing_permission_403(db: AsyncSession):
    """GET /api/reports without reports.view → 403."""
    session_row, tenant_id = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_row, tenant_id, status="ready")
    user_row = await _get_user_for_tenant(db, tenant_id)

    headers, restore = _setup_test_context(
        db, user_row, tenant_id, permissions=("candidates.view",)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers)
    finally:
        restore()

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_report_index_cross_tenant_excluded(db: AsyncSession):
    """Tenant B's index must not contain tenant A's completed session."""
    session_a, tenant_a = await seed_minimal_session(db, state="completed")
    await _seed_report(db, session_a, tenant_a, status="ready")

    client_b = await create_test_client(db)
    user_b = await create_test_user(db, client_b.id)

    headers_b, restore_b = _setup_test_context(db, user_b, client_b.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/reports", headers=headers_b)
    finally:
        restore_b()

    assert r.status_code == 200, r.text
    sids = [it["session_id"] for it in r.json()["items"]]
    assert str(session_a.id) not in sids
```

Run the test command → these FAIL (endpoint returns 404 / route missing).

- [ ] **Step 3: Implement the endpoint** in `router.py`. Add `Query` to the `fastapi` import and `text` to the `sqlalchemy` import at the top:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text
```

Add the schema imports:

```python
from app.modules.reporting.schemas import (
    HumanDecisionIn,
    ReportIndexItem,
    ReportIndexPage,
    ReportRead,
)
```

Add this endpoint **immediately after** the `get_report_by_session` handler (so the literal `""` path is registered before `/{report_id}`; FastAPI exact-matches `/api/reports` regardless, but keep it tidy):

```python
# ---------------------------------------------------------------------------
# GET /api/reports  — hub index of completed sessions + report status
# ---------------------------------------------------------------------------


@router.get("", summary="List completed sessions with report status (hub)")
async def list_report_index(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> Any:
    """Paginated list of completed sessions that are reportable.

    A session appears when it is `completed` AND (already has a report OR runs
    the v2 engine, i.e. is scoreable). Tenant-scoped by an explicit
    `s.tenant_id` filter (works in tests where RLS is disabled) plus RLS in
    prod. RBAC: reports.view or super-admin.
    """
    _require_reports_view(user)
    tenant_id: uuid_mod.UUID = user.user.tenant_id

    base = """
        FROM sessions s
        LEFT JOIN candidate_job_assignments a ON a.id = s.assignment_id
        LEFT JOIN candidates c ON c.id = a.candidate_id
        LEFT JOIN job_postings j ON j.id = a.job_posting_id
        LEFT JOIN job_pipeline_stages st ON st.id = s.stage_id
        LEFT JOIN session_reports sr ON sr.session_id = s.id
        WHERE s.tenant_id = :tenant_id
          AND s.state = 'completed'
          AND (sr.id IS NOT NULL OR j.interview_engine_version = 'v2')
    """
    params: dict[str, Any] = {"tenant_id": str(tenant_id)}

    total = (
        await db.execute(text("SELECT count(*) " + base), params)
    ).scalar_one()

    rows = (
        await db.execute(
            text(
                "SELECT s.id AS session_id, a.candidate_id, c.name AS candidate_name, "
                "j.title AS job_title, st.name AS stage_name, s.completed_at, "
                "COALESCE(sr.status, 'none') AS report_status, sr.verdict AS verdict, "
                "sr.overall_score AS overall_score "
                + base
                + " ORDER BY s.completed_at DESC NULLS LAST, s.created_at DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).mappings().all()

    items = [
        ReportIndexItem(
            session_id=str(r["session_id"]),
            candidate_id=str(r["candidate_id"]) if r["candidate_id"] else None,
            candidate_name=r["candidate_name"],
            job_title=r["job_title"],
            stage_name=r["stage_name"],
            completed_at=r["completed_at"].isoformat() if r["completed_at"] else None,
            report_status=r["report_status"],
            verdict=r["verdict"],
            overall_score=r["overall_score"],
        )
        for r in rows
    ]
    return ReportIndexPage(
        items=items, total=int(total), offset=offset, limit=limit
    ).model_dump(mode="json")
```

- [ ] **Step 4: Run the test command** → 3 new tests PASS (and the existing reporting router tests still pass). If the live `nexus` API needs the route for the running dev server, restart it: `docker compose up -d --force-recreate nexus`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/schemas.py backend/nexus/app/modules/reporting/router.py backend/nexus/tests/reporting/test_router.py
git commit -m "feat(reporting): GET /api/reports hub index (completed v2 sessions + report status)"
```

---

### Task H2 (frontend): API list method + `useReportsIndex` hook

**Files:**
- Modify: `frontend/app/lib/api/reports.ts` (add types + `reportsApi.list`)
- Modify: `frontend/app/lib/hooks/use-report.ts` (add `useReportsIndex`)
- Test: `frontend/app/tests/api/reports-index.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/tests/api/reports-index.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reportsApi } from '@/lib/api/reports'

const PAGE = {
  items: [
    { session_id: 's1', candidate_id: 'c1', candidate_name: 'Punar', job_title: 'FDE',
      stage_name: 'New Stage', completed_at: '2026-05-24T00:00:00Z',
      report_status: 'ready', verdict: 'reject', overall_score: 36 },
  ],
  total: 1, offset: 0, limit: 50,
}

afterEach(() => vi.unstubAllGlobals())

describe('reportsApi.list', () => {
  it('GETs /api/reports and returns the page', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => PAGE } as Response)
    vi.stubGlobal('fetch', fetchMock)
    const page = await reportsApi.list('tok')
    expect(page.items[0].candidate_name).toBe('Punar')
    expect(fetchMock.mock.calls[0][0]).toContain('/api/reports')
  })
})
```

- [ ] **Step 2: Run** `npm run test -- tests/api/reports-index.test.ts` (from `frontend/app`) → FAIL (`reportsApi.list` undefined).

- [ ] **Step 3: Add types + method** to `lib/api/reports.ts`. After the `ReportRead` interface, add:

```ts
export interface ReportIndexItem {
  session_id: string
  candidate_id: string | null
  candidate_name: string | null
  job_title: string | null
  stage_name: string | null
  completed_at: string | null
  report_status: 'none' | 'pending' | 'generating' | 'ready' | 'failed'
  verdict: Verdict | null
  overall_score: number | null
}

export interface ReportIndexPage {
  items: ReportIndexItem[]
  total: number
  offset: number
  limit: number
}
```

Inside the `reportsApi` object, add a `list` method (alongside `getBySession`):

```ts
  list: (
    token: string,
    opts?: { offset?: number; limit?: number; signal?: AbortSignal },
  ): Promise<ReportIndexPage> => {
    const params = new URLSearchParams()
    if (opts?.offset != null) params.set('offset', String(opts.offset))
    if (opts?.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return apiFetch<ReportIndexPage>(`/api/reports${qs ? `?${qs}` : ''}`, {
      token,
      signal: opts?.signal,
    })
  },
```

- [ ] **Step 4: Add the hook** to `lib/hooks/use-report.ts`. Add `ReportIndexPage` to the `reportsApi` type import, and append:

```ts
export function useReportsIndex() {
  return useQuery<ReportIndexPage>({
    queryKey: ['reports-index'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.list(token, { signal })
    },
    // While any row is mid-generation, poll so it flips to ready/failed live.
    refetchInterval: (q) => {
      const items = q.state.data?.items ?? []
      return items.some(
        (i) => i.report_status === 'pending' || i.report_status === 'generating',
      )
        ? 5000
        : false
    },
  })
}
```

(Update the import: `import { reportsApi, type HumanDecisionIn, type ReportEnvelope, type ReportIndexPage, type ReportRead } from '@/lib/api/reports'`.)

- [ ] **Step 5: Run** `npm run test -- tests/api/reports-index.test.ts` → PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/lib/api/reports.ts frontend/app/lib/hooks/use-report.ts frontend/app/tests/api/reports-index.test.ts
git commit -m "feat(reports): reportsApi.list + useReportsIndex hook"
```

---

### Task H3 (frontend): `/reports` hub page (replace placeholder) + full gate

**Files:**
- Replace: `frontend/app/app/(dashboard)/reports/page.tsx`
- Test: `frontend/app/tests/components/reports/ReportsHubPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/app/tests/components/reports/ReportsHubPage.test.tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/tests/_utils/render'

vi.mock('@/lib/auth/tokens', () => ({ getFreshSupabaseToken: vi.fn().mockResolvedValue('tok') }))

let mockSuperAdmin = false
vi.mock('@/lib/hooks/use-me', () => ({ useMe: () => ({ data: { is_super_admin: mockSuperAdmin } }) }))

import ReportsPage from '@/app/(dashboard)/reports/page'

const PAGE = {
  items: [
    { session_id: 's-ready', candidate_id: 'c1', candidate_name: 'Punar', job_title: 'FDE',
      stage_name: 'New Stage', completed_at: '2026-05-24T00:00:00Z',
      report_status: 'ready', verdict: 'reject', overall_score: 36 },
    { session_id: 's-none', candidate_id: 'c2', candidate_name: 'Ishant', job_title: 'CA',
      stage_name: 'Bot Screening', completed_at: '2026-05-23T00:00:00Z',
      report_status: 'none', verdict: null, overall_score: null },
  ],
  total: 2, offset: 0, limit: 50,
}

function stub() {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => PAGE } as Response))
}

afterEach(() => { vi.unstubAllGlobals(); mockSuperAdmin = false })

describe('ReportsPage (hub)', () => {
  it('lists sessions; a ready row links to its report', async () => {
    stub()
    renderWithProviders(<ReportsPage />)
    expect(await screen.findByText('Punar')).toBeInTheDocument()
    expect(screen.getByText('Ishant')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /view report/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('/reports/session/s-ready'))
  })

  it('shows Generate for an ungenerated row only to super-admin', async () => {
    mockSuperAdmin = true
    stub()
    renderWithProviders(<ReportsPage />)
    await screen.findByText('Ishant')
    expect(screen.getByRole('button', { name: /generate/i })).toBeInTheDocument()
  })

  it('hides Generate from non-super-admin', async () => {
    mockSuperAdmin = false
    stub()
    renderWithProviders(<ReportsPage />)
    await screen.findByText('Ishant')
    expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run** `npm run test -- tests/components/reports/ReportsHubPage.test.tsx` → FAIL (still the placeholder).

- [ ] **Step 3: Replace `page.tsx`** entirely:

```tsx
// frontend/app/app/(dashboard)/reports/page.tsx
'use client'

import Link from 'next/link'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { VerdictChip } from '@/components/dashboard/reports/VerdictBand'
import { scoreToTen } from '@/components/dashboard/reports/report-format'
import type { ReportIndexItem } from '@/lib/api/reports'
import { useMe } from '@/lib/hooks/use-me'
import { useRegenerateReport, useReportsIndex } from '@/lib/hooks/use-report'

const STATUS_LABEL: Record<ReportIndexItem['report_status'], string> = {
  none: 'Not generated',
  pending: 'Generating…',
  generating: 'Generating…',
  ready: 'Ready',
  failed: 'Failed',
}

function reportHref(item: ReportIndexItem): string {
  const p = new URLSearchParams()
  if (item.candidate_id) p.set('candidateId', item.candidate_id)
  if (item.candidate_name) p.set('candidateName', item.candidate_name)
  if (item.job_title) p.set('title', item.job_title)
  if (item.stage_name) p.set('subtitle', item.stage_name)
  return `/reports/session/${item.session_id}?${p.toString()}`
}

export default function ReportsPage() {
  const { data, isLoading, error } = useReportsIndex()
  const { data: me } = useMe()
  const isSuperAdmin = !!me?.is_super_admin

  return (
    <div className="mx-auto max-w-[1200px] px-8 pb-10 pt-5">
      <div className="mb-6">
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          Reports
        </h1>
        <p className="mt-2 text-[13px]" style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}>
          Completed AI interviews and their evaluations. Open a report, or
          generate one for a session that hasn&rsquo;t been scored yet.
        </p>
      </div>

      {isLoading ? (
        <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>Loading…</div>
      ) : error ? (
        <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
          Could not load reports.
        </div>
      ) : !data || data.items.length === 0 ? (
        <div
          className="rounded-[10px] border p-8 text-center"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>No completed interviews yet.</p>
          <p className="mt-1 text-xs" style={{ color: 'var(--px-fg-4)' }}>
            Reports appear here once an AI-screening interview completes.
          </p>
        </div>
      ) : (
        <div
          className="overflow-hidden rounded-[10px] border"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <table className="min-w-full text-left text-[13px]">
            <thead>
              <tr style={{ background: 'var(--px-surface-2)', color: 'var(--px-fg-4)' }}>
                <th className="px-4 py-2.5 font-semibold uppercase tracking-wide text-[10.5px]">Candidate</th>
                <th className="px-4 py-2.5 font-semibold uppercase tracking-wide text-[10.5px]">Role</th>
                <th className="px-4 py-2.5 font-semibold uppercase tracking-wide text-[10.5px]">Stage</th>
                <th className="px-4 py-2.5 font-semibold uppercase tracking-wide text-[10.5px]">Verdict</th>
                <th className="px-4 py-2.5 text-right font-semibold uppercase tracking-wide text-[10.5px]">Score</th>
                <th className="px-4 py-2.5 text-right font-semibold uppercase tracking-wide text-[10.5px]">Action</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <ReportRow key={item.session_id} item={item} isSuperAdmin={isSuperAdmin} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ReportRow({ item, isSuperAdmin }: { item: ReportIndexItem; isSuperAdmin: boolean }) {
  const qc = useQueryClient()
  const regen = useRegenerateReport(item.session_id)
  const hasReport = item.report_status === 'ready' || item.report_status === 'failed'
  const generating = item.report_status === 'pending' || item.report_status === 'generating'
  const ten = scoreToTen(item.overall_score)

  const handleGenerate = () => {
    regen.mutate(undefined, {
      onSuccess: () => {
        toast.success('Report generation started')
        void qc.invalidateQueries({ queryKey: ['reports-index'] })
      },
      onError: (e) => toast.error(e.message || 'Could not start generation'),
    })
  }

  return (
    <tr className="border-t" style={{ borderColor: 'var(--px-hairline)' }}>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg)' }}>{item.candidate_name ?? '—'}</td>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg-2)' }}>{item.job_title ?? '—'}</td>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg-3)' }}>{item.stage_name ?? '—'}</td>
      <td className="px-4 py-2.5">
        {item.verdict ? <VerdictChip verdict={item.verdict} /> : <span style={{ color: 'var(--px-fg-4)' }}>—</span>}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: 'var(--px-fg-2)' }}>{ten ?? '—'}</td>
      <td className="px-4 py-2.5 text-right">
        {hasReport || generating ? (
          <Link href={reportHref(item)} className="text-[12px] font-medium hover:underline" style={{ color: 'var(--px-accent)' }}>
            {generating ? 'Generating…' : 'View report'}
          </Link>
        ) : isSuperAdmin ? (
          <button
            type="button"
            onClick={handleGenerate}
            disabled={regen.isPending}
            className="px-btn outline xs"
          >
            {regen.isPending ? 'Starting…' : 'Generate'}
          </button>
        ) : (
          <span className="text-[12px]" style={{ color: 'var(--px-fg-4)' }}>{STATUS_LABEL[item.report_status]}</span>
        )}
      </td>
    </tr>
  )
}
```

- [ ] **Step 4: Run** `npm run test -- tests/components/reports/ReportsHubPage.test.tsx` → 3 PASS.

- [ ] **Step 5: Full gate** (from `frontend/app`): `npm run lint`, `npm run type-check`, `npm run test`. Zero lint/type errors; the only test failures permitted are the 5 PRE-EXISTING ones in `OrgUnitNode`/`TrackerJobCard`/`use-tracker-jobs` (unrelated). Fix anything in the files you touched.

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/app/(dashboard)/reports/page.tsx" frontend/app/tests/components/reports/ReportsHubPage.test.tsx
git commit -m "feat(reports): /reports hub — sessions table with view/generate"
```

---

## Notes / deferred
- Search/filter by candidate or role, pagination controls (the API supports offset/limit; the page fetches the first 50). Add when volume needs it.
- The funnel/time-to-hire analytics placeholder is removed; a dedicated analytics surface is a later, separate page.
