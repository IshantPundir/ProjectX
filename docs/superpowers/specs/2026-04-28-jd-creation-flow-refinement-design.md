# JD Creation Flow Refinement — Split Pipeline, Skip Toggle, Loading UX

**Status:** Draft (design)
**Date:** 2026-04-28
**Module:** `app/modules/jd/` (backend), `app/(dashboard)/jobs/[jobId]/` (frontend)
**Touches:** jd, ai, frontend dashboard shell, prompts

---

## 1. Context

ProjectX's MVP demo is scoped to the admin-only happy path: create a job → add a candidate → candidate takes the AI screening → report renders. Step 1 of that demo is "create and manage Jobs," which today runs through the JD pipeline shipped in Phase 2A and edited in Phase 2B.

Three rough edges in the current Job creation flow are blocking a polished demo:

1. **Loading UI is from the pre-v4 design system.** `LoadingSkeleton.tsx` was last touched in Phase 2B (`ac6a83b`) and never updated when the `px-*` primitives + `var(--px-*)` token system landed in `e33b90e` ("v4 UI refactor"). It uses raw `zinc-*` Tailwind colors and inline `style=` attributes, which makes the post-submit screen look obviously older than the rest of the app.
2. **Every JD is unconditionally enriched.** Some recruiters/admins paste already-polished JDs and don't need or want AI rewriting. There is no opt-out today — `create_job_posting()` always transitions `draft → signals_extracting` and dispatches the extraction actor.
3. **Enrichment + signal extraction happen in a single coupled LLM call.** `actors.py` sends one Instructor request that returns both `enriched_jd` and `signals` in one structured `ExtractionOutput`. The frontend gets one SSE event when both complete. The user has no real-time visibility into which phase is running, and there is no way to skip enrichment without skipping signal extraction too.

A separate but UI-adjacent issue: the JD review page has a "Full JD" button on the left side panel that toggles the center column between signal details and the JD body. This is awkward — the affordance is in the wrong place (left panel button controls center column), and the binary doesn't account for raw-vs-enriched JD distinction.

This spec addresses all four in one coherent change. Provenance ("Where in the JD") is **not** in scope: signal schema v2 already ships `source` + `inference_basis` per signal, the prompt already populates them, and `SignalInspector.tsx` already renders them.

---

## 2. Goals & Non-Goals

### Goals

