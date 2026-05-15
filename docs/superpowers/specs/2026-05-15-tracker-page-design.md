# Tracker page — dedicated kanban surface per live job

**Date:** 2026-05-15
**Surface:** `frontend/app` (recruiter dashboard)
**Status:** spec

---

## Problem

The candidate kanban view exists today, but it is buried. To reach it a recruiter
must:

1. Open `/candidates` (lists candidates across every role)
2. Click a candidate row → candidate profile
3. Open the **Assignments** tab
4. Click the assigned job → lands on `/candidates?jd=<uuid>&view=kanban`

There is no surface for "show me the board for role X" — the only way in is via
a candidate detail page. The view itself is also conceptually misplaced inside
`/candidates` (which lists candidates, not boards).

## Goal

Add a dedicated top-level surface — `/tracker` — that lists every **live** job
as a card, and on click navigates to a full-width kanban board for that job.
Remove the kanban view from `/candidates` and redirect the legacy URL to the
new surface.

## Non-goals

- No backend changes. `/api/jobs` (`jobsApi.list`) and
  `/api/candidates/kanban` (`useKanbanBoard`) cover everything we need.
- No new kanban features (bulk move, realtime push, etc.) — single-card DnD
  remains the only mutation path, same as today.
- No org-unit filtering beyond the existing status chip strip — defer until a
  tenant has enough live boards to need it.
- No fix to the breadcrumb UUID-rendering issue (`humanizeSlug` in
  `AppShell.tsx` returns `"Detail"` for any UUID-shaped slug). `/jobs/[id]` has
  the same behavior; out of scope for this PR.

## Decisions made during brainstorming

| Decision | Choice | Reason |
|---|---|---|
| Page flow | Two-page (`/tracker` list → `/tracker/[jobId]` board) | Full-width board, mirrors `/jobs → /jobs/[id]` mental model, bookmarkable per-job URL |
| Surface name | `Tracker` (route `/tracker`) | Backend `ats` module reserves "Applicant tracking"; `Pipeline` is taken; `Tracker` is short and unambiguous |
| Live filter | `status ∈ {pipeline_built, active}` | Strictest definition — only jobs that have kanban columns |
| Old `/candidates` kanban | Remove + 308 redirect | Single source of truth; preserves stale bookmarks |
| Card density | Rich (per-stage bar + counts + last activity) | Lets recruiters spot bottlenecks before opening the board; per-stage counts are already in the kanban response |

## Architecture

### File layout

```
frontend/app/
├── app/(dashboard)/tracker/
│   ├── page.tsx                       NEW — server component, renders <ClientTrackerLandingPage />
│   ├── ClientTrackerLandingPage.tsx   NEW — Rich card grid of live jobs
│   └── [jobId]/
│       └── page.tsx                   NEW — server component, awaits async params,
│                                            renders <TrackerKanbanPage jobId=... />
├── components/dashboard/tracker/
│   ├── TrackerJobCard.tsx             NEW — card with stacked stage bar + counts
│   └── TrackerKanbanPage.tsx          NEW — header + reuses CandidateKanbanView
├── lib/hooks/
│   └── use-tracker-jobs.ts            NEW — derives live jobs from jobsApi.list
└── components/dashboard/AppShell.tsx  EDIT — Tracker nav entry; swap /pipeline icon
```

The existing kanban primitives (`CandidateKanbanView.tsx`,
`CandidateKanbanColumn.tsx`, `CandidateKanbanCard.tsx`) move from
`app/(dashboard)/candidates/` to `components/dashboard/tracker/`. They are
already pure props-in / event-out components — no behavioral change. Tracker is
their only consumer post-refactor.

### Data flow

1. **Landing page** mounts `useTrackerJobs()` — wraps `useQuery(['jobs-list'], jobsApi.list)`,
   filters to `status ∈ {pipeline_built, active}`, sorts by `updated_at` desc.
2. For each visible card, `useKanbanBoard(job.id)` runs in parallel. The card
   renders title/org/status pill immediately from the jobs response; the
   stacked bar and per-stage counts render once the board response arrives
   (skeleton shimmer in the meantime).
3. **Detail page** mounts `<CandidateKanbanView jobId={jobId} />` (reused). The
   `useKanbanBoard(jobId)` query is already cached from step 2 if the user
   came from the landing page — instant render.

### Per-card kanban roll-up — the only soft spot

We fetch per-stage counts by calling `useKanbanBoard(job.id)` per card. With
N live jobs that's N parallel requests on landing-page mount. TanStack Query
parallelizes; for an MVP-scale tenant (< 20 live boards) this is fine.

If a tenant grows past ~30 live boards we should add a backend roll-up endpoint
(e.g. `GET /api/tracker/summary` returning `{job_id, stages: [{id, name, count}]}[]`).
Tracked as follow-up, not a blocker.

