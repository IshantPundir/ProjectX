# JD Stakeholder Handoff — Hiring Manager → Recruiter Workflow

**Status:** Draft (design)
**Date:** 2026-04-28
**Module:** `app/modules/jd/` (backend), `app/(dashboard)/jobs/` (frontend)
**Touches:** auth, org_units, jd, pipelines, question_bank, audit, notifications

---

## 1. Context

ProjectX models hiring as a workflow between two distinct actors who today the system collapses into one:

- **Hiring Manager (HM)** — the *stakeholder*. They own the headcount, define the role need, write the original brief, evaluate finalists, and make the hire/no-hire decision.
- **Recruiter** — the *operator*. They take the brief, polish the JD, configure pipelines, generate question banks, source and screen candidates, and run the day-to-day process.

The current seeds give Recruiters `jobs.create + jobs.manage` and give HMs nothing on jobs. That treats the recruiter as the originator of every requisition. In real Fortune 500 hiring orgs, HMs raise reqs (they're the ones who need the headcount), and recruiters operate them. Without this distinction, ProjectX cannot model HM-driven req creation, HM approval gates, or the audit trail of "who did what" that compliance teams require.

This spec introduces a **stakeholder-handoff workflow** layered on top of the existing JD state machine, with two new actor-aware states (`pending_recruiter`, `pending_hm_approval`) and one revision state (`pending_recruiter_revision`). It also fixes two dormant permission gaps (`jobs.view`, `candidates.manage`) discovered during the recent RBAC audit.

Reference: prior conversation in commit `6bdb84f` (admin-only RBAC enforcement) established the role hierarchy and frontend gating model this spec extends.

---

## 2. Goals & Non-Goals

### Goals

- HMs can raise a job requisition for any org unit on which they hold `jobs.create`, providing only a raw JD and optional project scope. The brief enters a `pending_recruiter` state with no AI extraction.
- Any user with `jobs.manage` in the unit's ancestry can claim an unclaimed req. Claim is **atomic and exclusive** — first writer wins.
- Once claimed, the recruiter operates the JD through the full existing flow (signal extraction, pipeline construction, question bank generation), with the HM locked out of recruiter-craft artifacts during operation.
- The recruiter has two terminal options: **self-publish** (recruiter discretion) or **send for HM approval**. Both are auditable.
- During HM approval, the HM has full edit rights over every artifact (signals, pipeline, question bank, JD copy, brief). The recruiter is read-only during review. HM approval transitions the JD to `active` with the HM's edits baked in.
- HMs can also send the JD back ("Return to recruiter") with notes for major rework, putting it into `pending_recruiter_revision` until the recruiter re-sends.
- Edit rights at every state are enforced server-side; the frontend only mirrors what the backend allows.
- Every state transition and every edit is captured in `audit_log` with actor, before/after, and correlation ID.
- AI extraction cost is only spent on JDs a recruiter has accepted — no LLM tokens spent on speculative HM briefs.

### Non-Goals (explicit, deferred to later phases)

- **Explicit per-unit recruiter assignment** (e.g., "Sarah is the recruiter for Bangalore"). Out of scope. Permission-based ancestry pickup + atomic claim is sufficient for MVP. Add later if a customer asks.
- **Tenant-level "require HM approval before publish" toggle.** Out of scope. Recruiter discretion in MVP.
- **Round-robin or capacity-based auto-assignment** of unclaimed reqs to recruiters. Out of scope.
- **Mid-stream re-extraction during `pending_recruiter_revision`.** Recruiter can edit signals manually during revision but cannot re-fire the Dramatiq actor. If the brief changed materially, the right move is to archive and start fresh.
- **HM editing the JD post-publish.** Active JDs are recruiter-only-editable as today; HM-initiated changes after publish are out of scope (they trigger a new revision cycle, but the design of that cycle is deferred).
- **Notifications dispatch.** This spec defines notification *events* but the dispatch design (email content, in-app, SMS) is delegated to the existing `notifications/` module's templating; specific HTML templates land in the implementation plan.
- **Configurable `created_by_role` override at create time.** Auto-detected from caller's permission level. No manual override.

---

## 3. Roles & Permissions

### 3.1 Updated permission seeds

This spec ships an Alembic migration that **re-seeds the system roles** (no schema change to `roles` itself; just `UPDATE roles SET permissions = ... WHERE name = ... AND is_system = TRUE`). Changes:

| Role | Add | Remove | Notes |
|---|---|---|---|
| **Admin** | `jobs.view`, `candidates.manage` | — | Closes the dormant `jobs.view` and `candidates.manage` gates that currently fall through to super-admin only. |
| **Recruiter** | `jobs.view`, `candidates.manage` | — | Operators need both. `candidates.manage` is what makes the recruiter actually able to *manage* candidates. |
| **Hiring Manager** | `jobs.create`, `jobs.view` | — | The core change. HMs can now raise reqs and see their reqs through the lifecycle. |
| **Interviewer** | `jobs.view` | — | They need to see the JD they're interviewing for. |
| **Observer** | `jobs.view` | — | Read-only consistency. |

The migration is idempotent: it sets the permissions JSONB to the canonical list rather than appending. Running it twice yields the same state. Existing tenant-custom roles (currently none) are untouched — only `is_system = TRUE` rows are updated.

### 3.2 Authority-derived role identity (used by code, not stored)

Code paths that decide "is this caller acting as HM or recruiter on this JD?" derive identity from permissions, not from a role-name lookup. This avoids the trap that `_require_unit_admin` falls into (literal name match). The decision rule, applied to a JD's org-unit ancestry:

```
caller is_super_admin                          → 'admin' authority
caller has jobs.manage in ancestry             → 'recruiter' authority
caller has jobs.create in ancestry, no manage  → 'hm' authority
otherwise                                      → no authority (403)
```

`'admin' authority` includes super admin and anyone holding the `Admin` role on the unit's ancestry — both have `jobs.manage`. Admins are functionally treated as recruiters for state transitions, with the additional ability to override locks (edit a JD even when in `pending_hm_approval`, force-publish, etc.).

This is encapsulated in a new helper:

```python
# app/modules/jd/authz.py
def derive_jd_authority(user: UserContext, ancestry: list[OrgUnit]) -> JdAuthority:
    """Returns 'admin' | 'recruiter' | 'hm' | None."""
```

`JdAuthority` is a `Literal['admin', 'recruiter', 'hm']`. Every JD route handler calls this once, then routes on the result. **No business logic checks `role_name == 'Hiring Manager'` directly** — that's the lesson from the `_require_unit_admin` design choice.

---

## 4. State Machine

### 4.1 New states

| State | Meaning | Who can edit | AI activity |
|---|---|---|---|
| `pending_recruiter` | HM raised; awaiting recruiter pickup | HM (brief + scope only) | None |
| `pending_hm_approval` | Recruiter sent for HM sign-off | HM (everything) | None |
| `pending_recruiter_revision` | HM returned with notes | Recruiter (everything except re-extract) | None |

### 4.2 Updated `LEGAL_TRANSITIONS`

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "pending_recruiter": {"draft"},                                 # claim
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},
    "signals_extracted": {"signals_confirmed"},
    "signals_confirmed": {"signals_extracted", "pipeline_built"},
    "pipeline_built": {"active", "pending_hm_approval"},            # branch
    "pending_hm_approval": {"active", "pending_recruiter_revision"},
    "pending_recruiter_revision": {"pending_hm_approval"},
    "active": set(),
    "archived": set(),
}
```

Transitions added (5): `pending_recruiter → draft`, `pipeline_built → pending_hm_approval`, `pending_hm_approval → active`, `pending_hm_approval → pending_recruiter_revision`, `pending_recruiter_revision → pending_hm_approval`.

The 409 error message map in `app/main.py` needs entries for the three new states.

### 4.3 Visual

```
pending_recruiter
  │ (anyone with jobs.manage on ancestry clicks "Accept")
  │ assigned_recruiter_id := claimer.id  (atomic)
  ▼