- Admin can choose, at job-creation time, whether to enrich the pasted JD or use it verbatim. Default ON.
- Enrichment and signal extraction run as two separate, observable LLM calls. The frontend renders real-time progress for each.
- When enrichment runs, signal extraction reads the *enriched* JD (better signals on a polished input).
- When enrichment is skipped, signal extraction reads the raw JD directly. Provenance quotes reference the JD the model actually saw.
- Loading UI on the JD review page is rebuilt against the v4 design system, with phase-targeted loading states (only the column being mutated animates).
- The JD review page's center column gains a 3-way segmented toggle: `Raw JD` / `Enriched JD` / `Signal details`. The "Full JD" button on the left panel is removed.
- Retry semantics: a failed phase-2 with a successful phase-1 does not re-run phase 1 on retry (don't pay for tokens twice).
- The existing re-enrichment flow (Phase 2B's "edit signals → re-rewrite JD") continues to work unchanged.

### Non-Goals

- **HM/Recruiter role redesign.** Out of scope. Admin-only flow for MVP. Covered separately in `2026-04-28-jd-hm-recruiter-handoff-design.md`.
- **Per-stage participant assignment UX.** Out of scope. The MVP demo is single-AI-screening; participant slots stay backend-only.
- **Persisting `skip_enrichment` to the DB.** Request-time only. The resulting `enrichment_status` (`idle` for skipped, `enrichment_complete` for run) tells the audit story without a new column.
- **Pause-for-review checkpoint between enrichment and signal extraction.** The two phases stream sequentially without human intervention. If the user wants to edit the enriched JD and re-extract signals, that's a separate feature.
- **Streaming partial JSON from a single LLM call** (the "option B" alternative). Rejected because the user wants enrichment to be genuinely optional, not just visually staged.
- **Forcing signal-extraction provenance to quote the raw JD when enrichment ran.** Out of scope; quotes reference whichever JD the model saw (option (i)). Revisit if the UX feels weird in practice.
- **Mid-stream cancellation** of phase 1 once it's started. The actor runs both phases to completion or fails atomically.

---

## 3. State Model

### 3.1 Main JD state machine — unchanged

```
draft
└─→ signals_extracting
    ├─→ signals_extracted
    │   └─→ signals_confirmed
    │       └─→ pipeline_built → active
    └─→ signals_extraction_failed
        └─→ signals_extracting (retry)

archived (terminal)
```

No new states. The split is invisible to the main state machine.

### 3.2 Enrichment phase tracking — reuse existing column

`job_postings.enrichment_status` (already exists) tracks phase 1 progress independently:

| Value | Meaning |
|---|---|
| `idle` | Phase 1 was skipped (`skip_enrichment=true`) or never reached. |
| `enriching` | Phase 1 LLM call in flight. |
| `enrichment_complete` | Phase 1 succeeded; `description_enriched` is populated. |
| `enrichment_failed` | Phase 1 LLM call failed. Main state moves to `signals_extraction_failed`. |

### 3.3 Combined sequencing

| `skip_enrichment` | Phase 1 (Enrichment) | Phase 2 (Signal extraction) | Final `enrichment_status` |
|---|---|---|---|
| `false` (default) | Runs: raw JD → enriched JD | Runs against enriched JD | `enrichment_complete` |
| `true` | Skipped | Runs against raw JD | `idle` |

Both paths land at `signals_extracted` on success.

---

## 4. Backend Changes

### 4.1 Request schema

`backend/nexus/app/modules/jd/schemas.py` — add to `JobPostingCreate`:

```python
skip_enrichment: bool = Field(
    default=False,
    description="If true, signal extraction runs against the raw JD; enrichment is skipped entirely.",
)
```

### 4.2 Service layer

`backend/nexus/app/modules/jd/service.py:create_job_posting()`:

- Accept `skip_enrichment` from the request payload.
- No change to state-machine transitions: still `draft → signals_extracting` regardless.
- Pass `skip_enrichment` into the dispatched actor's payload.

### 4.3 Actor refactor

`backend/nexus/app/modules/jd/actors.py` — split `extract_and_enhance_jd` into two sequential phases inside the same actor. **One Dramatiq dispatch, two sequential LLM calls** to `app/ai/`:

```
extract_and_enhance_jd(job_id, skip_enrichment, ...):
  if not skip_enrichment:
    set enrichment_status='enriching'
    publish SSE: jd.enrichment_started
    enriched_jd = LLM call: jd_enrichment.txt against description_raw
    write description_enriched
    set enrichment_status='enrichment_complete'
    publish SSE: jd.enrichment_complete

  source_jd = description_enriched if not skip_enrichment else description_raw
  signals_output = LLM call: jd_signal_extraction.txt against source_jd
  write JobPostingSignalSnapshot v1 (signals + seniority + role_summary)
  transition signals_extracting → signals_extracted
  publish SSE: jd.signals_extracted   # existing event, existing payload shape
```

Notes:

- The actor remains idempotent on retry. On a `signals_extraction_failed` retry:
  - If `enrichment_status='enrichment_complete'`, skip phase 1; re-run phase 2 only.
  - If `enrichment_status='enrichment_failed'` or `idle` (with `skip_enrichment=false`), re-run phase 1.
  - If `skip_enrichment=true`, always skip phase 1.
- Both LLM calls go through `app/ai/` (no direct Instructor/OpenAI imports in `actors.py`) — same provider-agnostic boundary as today.
- Failures in phase 1 set `enrichment_status='enrichment_failed'` AND transition the main state to `signals_extraction_failed`. Phase 2 does not run if phase 1 fails. (Rationale: if the user asked for enrichment, fall-through to raw-JD signal extraction would silently degrade their request — surface the failure instead.)

### 4.4 Prompts

`backend/nexus/prompts/v1/jd_enhancement.txt` is split into two files:

- **`backend/nexus/prompts/v1/jd_enrichment.txt`** — input: raw JD + 4-layer context (company profile, project scope, etc.). Output: `EnrichmentOutput { enriched_jd: str }`. Carries forward only the enrichment-related instructions from the current prompt.
- **`backend/nexus/prompts/v1/jd_signal_extraction.txt`** — input: a JD (enriched or raw) + 4-layer context. Output: `SignalExtractionOutput { signals: list[SignalItemV2], seniority_level, role_summary }`. Carries forward all signal-extraction logic, including the existing provenance instructions (extracted vs. inferred, `inference_basis` rules) verbatim.

Prompt ordering convention from memory: **context (company profile) before document (JD/resume)** — applied in both prompts.

The existing `jd_reenrichment.txt` (used by Phase 2B's edit-signals → rewrite-JD flow) stays as-is.

### 4.5 AI schemas

`backend/nexus/app/ai/schemas.py`:

- **Add** `EnrichmentOutput { enriched_jd: str = Field(min_length=50) }`.
- **Add** `SignalExtractionOutput { signals: list[SignalItemV2], seniority_level: SeniorityLevel, role_summary: str }`.
- **Retire** `ExtractionOutput` (the combined one). Confirm no other call sites; remove import in `actors.py`.

### 4.6 SSE events

`backend/nexus/app/modules/jd/events.py` (or wherever pubsub events live for JD):

- **New event:** `jd.enrichment_complete` — payload `{ job_id, enrichment_status: 'enrichment_complete' }`. Emitted at the boundary between phase 1 and phase 2 when enrichment ran.
- **Optional new event:** `jd.enrichment_started` — emitted when phase 1 begins, mostly for parity. Frontend can derive this from polling `enrichment_status` but pushing it makes the loading UX honest.
- **Existing event:** `jd.signals_extracted` (or whatever the current name is) — unchanged shape, unchanged emission point at end of phase 2.

The frontend `useJobStatusStream` consumer needs to route the new events; see §5.3.

---

## 5. Frontend Changes

### 5.1 Form toggle

`frontend/app/app/(dashboard)/jobs/new/page.tsx`:

- Extend `createJobSchema` (Zod) with `skip_enrichment: z.boolean().default(false)`. UI default: ON (i.e., enrichment runs by default; toggle is "Enrich JD with AI").
- Render a toggle/switch component below the JD textarea with help text: *"Off if your JD is already polished — Copilot will extract signals from it as-is."*
- Submit `skip_enrichment` to the create endpoint.

### 5.2 JD review page — center-column toggle

`frontend/app/app/(dashboard)/jobs/[jobId]/` (page + layout components):

- **Remove:** "Full JD" button from the left side panel.
- **Add:** segmented control in the center column header with three views:
  - **`Raw JD`** — renders `description_raw`.
  - **`Enriched JD`** — renders `description_enriched`. Hidden entirely when `skip_enrichment=true` and `enrichment_status='idle'`. Disabled with tooltip when `enrichment_status='enrichment_failed'`.
  - **`Signal details`** — renders the comprehensive signals view that lives in the center column today (this is the existing component, just made an explicit tab).

Default tab on landing:

| Job state | Default tab |
|---|---|
| `enrichment_status='enriching'` | `Raw JD` (instantly readable while phase 1 runs) |
| `enrichment_status='enrichment_complete'`, signals still extracting | `Enriched JD` |
| `skip_enrichment=true`, signals extracting | `Raw JD` |
| `enrichment_failed` | `Raw JD` |
| Done (`signals_extracted` or beyond), enrichment ran | `Enriched JD` |
| Done, enrichment skipped | `Raw JD` |

### 5.3 Phase-targeted loading states

The current `LoadingSkeleton.tsx` (one monolithic skeleton over the whole page) is replaced by per-column loading states baked into the shell. Loading is targeted to the column being mutated:

| Phase | Toggle | Middle column | Side panels |
|---|---|---|---|
| Phase 1 in flight | ON | Loading animation in the active tab (Raw JD viewable, Enriched JD shows skeleton) | Static placeholder — no shimmer/skeleton, just an inert "Waiting for signals…" affordance |
| Phase 2 in flight | ON | Show JD (enriched or raw, per default tab logic) | **Loading animation in both side panels** |
| Phase 2 in flight | OFF | Show raw JD | **Loading animation in both side panels** |
| Done | either | JD visible in selected tab | Signal cards rendered |

Implementation:

- Rebuild the per-column loading affordances using `px-*` primitives (Skeleton, Shimmer, etc. from `frontend/app/components/px/`).
- Use `var(--px-*)` design tokens, not raw Tailwind color classes.
- Match Phase 3B copy tone (e.g., "Copilot is reading…" / "Copilot is enriching…" / "Copilot is extracting signals…" — avoid technical phrasing like "Extracting signals and enriching JD").
- The old `LoadingSkeleton.tsx` file is deleted once no consumer references it. Replacement: small per-region loading components co-located with the panels they affect (e.g., `JDPaneSkeleton`, `SignalPanelSkeleton`).

### 5.4 SSE event consumer

`frontend/app/.../useJobStatusStream.ts` (or equivalent hook):

- Subscribe to the new `jd.enrichment_started` and `jd.enrichment_complete` events. Update local state (`enrichment_status`) so the loading UI swaps phases without a full refetch.
- `jd.signals_extracted` continues to drive the existing transition. No payload changes.
- When `skip_enrichment=true`, the `enrichment_*` events never fire; the hook stays on phase 2 from page load.

---

## 6. Provenance — No Schema Work

Phase 2A's signal schema v2 already ships:

- `SignalItemV2.source: 'ai_extracted' | 'ai_inferred'`
- `SignalItemV2.inference_basis: str | None` (Pydantic-validated: required when source is `ai_inferred`, null otherwise)
- DB persistence via the `signals` JSONB column on `job_posting_signal_snapshots`
- `SignalInspector.tsx` already renders "Where in the JD" (verbatim quote search via `findSnippet()` for extracted signals; inference text fallback for inferred ones) and a `SourceBadge`

The only behavioral change here: when `skip_enrichment=false`, the signals' `value` strings will appear in the **enriched** JD. `findSnippet()` searches the JD currently displayed; no code change needed because the search runs against whatever JD the active tab is showing. The user sees consistent behavior — quotes match the JD they're looking at.

If "Where in the JD" appears empty/broken in the running build, that is a separate bug to investigate, not a design issue addressed here.

---

## 7. Error Handling & Retries

### 7.1 Failure modes

| Failure point | `enrichment_status` | Main state | User-visible behavior |
|---|---|---|---|
| Phase 1 LLM call fails | `enrichment_failed` | `signals_extraction_failed` | Loading UI shows "Enrichment failed — retry" in middle column. Side panels stay quiet. |
| Phase 2 LLM call fails (after successful phase 1) | `enrichment_complete` | `signals_extraction_failed` | Middle column shows enriched JD. Side panels show "Signal extraction failed — retry". |
| Phase 2 LLM call fails (skip_enrichment=true) | `idle` | `signals_extraction_failed` | Middle column shows raw JD. Side panels show "Signal extraction failed — retry". |

### 7.2 Retry semantics

The existing retry path (`signals_extraction_failed → signals_extracting`) is preserved. On retry, the actor decides per-phase whether to re-run:

```
if skip_enrichment:
  skip phase 1
elif enrichment_status == 'enrichment_complete':
  skip phase 1   # don't pay for it again
else:
  run phase 1

always run phase 2
```

This means a successful enrichment is preserved across phase-2 retries; the user only pays the phase-1 LLM cost once.

### 7.3 No partial-success degradation

If the user requested enrichment and phase 1 fails, we do **not** silently fall back to running phase 2 against the raw JD. Surfacing the failure is preferred — the user can either retry (recover phase 1) or untoggle enrichment and retry (explicit choice to use raw).

---

## 8. Testing Strategy

### 8.1 Backend

**Unit (with mocked AI client):**
- `test_actor_two_phase_happy_path` — `skip_enrichment=false`: assert two LLM calls dispatched, in order; `enrichment_status` transitions `idle → enriching → enrichment_complete`; signal snapshot v1 written; main state lands at `signals_extracted`.
- `test_actor_skip_enrichment` — `skip_enrichment=true`: assert exactly one LLM call (phase 2 only); `enrichment_status` stays `idle`; signal snapshot v1 written; phase-2 input is `description_raw`.
- `test_actor_phase1_failure` — phase-1 LLM raises: `enrichment_status='enrichment_failed'`, main state `signals_extraction_failed`, no phase-2 call dispatched.
- `test_actor_retry_skips_completed_phase1` — initial state `enrichment_status='enrichment_complete'`, main state `signals_extraction_failed`: retry runs phase 2 only.
- `test_actor_retry_reruns_failed_phase1` — initial state `enrichment_status='enrichment_failed'`: retry re-runs both phases.

**Integration (against test DB + real AI mocks):**
- SSE stream emits `jd.enrichment_complete` then `jd.signals_extracted` when toggle is on.
- SSE stream emits only `jd.signals_extracted` when toggle is off.
- Schema validation: `skip_enrichment` defaults to `false` when omitted from request body.

**Composition tests** (per project memory `feedback_composition_tests.md`):
- Render the JD review page with mock SSE source emitting events in sequence; assert center column tab swap and side-panel loading transitions.

### 8.2 Frontend

**Unit:**
- Form: toggle defaults to ON, submits `skip_enrichment: false` when on, `true` when off.
- Center toggle: correct default tab per state matrix in §5.2.
- Center toggle: `Enriched JD` tab hidden when `skip_enrichment=true` and `enrichment_status='idle'`; disabled with tooltip when `enrichment_failed`.

**Integration:**
- Mock SSE: emit `jd.enrichment_started` → middle column shows enrichment skeleton in `Enriched JD` tab. Emit `jd.enrichment_complete` → enriched JD renders, side panels start loading. Emit `jd.signals_extracted` → side panels render signal cards.
- Mock SSE with `skip_enrichment=true`: only `jd.signals_extracted` fires; no enrichment loading appears.

### 8.3 Manual E2E (admin happy path)

1. Admin pastes JD with toggle ON → submit → page lands on JD review with middle column loading (Raw JD tab default-active and readable, Enriched JD tab showing skeleton).
2. Phase-1 SSE event arrives → middle column swaps to `Enriched JD` tab with content; side panels start loading.
3. Phase-2 SSE event arrives → side panels populate with signal cards; user can switch tabs freely.
4. Repeat with toggle OFF → middle column shows raw JD immediately, no enrichment phase visible, side panels load directly into signal extraction.

---

## 9. Implementation Order

Suggested staging — each step is independently mergeable / testable:

1. **Backend two-phase actor split** — refactor `extract_and_enhance_jd` into two sequential phases. New prompts (`jd_enrichment.txt`, `jd_signal_extraction.txt`). New AI output schemas (`EnrichmentOutput`, `SignalExtractionOutput`); retire `ExtractionOutput`. New SSE events (`jd.enrichment_started`, `jd.enrichment_complete`). At this step, **both phases always run** — `skip_enrichment` is not yet a parameter. External behavior is identical to today; new unit tests cover the split paths.
2. **Backend skip toggle** — add `skip_enrichment: bool = False` to `JobPostingCreate` (request schema), plumb through `service.create_job_posting()` to the actor, and add the conditional branch in the actor. Backend now supports both modes; FE still defaults to enrichment-on.
3. **Frontend center-column toggle (Raw / Enriched / Signal details)** — remove "Full JD" button. Fully usable against existing single-event SSE; the new tabs work because the data is already there.
4. **Frontend phase-targeted loading + new SSE event consumption** — rebuild the per-column loading components against `px-*` primitives, hook up new events. Delete `LoadingSkeleton.tsx`.
5. **Frontend skip-enrichment toggle on the create form** — final piece; closes the loop end-to-end.

Steps 3 and 4 can swap order if convenient. Step 5 should land last so the feature is only user-reachable after the rest of the flow is polished.

---

## 10. Touch List

### Backend

| File | Change |
|---|---|
| `backend/nexus/app/modules/jd/schemas.py` | Add `skip_enrichment: bool = False` to `JobPostingCreate`. |
| `backend/nexus/app/modules/jd/service.py` | Accept and forward `skip_enrichment` to the actor dispatch. |
| `backend/nexus/app/modules/jd/actors.py` | Split `extract_and_enhance_jd` into two phases. Update imports (drop `ExtractionOutput`, add the two new schemas). |
| `backend/nexus/app/ai/schemas.py` | Add `EnrichmentOutput`, `SignalExtractionOutput`. Remove `ExtractionOutput`. |
| `backend/nexus/prompts/v1/jd_enhancement.txt` | Delete (replaced by two new prompt files below). |
| `backend/nexus/prompts/v1/jd_enrichment.txt` | New — enrichment-only prompt. |
| `backend/nexus/prompts/v1/jd_signal_extraction.txt` | New — signal extraction (with provenance) prompt. |
| `backend/nexus/app/modules/jd/events.py` (or equivalent) | Add `jd.enrichment_started` and `jd.enrichment_complete` events. |
| `backend/nexus/tests/modules/jd/test_actors.py` (or equivalent) | New tests per §8.1. |

### Frontend

| File | Change |
|---|---|
| `frontend/app/app/(dashboard)/jobs/new/page.tsx` | Add `skip_enrichment` to `createJobSchema` + toggle UI. |
| `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx` | Replace `LoadingSkeleton` usage with phase-targeted per-column loading. Wire new SSE events. |
| `frontend/app/app/(dashboard)/jobs/[jobId]/layout.tsx` (or relevant shell) | Add center-column segmented toggle (Raw / Enriched / Signal details). Default tab logic per §5.2. |
| `frontend/app/components/dashboard/jd-panels/LoadingSkeleton.tsx` | Delete after consumers are migrated. |
| Left-panel component (the one with "Full JD" button) | Remove the "Full JD" button. |
| `frontend/app/app/(dashboard)/jobs/[jobId]/components/JDPaneSkeleton.tsx` (new) | Per-column loading using `px-*` primitives. |
| `frontend/app/app/(dashboard)/jobs/[jobId]/components/SignalPanelSkeleton.tsx` (new) | Per-column loading for side panels. |
| `frontend/app/.../useJobStatusStream.ts` | Subscribe to `jd.enrichment_started` and `jd.enrichment_complete`. |
| Vitest specs co-located with each modified component | New tests per §8.2. |

---

## 11. Open Questions (none blocking)

- **Help-text wording on the toggle.** Final copy can land in the implementation plan / PR.
- **Whether to expose `enrichment_status` in the GET /jobs/{id} response.** Likely yes — the FE needs it on initial page load before any SSE event fires (e.g., on hard refresh during phase 2). Confirm in implementation review.
- **Removal of the orphaned `ExtractionOutput` schema.** Confirm during implementation that no other module imports it before deleting.