## `/tracker` landing page

### Header

- Serif `Tracker` title (30px, `var(--px-fg)`, `letterSpacing: -0.6px`).
- Subtitle (12.5px, `var(--px-fg-3)`):
  `Live boards. Pick a role to see candidates and move them through stages.`
- No "+ Add" CTA — Tracker doesn't create jobs. New jobs are created in
  `/jobs/new`.

### Filter chips (left-aligned)

`All` · `Active` · `Pipeline ready` — derived from `JobStatus`. Default = `All`.
Counts in mono right of label, same visual as the `/jobs` filter strip.

### Card grid

Tailwind `grid` with `grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`
(matches `JobCard` in `/jobs`). Whole card is an `<a href="/tracker/[jobId]">`
for native middle-click / cmd-click behavior.

### Card content (Rich)

1. Title (14.5px semibold) + status pill on the right (`active` green / `pipeline_built` blue)
2. Org unit name (11.5px, `var(--px-fg-4)`)
3. Stacked horizontal bar (6px tall) — one segment per stage, width
   proportional to `stage.candidates.length`. Segment colors cycled from a
   small palette (intake → debrief). Empty stages render as a thin muted
   sliver so the bar doesn't visually disappear.
4. Per-stage labels under the bar (`Intake 3 · Phone 4 · AI 2 · Hum 2 · Deb 1`)
   — clipped with ellipsis if 6+ stages
5. Footer divider, then `<N> candidates` (mono) on the left, `moved <relative-time> ago`
   on the right. `postedAgo()` currently lives inline in `/jobs/page.tsx`;
   Tracker is the second consumer, so extract it to `lib/utils.ts` and have
   both pages import from there.

### States

| State | Render |
|---|---|
| Loading | Skeleton grid — 6 placeholder cards using `<Skeleton />` from `components/px` |
| Error | Toast via existing `lib/auth/handle-error` sink + inline retry message |
| No live jobs | Empty state: `No live boards yet. Confirm signals and build a pipeline on a role to make it live.` with a `View roles →` link to `/jobs` |
| Job has zero candidates | Card renders, bar shows a single muted segment labeled "No candidates yet" |

## `/tracker/[jobId]` kanban page

### Page shell

- `app/(dashboard)/tracker/[jobId]/page.tsx` — server component, awaits Next 16
  async `params` per `frontend/app/AGENTS.md`. Renders `<TrackerKanbanPage jobId={params.jobId} />`.
- Auth gate is the dashboard `layout.tsx` — no extra check needed.
- 404 (job missing): `useJob(jobId)` returns 404 → render an empty state
  ("This role no longer exists.") with a link back to `/tracker`. Same pattern
  as `/jobs/[id]`.
- 403 (cross-tenant or no ancestry access): backend already enforces
  ancestry-walking authz on `/api/jobs/:id` — surface as the standard
  `<AccessDenied>` panel.

### Header (rendered by `TrackerKanbanPage`)

Mirrors `/jobs/[id]` style:

- Left: serif job title (24px) + small org-unit chip + status pill
- Sub-row: small inline metadata strip — `<N> candidates · <M> in motion · last move <relative-time>`

No primary action button on the page header. The existing per-card `Send invite`
button on `CandidateKanbanCard` stays — that flow is candidate-bound
(`SendInviteDialog` requires `candidateId` + `assignmentId` + `candidateName`)
so a job-level header button doesn't have a coherent action to map to.
`AddCandidateDialog` would create an unassigned candidate which is also the
wrong shape for "add someone to this board" — that needs an assign-after-create
flow that doesn't exist today and is out of scope here.

One-line tip below the header on first paint: `Drag a card across columns to advance a candidate. Click a card to open their profile.`
Dismissible per-tenant via localStorage key `tracker-board-tip-dismissed`.

The breadcrumb is rendered by `AppShell` automatically (`Tracker › Detail` —
the UUID slug returns `"Detail"` from `humanizeSlug`). Out of scope to fix.

### Body

`<CandidateKanbanView jobId={jobId} />` reused **verbatim**. Already provides:

- Loading + error toast (`useEffect` on error ref)
- Empty pipeline state (`This role has no pipeline stages…`)
- DnD with `PointerSensor (distance: 6)` + `KeyboardSensor (sortableKeyboardCoordinates)`
- `useTransitionCandidate` mutation with optimistic feedback

## Sidebar nav

Edit `components/dashboard/AppShell.tsx`:

- Add to `PRIMARY_NAV` between **Candidates** and **Pipeline** —
  `{ href: "/tracker", label: "Tracker", icon: NI.kanban, kbd: "T" }`.
  Final order: Home · Roles · Candidates · **Tracker** · Pipeline · Question bank · Reports.