draft
  │
  ▼  (existing AI flow, unchanged)
signals_extracting → signals_extracted → signals_confirmed → pipeline_built
  │
  ├──► "Publish" ───────────────────────────────► active   (recruiter discretion)
  │
  └──► "Send for HM approval"
       ▼
       pending_hm_approval         (HM edits anything; recruiter read-only)
         │
         ├──► HM "Approve" ──────► active   (HM's edits land verbatim)
         │
         └──► HM "Return to recruiter"  (with notes)
              ▼
              pending_recruiter_revision
                │ (recruiter edits, then "Re-send")
                ▼
                pending_hm_approval (loop)
```

Existing JDs: the migration that adds the new states does **not** retroactively change any existing rows. Every JD row in production today is in one of `draft / signals_extracting / signals_extracted / signals_confirmed / pipeline_built / active / archived` and stays there. New JDs created post-deploy by HMs use the new entry state.

---

## 5. Schema Changes

### 5.1 New columns on `job_postings`

```sql
ALTER TABLE job_postings
  ADD COLUMN created_by_role TEXT NOT NULL DEFAULT 'recruiter'
    CHECK (created_by_role IN ('hm', 'recruiter', 'admin')),
  ADD COLUMN assigned_recruiter_id UUID REFERENCES users(id),
  ADD COLUMN claimed_at TIMESTAMPTZ,
  ADD COLUMN approved_by_hm UUID REFERENCES users(id),
  ADD COLUMN approved_at TIMESTAMPTZ,
  ADD COLUMN revision_notes TEXT;
-- revision_notes is single-blob, last-write-wins (set on
-- `return-to-recruiter`, cleared on `resend-for-approval`). Multi-revision
-- history is out of scope; see §14.

-- "Unclaimed reqs in this unit" — primary dashboard query.
CREATE INDEX job_postings_unclaimed_idx
  ON job_postings (org_unit_id)
  WHERE assigned_recruiter_id IS NULL AND status = 'pending_recruiter';

-- "My active reqs" for an assigned recruiter.
CREATE INDEX job_postings_assigned_recruiter_idx
  ON job_postings (assigned_recruiter_id, status)
  WHERE assigned_recruiter_id IS NOT NULL;
```

The `created_by_role` default of `'recruiter'` is **load-bearing**: existing rows back-fill to recruiter (matches today's reality, where every JD was created by a Recruiter or Admin). New rows pass an explicit value.

### 5.2 Atomic claim mechanic

The "first-claim-wins" semantic is implemented via a single conditional UPDATE inside the claim handler. No advisory locks, no SELECT-FOR-UPDATE — Postgres' MVCC handles it natively when the predicate filters on `assigned_recruiter_id IS NULL`:

```python
# app/modules/jd/service.py
async def claim_job_for_recruiter(db, job_id, recruiter_id, correlation_id):
    result = await db.execute(
        update(JobPosting)
        .where(
            JobPosting.id == job_id,
            JobPosting.assigned_recruiter_id.is_(None),     # ← gating predicate
            JobPosting.status == 'pending_recruiter',
        )
        .values(
            assigned_recruiter_id=recruiter_id,
            claimed_at=func.now(),
        )
        .returning(JobPosting)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise ConflictError("Already claimed by another recruiter")
    await transition(db, job, to_state='draft', actor_id=recruiter_id, correlation_id=correlation_id)
    # ...dispatch Dramatiq actor as today...
```

If two recruiters race, exactly one row is updated; the loser gets `None` and a 409. No double-claim possible.

### 5.3 RLS

All new columns inherit existing `tenant_isolation` + `service_bypass` policies on `job_postings` (the table already has the canonical policy pair from migration 0009 + 0011). No new policies needed.

The startup RLS-completeness check in `app/main.py` requires no update — `job_postings` is already enumerated.

### 5.4 Indexing rationale

- `job_postings_unclaimed_idx` is partial (only rows with `assigned_recruiter_id IS NULL AND status = 'pending_recruiter'`) so it stays small even at scale. The "Unclaimed reqs" dashboard query for a recruiter is `WHERE org_unit_id IN (their_unit_ids) AND status = 'pending_recruiter' AND assigned_recruiter_id IS NULL`. Index covers it.
- `job_postings_assigned_recruiter_idx` powers the "My active reqs" list for a recruiter and the future "Sarah's queue" UI.

---

## 6. API Surface

### 6.1 Modified: `POST /api/jobs`

**Behavior split based on caller's authority on the target org unit**, derived via `derive_jd_authority(user, ancestry)`:

- **`'admin'` or `'recruiter'`:** existing behavior. JD lands in `draft`, `created_by_role` = caller's authority, `assigned_recruiter_id` = caller, `claimed_at` = now. Dramatiq actor dispatched. Response is 201 + the existing snapshot shape.
- **`'hm'`:** new behavior. JD lands in `pending_recruiter`, `created_by_role = 'hm'`, `assigned_recruiter_id = NULL`, `claimed_at = NULL`. **No Dramatiq dispatch.** Response is 201 + a minimal "raised, awaiting recruiter" payload (no snapshot — there's nothing to snapshot yet).
- **No authority:** 403 (existing behavior).

Request body remains the same shape. The HM-only path requires `description_raw` (their brief) and accepts `project_scope_raw`; all other fields (deadline, headcount, employment_type, etc.) are optional from the HM and may be filled in by the recruiter post-claim.

### 6.2 New: `POST /api/jobs/{id}/claim`

Recruiter or Admin claims a `pending_recruiter` req. Authorization: `derive_jd_authority` must return `'recruiter'` or `'admin'` on the JD's org unit ancestry. Body is empty.

Atomically updates `assigned_recruiter_id`, `claimed_at`, transitions `pending_recruiter → draft`, dispatches the Dramatiq extraction actor (same as today's create flow). Returns the JD with snapshot.

Errors: 404 if the JD doesn't exist or is in a different state, 409 if already claimed (`assigned_recruiter_id IS NOT NULL`), 403 if caller lacks authority.

### 6.3 New: `POST /api/jobs/{id}/send-for-approval`

Recruiter or Admin sends a `pipeline_built` JD to HM review. Authz: caller must be the `assigned_recruiter_id` OR have `'admin'` authority. Transitions `pipeline_built → pending_hm_approval`. Body is empty (notes via separate field on the JD if needed — out of scope for MVP).

### 6.4 New: `POST /api/jobs/{id}/approve`

HM approves a `pending_hm_approval` JD. Authz: `derive_jd_authority` must return `'hm'` (the HM who raised it OR any HM on the ancestry — see §7.3 below) or `'admin'`. Stamps `approved_by_hm`, `approved_at`. Transitions `pending_hm_approval → active`.

### 6.5 New: `POST /api/jobs/{id}/return-to-recruiter`

HM rejects with notes. Authz: same as approve. Body: `{ notes: string }`. Transitions `pending_hm_approval → pending_recruiter_revision`. Notes are stored as a new `revision_notes` column on `job_postings` (single-text-blob; revision history out of scope).

### 6.6 New: `POST /api/jobs/{id}/resend-for-approval`

Recruiter re-sends after revision. Authz: assigned recruiter or admin. Transitions `pending_recruiter_revision → pending_hm_approval`. Clears `revision_notes`.

### 6.7 Modified: `GET /api/jobs` and `GET /api/jobs/{id}`

List and detail endpoints honor the existing ancestry-walk visibility. New filters added to list:

- `?status=pending_recruiter&unclaimed=true` — recruiter dashboard ("queue of reqs to claim").
- `?assigned_to=me` — recruiter's own queue.
- `?created_by=me` — HM's "my reqs".

Response shape gains the new fields (`created_by_role`, `assigned_recruiter_id`, `claimed_at`, `approved_by_hm`, `approved_at`, `revision_notes`).

### 6.8 Modified: existing edit endpoints

Every endpoint that mutates JD state, signals, pipeline, or question bank consults the **edit-rights matrix** (§7) before allowing the operation. The check is centralized in a new helper:

```python
# app/modules/jd/authz.py
async def require_edit_rights(
    db, job, user, *, artifact: Literal['brief', 'signals', 'pipeline', 'question_bank']
):
    """Raises 403 if the caller cannot edit this artifact in the job's current state."""
```

Routes that fan out to multiple artifacts (e.g., re-enrichment edits signals + pipeline) call the helper for each artifact they touch.

---

## 7. Authorization & Edit Boundaries

### 7.1 Edit-rights matrix

| State | HM (any HM on ancestry) | Assigned Recruiter | Admin / super admin |
|---|:---:|:---:|:---:|
| `pending_recruiter` | brief + scope (creator only) | — | full override |
| `draft` | view | full | full override |
| `signals_extracting` → `pipeline_built` | view | full | full override |
| `pending_hm_approval` | full (everything) | view | full override |
| `pending_recruiter_revision` | view | full (no re-extract) | full override |
| `active` | view | view (edits via re-approval cycle — out of scope, §14) | full override |
| `archived` | view | view | view |

Admin override behavior: an Admin or super-admin can edit any artifact at any state. Used for emergency intervention (recruiter unavailable, HM blocking a critical hire, etc.). Always logged.

### 7.2 Locking implementation

State + `assigned_recruiter_id` + `created_by` together encode who can edit what. The check is simple lookup-then-compare; no additional locks. Concurrency is handled by the existing version-bump pattern on signals (`SELECT … FOR UPDATE` on the JD row before signal writes), extended to pipeline and question bank writes via the same pattern.

### 7.3 "Which HM can approve?"

Two interpretations were considered:

- **(a) Only the HM who raised it.** Strict but rigid — if that HM is on PTO, the JD blocks indefinitely.
- **(b) Any user with `jobs.create` (HM authority) on the unit's ancestry.** Pooled approval, matches how HMs cover for each other in real teams.

**MVP picks (b).** It's the lower-friction default. Audit log captures *which* HM approved (`approved_by_hm`), so accountability is preserved. A future tenant setting could enforce (a) if a customer requires it (e.g., regulated industries).

### 7.4 Frontend mirror

The frontend reads `me.assignments` and the JD's state + `assigned_recruiter_id` to decide which buttons to show. Pure read-side computation — no permission check happens client-side, every action goes through the gated API.

---

## 8. AI Cost Discipline

The cost concern is real and load-bearing for Phase 3 economics: at Fortune 500 volume, every speculative HM brief that triggers a full Call-1+Call-2 extraction is ~3–6k tokens of waste if the recruiter rejects the req. The new `pending_recruiter` state isolates AI cost behind a recruiter checkpoint:

- HM-raised JDs sit in `pending_recruiter` with no Dramatiq dispatch.
- Recruiter claim is the trigger for `transition → draft → signals_extracting`. The same `_safe_dispatch_extraction` background task that runs in today's create flow is moved into the claim handler.
- If a recruiter rejects a req before claiming (hypothetical "Decline" action — out of scope for MVP), zero AI cost is incurred.

For recruiter-self-created JDs, behavior is unchanged: extraction fires immediately, same cost profile as today.

This is the only AI-cost change in scope. Mid-stream re-extraction during revision, batched re-runs, or any LLM-cache strategy are out of scope.

---

## 9. Audit & Notifications

### 9.1 Audit events

Every state transition writes to `audit_log` via the existing `state_machine.transition()` path — no change there. Three new action codes are added:

- `job_posting.claimed` — payload: `{ recruiter_id, recruiter_email }`
- `job_posting.sent_for_approval` — payload: `{ sent_by, sent_at }`
- `job_posting.approved` — payload: `{ approved_by_hm, approved_at, edits_diff }` where `edits_diff` is the JSON diff of artifacts changed during HM review (computed at approve time from the snapshot delta)
- `job_posting.returned_to_recruiter` — payload: `{ returned_by, notes }`

Existing `job_posting.status_changed` audit entry continues to fire on every transition; the new actions provide higher-signal context (e.g., the diff on approve).

### 9.2 Notification events (dispatch deferred to implementation plan)

The notifications module defines and dispatches:

| Event | Trigger | Recipients |
|---|---|---|
| `req.raised` | `pending_recruiter` enters | All users with `jobs.manage` on the unit's ancestry (capped at N=20 for ergonomics) |
| `req.claimed` | claim succeeds | The HM who raised the req |
| `req.sent_for_approval` | recruiter sends | The HM who raised the req |
| `req.approved` | HM approves | Assigned recruiter |
| `req.returned_to_recruiter` | HM returns | Assigned recruiter |
| `req.published` | JD enters `active` (any path) | The HM who raised the req (always — never surprised) |

In-app + email both. Templates land in the implementation plan.

---

## 10. Frontend Implications

### 10.1 New surfaces (lightweight; full design in implementation plan)

- **HM "Raise a req" form** — title, target unit picker, raw description, optional project scope, optional headcount/deadline. Lives at `/jobs/new` for HMs (the existing route adapts based on caller role).
- **Recruiter "Unclaimed reqs" dashboard** — list view at `/jobs?unclaimed=true`. Each row has an "Accept" button.
- **HM approval review page** — extends the existing JD review at `/jobs/{id}/review` with HM-edit-mode when the JD is `pending_hm_approval` and caller is HM. Two new buttons: "Approve" and "Return to recruiter".
- **Recruiter revision notes banner** — when `pending_recruiter_revision`, show the HM's `revision_notes` at the top of the review page in a yellow informational banner.

### 10.2 Read-only rendering

When the frontend detects a JD is in a state where the caller cannot edit (e.g., recruiter viewing a `pending_hm_approval` JD), all editable controls render as `disabled`/read-only with a small banner explaining why. No permission *enforcement* on the frontend — the API is the source of truth — but the UX should mirror it.

### 10.3 Visibility filters in nav

- HMs see "My reqs" in primary nav (filtered to `created_by=me`). No org-units / team-access nav (already gated to admins by prior commit).
- Recruiters see "Unclaimed in my unit" + "My active reqs" tabs in `/jobs`.
- Admins see all reqs across the tenant.

### 10.4 No changes to AppShell nav rail beyond what's already shipped

The existing nav (`Roles / Candidates / Pipeline / Question bank / Reports`) covers HMs and Recruiters fine. Sub-tabbing inside `/jobs` is a per-page concern.

---

## 11. RLS Considerations

The existing RLS policy pair on `job_postings` (`tenant_isolation` USING + WITH CHECK, `service_bypass` USING) covers the new columns automatically — they're scoped by `tenant_id`, which is the policy predicate.

No cross-tenant leak surface is introduced. The new `assigned_recruiter_id` and `approved_by_hm` columns FK to `users(id)`, but `users` already has the same tenant-isolated RLS, so any join through them stays within the tenant.

The startup RLS completeness check (`_assert_rls_completeness` in `app/main.py`) requires no update.

---

## 12. Testing Strategy

### 12.1 Unit tests

- `derive_jd_authority` — full truth table across role assignments (super admin, Admin, Recruiter only, HM only, no perms, multi-role on different units).
- `LEGAL_TRANSITIONS` — every legal edge fires; every illegal edge raises `IllegalTransitionError`.
- `claim_job_for_recruiter` — single-claim happy path, double-claim race (loser gets 409), claim of non-`pending_recruiter` job (rejected).
- `require_edit_rights` — full matrix from §7.1, all artifacts × all states × all authority levels.
- `revise_role_seeds` migration — runs idempotently; verifies each role's permission set post-migration.

### 12.2 Integration tests (httpx + real DB)

- HM creates JD → lands in `pending_recruiter`, no Dramatiq dispatch (mock the broker; assert no `send` call).
- Recruiter claims → JD transitions to `draft`, Dramatiq dispatched.
- Two recruiters race claim — one succeeds, one gets 409.
- Recruiter sends for approval → HM edits signals → HM approves → JD active with HM's edits intact.
- HM returns with notes → recruiter edits → re-sends → HM approves.
- Edit attempts that violate the matrix return 403 (HM tries to edit signals while in `draft`; recruiter tries to edit while in `pending_hm_approval`).
- Audit log contains entries for every transition + claim + approve + return event.
- RLS: a user from tenant B cannot see, claim, or edit any JD in tenant A (existing pattern; new fields covered).

### 12.3 Frontend tests (vitest)

- HM-form submission goes through the auto-detected `pending_recruiter` path and shows "Awaiting recruiter pickup" affordance.
- Recruiter dashboard "Accept" claims and routes to the JD detail.
- HM review page disables recruiter-craft editing when not in `pending_hm_approval`.
- Read-only banner appears on the recruiter's view of a JD in `pending_hm_approval`.

### 12.4 Migration tests

- `test_migration_<NNNN>` — runs the seed-update on a fresh DB, asserts the canonical permission lists post-migration, asserts existing tenant-custom roles untouched.
- Backfill audit: existing `job_postings` rows get `created_by_role = 'recruiter'` defaulted on; spot-check a few representative rows.

---

## 13. Migration & Rollout

### 13.1 Alembic migration sequence

1. **Migration A:** Schema additions to `job_postings` (`created_by_role`, `assigned_recruiter_id`, `claimed_at`, `approved_by_hm`, `approved_at`, `revision_notes`). Create indexes (`job_postings_unclaimed_idx`, `job_postings_assigned_recruiter_idx`). Idempotent backfill: `UPDATE job_postings SET created_by_role = 'recruiter' WHERE created_by_role IS NULL` (column has DEFAULT so this is belt-and-braces).
2. **Migration B:** Re-seed system role permissions. Idempotent — re-runnable.

Both migrations are **independent** and can be applied in either order. They're authored as separate files so a rollback of either does not affect the other.

### 13.2 Code rollout

- Backend changes can ship before frontend changes; the new endpoints are additive.
- Frontend rolls out behind no feature flag — the API gates correctly, so a frontend that doesn't yet show HM affordances simply won't expose them. Existing recruiter/admin flow is unchanged.
- `derive_jd_authority` is called only by new routes and the modified `POST /api/jobs`. The latter is the single change to existing behavior; everything else is greenfield.

### 13.3 Rollback

Each migration has a paired downgrade. Schema downgrade drops the new columns; permission-seed downgrade restores the prior canonical permission lists. The seed downgrade is required reading before a customer-facing rollout — it's a small file but easy to forget.

A backwards-compatibility note: if any JDs were created in `pending_recruiter` and the schema is rolled back, those rows have nowhere to live (status enum lacks the value). Operationally, before downgrading we'd need to (a) archive any open `pending_recruiter` rows, or (b) hand-update them to `draft`. Documented in the migration file's docstring.

---

## 14. Out of Scope (explicit)

These are deliberately deferred. Each is a real follow-up but not part of this spec:

- Per-unit explicit recruiter assignment (e.g., "Sarah is the recruiter for Bangalore").
- Tenant-level "require HM approval before publish" toggle.
- Round-robin auto-assignment of unclaimed reqs.
- Mid-revision AI re-extraction (ability to re-run Call 1/2 during `pending_recruiter_revision`).
- HM editing the JD post-publish (triggers a new revision cycle).
- Restrict HM approval to the originating HM only (current MVP allows any HM on ancestry).
- Notification template HTML.
- "Decline req" action that lets a recruiter reject a `pending_recruiter` req before claim.
- Multi-recruiter co-ownership (always single-assignee in MVP).
- Revision history / multiple revision_notes (single overwrite-on-resend in MVP).
- Email/Slack notifications beyond the `notifications/` module's existing dispatch surfaces.
- Mobile responsive design for the new HM raise form.

---

## 15. Open Questions for Implementation Plan

These are not unanswered design questions, but specifics that the implementation plan needs to nail down:

1. **Pipeline + Question Bank mutability during HM review.** The spec says HM can edit "everything" in `pending_hm_approval`. The pipelines and question_bank modules have their own state machines and edit-rights logic — confirm those compose cleanly with the JD's `pending_hm_approval` state, or surface a list of route-level adjustments needed.
2. **`edits_diff` computation for the audit log on approve.** Likely a deep diff of the latest signal snapshot, pipeline instance, and question bank state vs. the snapshot at `pipeline_built`. Implementation plan should pick a diff library or define a hand-rolled walker.
3. **Notification recipient cap (N=20) on `req.raised`.** Picked arbitrarily — confirm with product/UX whether to cap at 20, page differently, or always notify all.
4. **HM "Raise a req" form fields.** Spec lists the minimum (title, unit, brief). The implementation plan should propose the full field set and which are HM-required vs recruiter-fillable-later.
5. **Frontend "Unclaimed reqs" page query strategy.** Live polling, SSE on a new stream, or React Query refetch on focus? Today's JD list uses TanStack Query with `staleTime: 10_000` — that's probably fine, but worth confirming under load.

---

## 16. Acceptance Criteria

This spec is correctly implemented when:

- An HM with `jobs.create` on a unit can `POST /api/jobs` and have the JD land in `pending_recruiter` with no AI cost.
- A Recruiter with `jobs.manage` on the unit's ancestry can claim it via `POST /api/jobs/{id}/claim`; first claimer wins atomically.
- The full existing flow (extract → confirm signals → build pipeline) works post-claim, unchanged.
- The Recruiter can self-publish or send for HM approval. HM approval round-trips correctly, including the "Return to recruiter" path.
- Edit-rights matrix (§7.1) is enforced on every mutation route. Frontend reflects the lock without performing the check.
- Every transition + claim + approve + return is captured in `audit_log`.
- Permission seeds are updated to grant `jobs.create` to HM, `jobs.view` to all roles, and `candidates.manage` to Admin + Recruiter.
- All existing tests continue to pass; new tests cover the new states, transitions, claim race, edit matrix, and migration idempotency.
- No RLS regression; cross-tenant isolation holds across the new columns.
