# Recruiter Report UI — Design Spec (Sub-project A2)

- **Date:** 2026-05-26
- **Status:** Approved (design); ready for implementation plan
- **Author:** Ishant + Claude
- **Surface:** `frontend/app` (recruiter dashboard) — Next.js 16 App Router, in-house `components/px/` primitives, BinQle / Iris theme.
- **Scope:** The recruiter-facing UI for the per-session candidate evaluation report. Consumes the already-shipped backend report scoring engine (`app/modules/reporting/`, `/api/reports/*`). This is **A2** — the fast follow-up to the A backend spec (`2026-05-25-report-scoring-engine-design.md`).
- **Out of scope:** Backend changes (engine is done), session recording / video playback (sub-project B), highlight reels (C), aggregate hiring analytics (the existing `/reports` placeholder's eventual job), PDF export.

---

## 1. Background & framing

The backend scores a completed v2 session offline and persists one `session_reports` row, served by `/api/reports/*`. A2 renders that report as a **defensible, auditable evaluation a recruiter can trust in seconds and defend under scrutiny**.

The design is anchored to the research-locked defensibility principles (from the A spec §1 and the project memory):

1. **Banded verdict is the top-line** — `advance` / `borderline` / `reject` with color. The numeric score is always *secondary*, never the headline.
2. **Every score shows its work** — evidence quotes (with timestamps) visible on the same screen as the score they justify.
3. **"Insufficient evidence" / "not assessed" is an explicit, visible state** — never a silent zero.
4. **Confidence/coverage is shown separately from the score.**
5. **AI recommends; a human decides** — the human decision is a separate, required, logged action. Borderline is UI-locked against one-click resolution (product invariant).
6. **Verbal-content-only** — surfaced as a trust signal; no facial/affect/appearance scoring exists or is implied (the HireVue cautionary tale).
7. **Progressive disclosure**: verdict → dimensions → per-signal evidence → Q&A/evidence walkthrough.

### Visual direction (approved via brainstorming mockups)

A rich, multi-panel dashboard modeled on a reference the user provided (media panel + circular gauges + score visualization + side conversation panel + soft cards + violet accent), **molded to our schema** with two load-bearing substitutions:
- The reference's video panel → a **session-playback stub** (forward-compat slot for sub-project B).
- The reference's affect radar ("Professionalism/Attitude/Sociability") → **dropped**; replaced by a verbal-content-only trust signal + a per-*signal* spider chart of our calculated scores.

Approved mockup reference: `.superpowers/brainstorm/<session>/content/molded-v2.html`.

---

## 2. Information architecture & routing

- **Route:** `app/(dashboard)/reports/session/[sessionId]/page.tsx`. Keyed by **session** (not report id) so the page can render `pending` / `generating` / `failed` / `404` states before/without a report row, and so entry points only need a session id.
- **Entry points** (the report is reached *contextually*, never from the top nav):
  - `CandidateSessionsTab` — each completed session row gains a **"View report"** link to `/reports/session/{sessionId}`.
  - Kanban card (`tracker/`) — a completed-session card gains an affordance to open its report. (Kanban card carries `latest_session_state`; the link resolves the session id. If the card lacks a session id today, this entry point may be a thin follow-up — the Sessions-tab entry is the primary one and is in scope.)
- **The top-level `/reports` nav placeholder stays as-is** (reserved for future aggregate analytics). A2 does **not** repurpose it. No new sidebar nav item.
- **Error boundary + `loading.tsx`** for the new route segment (root CLAUDE.md production rule).

---

## 3. Page layout & component tree

Full-width content area (this dense dashboard justifies departing from the centered `max-w-[1100px]` page convention; a comfortable `max-w-[1400px]` cap). Two-column grid (`~1.85fr` main / `1fr` side) that collapses to a single column below the `3xl`/`xl` breakpoint.

```
ReportPage (route, client component — polls)
└─ ReportView (status==='ready')                     components/dashboard/reports/
   ├─ ReportTopBar         ← back-to-candidate · title · VerdictChip · regenerate menu
   ├─ grid
   │  ├─ main column
   │  │  ├─ SessionPlaybackStub        ← media-slot stub + VerbalContentOnlyBadge
   │  │  ├─ SignalSpiderChart          ← in-house SVG radar (assessed signals, 0–10)
   │  │  ├─ SignalScorecards           ← knockouts (evidence inline) + weighted signals
   │  │  │   └─ EvidenceQuote (×N)     ← quote + timestamp chip + question_id  [fwd-compat seek]
   │  │  └─ ReportSummary              ← headline · strengths · gaps · rationale
   │  └─ side column
   │     ├─ AiRecommendationCard       ← VerdictBand + ScoreGauge(Overall, big) + 3 dim gauges + coverage/confidence
   │     ├─ HumanDecisionPanel         ← AI-recommends → required logged decision (borderline-locked)
   │     └─ QaEvidencePanel            ← Q&A/evidence walkthrough (from question_scorecards)
   └─ ReportMethodologyFooter          ← scoring_manifest summary + "verbal-content-only" + manifest link
```

### Shared primitives (in-house SVG, no chart lib)

- **`ScoreGauge`** — radial SVG gauge. Props: `score: number | null` (0–100 domain), `size`, `label`. Renders normalized **0–10** (`score/10`, one decimal) as the inner number, ring fill = `score/100`, ring color by tier. `null` → dashed "n/a" ring (not assessed). Animated ring sweep (stroke-dashoffset) + count-up; both **disabled under `prefers-reduced-motion`** (renders final state immediately).
- **`SignalSpiderChart`** — SVG radar over **assessed** signals only (`state !== not_assessed && score != null`), radius = `score/10`. Renders when `assessedSignals.length >= 3`; otherwise the component returns `null` and the page relies on `SignalScorecards` alone. Not-assessed signals are **not plotted**; they are listed (by `SignalScorecards`) so the gap stays visible. Data polygon animates a scale-in (reduced-motion: static).
- **`VerdictBand`** / **`VerdictChip`** — the banded verdict word in tier color; chip is the compact form for the top bar.
- **`EvidenceQuote`** — `{quote, timestamp_ms, question_id, grounded}`. Renders the quote + a **timestamp chip** (`mm:ss` from `timestamp_ms`) + `question_id` + a grounded ✓/⚠ marker. The chip is built as the future **click-to-seek** control: today it is inert (tooltip: "playback coming soon"); when sub-project B lands it becomes a seek button into `SessionPlaybackStub`'s player. This is the single forward-compat contract — keep the prop shape stable.

### Color / tier mapping (Iris semantic tokens; `--px-<role>` = ink, `-fill` = pastel)

| Concept | Token role |
|---|---|
| `advance` verdict · `excellent`/`meets_bar` · knockout `passed` | `ok` (mint-teal) |
| `borderline` verdict | `human` (lavender — the "human review" color) |
| `reject` verdict · `below_bar` · knockout `failed` | `danger` (coral-red) |
| knockout `insufficient` · `partial` opportunity | `caution` (amber) |
| `not_assessed` · `none` opportunity | neutral grey (`--px-fg-4` / `--px-surface-3`) |
| accent / spider stroke / decision panel / seek chips | `accent` (violet) |
| verbal-content-only trust badge | `ai` (cyan) |

Pastel fills never carry text (text is the role ink) — per the brand system. The verdict word uses the role **ink** color at large size; the compact `VerdictChip` may use the saturated fill as a solid background with high-contrast text.

---

## 4. Data flow & API wiring

### `lib/api/reports.ts` (new namespace, co-located types)

Mirror the `candidates.ts` pattern. Response types are hand-typed to match `ReportRead` (backend `app/modules/reporting/schemas.py`): `Verdict`, `Confidence`, `Opportunity`, `KnockoutStatus`, `SignalState`, `EvidenceOut`, `SignalScorecard`, `DimensionScoreOut`, `KnockoutResultOut`, `QuestionScorecard`, `SummaryOut`, `ScoringManifest`, `HumanDecision`, `ReportRead`.

```
type ReportEnvelope =
  | { state: 'ready';    report: ReportRead }      // 200, status ready|failed (branch on report.status)
  | { state: 'pending';  status: 'pending' | 'generating' }  // 202 body {status}
  | { state: 'noReport' }                          // 404 — caught, NOT thrown
```

Methods:
- `getBySession(token, sessionId, opts?) → ReportEnvelope` — calls `GET /api/reports/session/{sessionId}`. Discriminates 202 vs 200 by body shape (`'verdict' in body`). A `404` is **caught and returned as `{state:'noReport'}`** (not thrown) so the hook can keep polling through the brief window after a regenerate before the actor creates the row. Only `403` throws `ApiError(403)` (access denied).
- `regenerate(token, sessionId) → void` — `POST /api/reports/session/{sessionId}/regenerate` (super-admin; 202).
- `recordDecision(token, reportId, body: HumanDecisionIn) → ReportRead` — `POST /api/reports/{reportId}/decision`.

### `lib/hooks/use-report.ts` (TanStack Query)

- `useReport(sessionId)` — query key `['report', sessionId]`. The queryFn never throws on 404 (returns `{state:'noReport'}`); it throws only on 403 (`retry: false`, normalized to `forbidden`). `refetchInterval`: poll every ~4s while the result is `pending`, **or** while `noReport` *and* a generation was requested within a short grace window (so the poll survives the actor's row-creation lag); otherwise stop. Returns a normalized state the page switches on: `loading | noReport | forbidden | pending | failed | ready`.
- `useRecordDecision(sessionId, reportId)` — mutation; on success `setQueryData(['report', sessionId])` with the returned `ReportRead` and toast. Surfaces 422 field errors to the rationale field via `applyApiErrorToForm`.
- `useRegenerateReport(sessionId)` — mutation; on success **optimistically `setQueryData(['report', sessionId], {state:'pending', status:'generating'})`** (and marks the grace window) so polling starts immediately and rides through the transient 404 until the real `generating` row appears.

All hooks use `getFreshSupabaseToken()` (never inline `getSession()`).

### Conditional UI (from `/api/auth/me`)

- **View** is enforced server-side (`reports.view`); the page renders whatever the API returns and shows an access-denied state on 403.
- **Regenerate** button + **404 "Generate report"** action render only when `me.is_super_admin` (matches the backend super-admin gate).
- **Decision** is available to anyone who can view (same `reports.view` set) — no extra client gate.

---

## 5. The human-decision flow

- **Panel states:**
  1. *No decision yet* → "AI recommends **{verdict}**. You decide." + actions.
  2. *Decided* → recorded decision banner (`decision`, `decided_at`, decided-by) + **"Change decision"** (re-opens the form; re-POST overwrites `human_decision` and writes a fresh audit row — intentional, preserves the trail).
- **Form:** `decision ∈ {advance, reject, hold}` + **required** `rationale` (RHF + Zod; the API mandates rationale). Submit calls `useRecordDecision`.
- **Borderline lock (product invariant):** when `verdict === 'borderline'`, the panel renders the explicit "this candidate requires a human decision — required and logged" treatment and there is **no one-click action**: a decision cannot be submitted without a non-empty rationale, and the affordance is visually a deliberate form, not a quick button. (Applies regardless of verdict since rationale is always required, but borderline gets the prominent, non-dismissible framing.)
- **Decision ≠ stage move (A2 boundary):** recording a decision updates the report + audit only. It does **not** transition the candidate's pipeline stage. Moving the candidate stays the existing manual kanban/assignment action. (A future enhancement could offer "advance + move to next stage" — explicitly deferred.)

---

## 6. States (the page switches on these before rendering)

| State | Trigger | UI |
|---|---|---|
| `loading` | initial fetch | route `loading.tsx` skeleton (gauge/card skeletons via `px/Skeleton`) |
| `noReport` | `404` (caught, not thrown) | Empty state: "No evaluation yet." Explains likely reasons (session not completed, or not a v2 AI-screening stage). Super-admin sees a **"Generate report"** action (calls `regenerate`, which flips the page into `pending` and polls through the row-creation lag). |
| `pending` / `generating` | `202` | Animated "Scoring this interview…" state; polls every ~4s until `ready`. |
| `failed` | `200` + `report.status === 'failed'` | "Report generation failed." No internal error shown (PII discipline). Super-admin sees **Regenerate**. |
| `forbidden` | `403` | Access-denied panel (reuse `AccessDenied` pattern). |
| `ready` | `200` + `status === 'ready'` | Full `ReportView`. |

`ReportView` **must branch on `report.status === 'failed'` before** rendering gauges, because `_row_to_read` coerces a failed row's `overall_coverage → 0.0`, `dimension_scores → {}`, etc.

---

## 7. Forward-compatibility (sub-project B)

- **`SessionPlaybackStub`** occupies the exact media slot the recording will fill. Renders a dark 16:7 media card with a "playback arrives with recording" message, a "Sub-project B" tag, and a disabled scrubber. It is a self-contained component so B can swap its internals for a real player without touching the surrounding layout.
- **`EvidenceQuote`'s timestamp chip** is the seek-control-in-waiting (see §3). Every evidence object already carries `timestamp_ms` + `question_id`; the chip renders them now and becomes an active seek into the player when B lands. **No data reshaping needed later** — this is the whole point of designing the evidence component around `{quote, timestamp_ms, question_id}` today.
- **Full transcript tab** — deferred. Requires a new PII-gated recruiter endpoint exposing `sessions.transcript` (deliberately excluded from `SessionDetailResponse` today). The `QaEvidencePanel` is the A2 stand-in and can gain a "Transcript" tab when that endpoint exists.

---

## 8. Accessibility, performance, security

- **A11y:** keyboard-navigable (decision form, tabs, regenerate menu); ARIA labels on icon-only buttons and on each gauge (`aria-label="Overall score 3.6 out of 10, verdict reject"`); the spider/gauges are decorative-with-text-equivalent (the numbers + scorecards carry the same data for screen readers); dialogs move focus on open (existing pattern). `prefers-reduced-motion` disables ring/count-up/polygon animation.
- **Perf:** in-house SVG only (no chart lib) keeps the route under the 250KB first-load budget; `Suspense` + `loading.tsx`; gauges/spider are pure render from already-fetched data.
- **Security / PII:** no raw PII in `console.*` or any telemetry. Evidence quotes (candidate words) render in the authenticated tenant UI as the evaluation evidence — expected — but must never be logged. Backend-returned strings render as text with `whitespace-pre-wrap`, never `dangerouslySetInnerHTML`. No new redirect surfaces.

---

## 9. Testing (Vitest + Testing Library; composition tests at the API boundary)

- **`lib/api/reports.ts`** — 202-vs-200 discrimination, 404/403 mapping.
- **`use-report` hook** — pending→ready polling transition; failed/404/403 normalization (mock `apiFetch`).
- **`ScoreGauge`** — null → "n/a" dashed ring (not a zero); 0–10 normalization (36 → "3.6"); tier color by score; reduced-motion path renders final state.
- **`SignalSpiderChart`** — plots only assessed signals; returns null for <3 assessed; not-assessed signal never contributes a vertex.
- **`VerdictBand`** — each verdict → correct tier color + band label.
- **`EvidenceQuote`** — renders quote + `mm:ss` from `timestamp_ms` + `question_id`; grounded ⚠ when `grounded === false`.
- **`HumanDecisionPanel`** (composition) — borderline locks one-click; rationale required (submit disabled/invalid without it); "Change decision" re-opens; decided state renders recorded decision. Verify negative control by removing the lock.
- **`ReportView`** branches: `failed` status renders the failed state, not gauges.
- **States**: noReport (super-admin sees Generate; non-admin does not), forbidden, pending, ready.

---

## 10. File inventory

**New:**
- `app/(dashboard)/reports/session/[sessionId]/page.tsx` + `loading.tsx` + `error.tsx`
- `lib/api/reports.ts`
- `lib/hooks/use-report.ts`
- `components/dashboard/reports/` — `ReportView.tsx`, `ReportTopBar.tsx`, `AiRecommendationCard.tsx`, `ScoreGauge.tsx`, `SignalSpiderChart.tsx`, `VerdictBand.tsx`, `SignalScorecards.tsx`, `EvidenceQuote.tsx`, `ReportSummary.tsx`, `HumanDecisionPanel.tsx`, `QaEvidencePanel.tsx`, `SessionPlaybackStub.tsx`, `VerbalContentOnlyBadge.tsx`, `ReportMethodologyFooter.tsx`, plus state components (`ReportEmptyState`, `ReportPendingState`, `ReportFailedState`).

**Modified:**
- `app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab.tsx` — add "View report" link per completed session.
- (Possibly) `components/dashboard/tracker/*` kanban card — open-report affordance (thin; Sessions-tab entry is primary).

---

## 11. Open tunables / explicit deferrals

- **0–10 display:** one decimal (`3.6`). The verdict band remains the headline, so this does not reintroduce "decimal-as-headline." (Revisit if integer-of-10 reads cleaner with real data.)
- **Polling cadence** (~4s) and **max poll duration** — tune with real generation latencies.
- **Spider axis source** — per-signal (chosen). Per-dimension was rejected as too sparse (3-point triangle) and redundant with the gauges.
- **Deferred:** decision-triggers-stage-move; full transcript tab; video playback (B); reels (C); aggregate analytics; PDF export.