- The `NI.kanban` glyph is currently used by `/pipeline`. Move `/pipeline` to a
  new `NI.layers` glyph
  (`"M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"`)
  so the two surfaces stay visually distinct. `NI.kanban` stays defined and
  becomes the Tracker icon — kanban-board is the more literal fit.
- Add `"tracker": "Tracker"` to `PATH_LABELS` so the breadcrumb renders the
  surface name correctly.
- No role gate — every dashboard role can use it (matches `/candidates`).

## Old-URL redirect (`proxy.ts`)

Add a rule near the top of the existing matcher (before any auth gating —
redirect outranks the dashboard guard for unambiguously legacy URLs).

```ts
// Legacy: /candidates?jd=<uuid>&view=kanban → /tracker/<uuid>
const url = new URL(req.url)
if (url.pathname === '/candidates' && url.searchParams.get('view') === 'kanban') {
  const jd = url.searchParams.get('jd') ?? ''
  const isUuid = /^[0-9a-f-]{36}$/i.test(jd)
  const target = isUuid ? `/tracker/${jd}` : '/tracker'
  return NextResponse.redirect(new URL(target, req.url), 308)
}
```

- 308 (permanent + preserves method) over 301 — modern browsers honor it
  correctly and search engines treat it the same.
- The UUID regex guards against open-redirect via crafted query strings —
  mirrors the redirect-allowlist pattern in `app/(auth)/invite/page.tsx`.
- Strip `view`, `jd`, and any other params from the redirected URL.
- If `view=kanban` but no/invalid `jd`, redirect to `/tracker` (the landing) —
  matches what the toggle did anyway.

## `/candidates` page cleanup

Edit `app/(dashboard)/candidates/ClientCandidatesPage.tsx`:

- Remove the View toggle (List / Kanban buttons).
- Remove `kanbanDisabled`, the `view` URL-param handling, and the `view === 'kanban'`
  branch.
- Keep `JdPicker` (still useful as a list filter — just no longer gates
  kanban).
- Keep `AddCandidateDialog`, the title, the subtitle text.
- Update subtitle copy:
  - From: `Track applicants through the pipeline. Signal-match kanban per role.`
  - To: `Search and triage candidates across roles. Open Tracker to see the board view per role.`

Edit `app/(dashboard)/candidates/[candidateId]/CandidateAssignmentsTab.tsx:273`:

- `href={\`/candidates?jd=${assignment.job_posting_id}&view=kanban\`}` →
  `href={\`/tracker/${assignment.job_posting_id}\`}`
- Update the link label to `Open tracker board` (or whatever copy exists today —
  match the new vocabulary).

## Tests

Per the root `CLAUDE.md` test-coverage gate, the following PRs cannot land
without test deltas:

- **`proxy.ts`** (3 cases):
  1. `/candidates?jd=<uuid>&view=kanban` → 308 to `/tracker/<uuid>`
  2. `/candidates?view=kanban` (no jd) → 308 to `/tracker`
  3. `/candidates?jd=not-a-uuid&view=kanban` → no redirect (passthrough — guards against crafted input)
- **`TrackerJobCard`** composition test: render with mocked `useKanbanBoard`
  returning a known stage distribution; assert the bar segments and per-stage
  counts render. Negative-control: pass `stages=[]` and assert the "No candidates yet" branch.
- **`useTrackerJobs`** smoke test: feed `jobsApi.list` mock with a mix of
  statuses; assert only `pipeline_built` + `active` come through, sorted by
  `updated_at desc`.

## Build sequence

1. Move kanban primitives to `components/dashboard/tracker/` (pure rename, no behavioral change). Update imports inside the moved files. Update the single import in the soon-to-be-deleted `view === 'kanban'` branch in `ClientCandidatesPage.tsx`.
2. Add `useTrackerJobs` hook + `TrackerJobCard` component + landing page (`/tracker`).
3. Add `TrackerKanbanPage` + `/tracker/[jobId]` route.
4. Update `AppShell.tsx` — `PRIMARY_NAV` entry, `NI.layers` glyph for `/pipeline`, `PATH_LABELS["tracker"]`.
5. Add the redirect to `proxy.ts` + tests.
6. Strip kanban from `ClientCandidatesPage.tsx`. Update `CandidateAssignmentsTab.tsx` link.
7. Run `npm run lint && npm run type-check && npm run test && npm run build`.
8. Manual smoke pass:
   - `/tracker` shows live jobs only; cards show stage bar + counts.
   - Click a card → `/tracker/[jobId]` renders the kanban; DnD works; "Send invite" opens the dialog.
   - Old URL `/candidates?jd=<uuid>&view=kanban` 308-redirects to `/tracker/<uuid>`.
   - Candidate profile → Assignments → "Open tracker board" link lands on `/tracker/[jobId]`.
   - `/candidates` no longer shows the kanban toggle.
