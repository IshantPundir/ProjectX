# Candidates + Scheduler (Phase 3B + 3C) — Design Spec

**Date:** 2026-04-19
**Status:** Draft (awaiting user review)
**Owner:** Ishant
**Scope:** `backend/nexus/app/modules/candidates/` (new), `backend/nexus/app/modules/scheduler/` (flesh from stub), `backend/nexus/app/modules/session/` (flesh from stub, pre-LiveKit), `frontend/app/app/(dashboard)/candidates/` (new), `frontend/app/app/(interview)/` (new route group)

---

## Goal

Build the candidate management and scheduling layer that sits between the existing JD pipeline (Phase 2A–2C.2) and the interview engine (Phase 3A). Deliver two sequential phases that together produce a fully testable pre-interview flow — invitation dispatch, consent capture, pre-check UX — without requiring LiveKit infrastructure. LiveKit wiring is explicitly deferred to Phase 3D.

## Non-goals

- LiveKit room creation, participant token minting, Egress recording (Phase 3D)
- Post-session scoring and report compilation (Phase 3F)
- Resume parsing or pre-interview fit scoring (Phase 3G)
- Bulk CSV import (future `CsvBulkSource`)
- ATS integrations — Ceipal, Greenhouse, Workday (future adapters under the same `CandidateSource` protocol)
- AI Copilot panel for live observers (Phase 3E)
- Live panel interviews with human interviewers (future stage type)
- SMS invitation channel (email only; SMS is a config swap on the existing `notifications` module when needed)
- Candidate timezone awareness / calendar invites / rescheduling UX

## Success criteria

1. A recruiter can manually add a candidate, assign them to one or more JDs, and move them through kanban stages via drag-and-drop.
2. A recruiter can send an interview invite for an `ai_interview` stage. The candidate receives an email with a single-use link.
3. A candidate can click the link, load the pre-check page (company/role/duration context, proctoring messages, camera/mic test, consent checkbox) as many times as needed without the token being consumed.
4. When the candidate clicks "Start Interview", the atomic single-use check fires. First click succeeds with a 501 sentinel response (`LIVEKIT_INTEGRATION_PENDING`). Replay attempts return 409 with an audit log entry.
5. After session start, clicking the link again loads the pre-check but the Start button indicates "session already started"; backend rejoin path is wired for Phase 3D.
6. GDPR-compliant PII redaction is available with audit trail preservation.
7. Every state transition (candidate created, assigned, stage changed, invite sent, consent given, token used, token replay blocked) writes a row to `audit_log`.
8. The pre-Phase-3 blocker (candidate-JWT single-use enforcement in `middleware/auth.py`) is resolved as part of 3C.

---

## Background

### Product context

ProjectX replaces recruiter phone screens with AI-led video interviews. Phases 1 through 2C.2 built the foundation: auth, multi-tenancy with RLS, JD creation, signal extraction, pipeline builder, and per-stage question bank generation. Phase 3A delivered the standalone interview engine (a LiveKit Agent worker that conducts interviews driven by a deterministic state machine with deterministic question ordering and function-tool-based observation capture).

What's missing is the entire space between "confirmed JD" and "running interview": candidates (who gets interviewed), scheduling (when/how invitations are dispatched), and the candidate-facing pre-check flow.

### User mental model

Recruiters already use ATS platforms like Greenhouse, Workday, and Ceipal. The UI should feel familiar:
- Kanban board per JD, one column per pipeline stage
- Manual drag-drop for stage transitions
- "Send Invite" button per candidate
- Candidate cards show status + session state badges

The product's differentiation is the AI interview itself, not the pipeline management layer. The candidate module should be a minimally-opinionated, well-executed ATS-style CRUD feature.

### Integration with interview engine

The interview engine at `backend/interview_engine/` currently loads session config from a fixture file. In Phase 3D it will load from LiveKit room metadata. For Phase 3C, Nexus creates a `sessions` row but does not mint a LiveKit token — the candidate-facing `/start` endpoint returns `LIVEKIT_INTEGRATION_PENDING` so the frontend can display a clear "integration coming soon" message without breaking the invitation flow.

---

## Approach

### Approach A (selected): Orthogonal stage + session state

Pipeline stage and session state are separate dimensions. `candidate_job_assignments.current_stage_id` tracks kanban position. `sessions` rows track interview attempts with their own state machine. Kanban cards display both: column position (stage) + inline badge (session state).

### Alternatives considered

| Alternative | Why not |
|---|---|
| Session state embedded in `candidate_stage_progress` | Mixes two concerns. Non-interview stages (resume review, reference check) awkwardly share schema with interview stages. Re-interviews within the same stage require schema contortions. |
| Pipeline-driven auto-dispatch on stage entry | Zero-friction but impossible to batch-load candidates without sending invites immediately. Recruiters need explicit control over dispatch timing. Can be added as a stage-config flag in a future phase without data model changes. |

---

## Phase split

Two sequential phases with strict code-level dependency direction (3B module never imports from 3C modules; reverse imports are one-way).

### Phase 3B — Candidates module

- New `app/modules/candidates/` — router, service, authz, schemas, sources abstraction
- Alembic migration 0013: `candidates`, `candidate_job_assignments`, `candidate_stage_progress` + RLS policies + `candidates.view` / `candidates.manage` role seeding
- Frontend: `/candidates` top-level route with list view + JD picker + kanban view; `/candidates/[candidateId]` detail page with Profile / Assignments / Sessions tabs (Sessions tab shows empty state until 3C)
- No invitations, no sessions, no candidate-facing routes yet

**Testable end state:** Full manual CRM — create candidates, assign to JDs, drag-drop through kanban, upload resumes, status management, GDPR redaction.

### Phase 3C — Scheduler + Session (pre-LiveKit)

- Flesh out `app/modules/scheduler/` and `app/modules/session/` stubs
- Alembic migration 0014: `sessions`, `candidate_session_tokens` + RLS
- Candidate JWT minting (`create_candidate_token`) matching existing `verify_candidate_token` pattern
- Email template for invite dispatch under `app/modules/notifications/templates/interview_invite.html`
- Candidate-facing `(interview)` route group with `/interview/<token>/pre-check`
- `/start` endpoint returns 501 sentinel error code `LIVEKIT_INTEGRATION_PENDING`

**Testable end state:** Full invitation flow — recruiter sends invite, candidate receives email, clicks link, takes consent, hits Start button, sees clear "integration pending" message. Single-use enforcement verifiable with replay attempts returning 409.

---

## Architecture

### Module boundaries

```
┌──────────────────┐
│  candidates      │ ← owns candidate identity, assignments, stage progress
│  (3B)            │   NO imports from scheduler or session
└────────┬─────────┘
         │ (reads via public schemas)
         ▼
┌──────────────────┐        ┌──────────────────┐
│  scheduler       │───────▶│  session         │ ← owns session state machine, tokens
│  (3C)            │ writes │  (3C)            │   NO imports from scheduler
└────────┬─────────┘        └──────────────────┘
         │ (dispatches via notifications.send_email)
         ▼
┌──────────────────┐
│  notifications   │ ← existing, provider-agnostic
│  (Phase 1)       │
└──────────────────┘
```

### State machines

**Assignment status** (per-candidate-per-JD):
`active` ⇄ `archived` / `hired` / `rejected` / `withdrawn`
All transitions legal (recruiter has final say). Transitions log to `audit_log`.

**Stage progress** (append-only):
One row per entry into a stage. `entered_at` required, `exited_at` nullable (null = currently in this stage), `outcome` set on exit (`advanced` / `rejected` / `withdrawn`), `override=true` if manual override of system recommendation (relevant when Report builder in Phase 3F starts feeding back classifications).

**Session lifecycle:**
```
created → waiting → pre_check → consented → active → completed
                                                  └→ cancelled
                                                  └→ error
```
Transitions driven by candidate actions (pre-check load, consent, start) or recruiter actions (revoke). Each transition writes `state_changed_at` + audit row.

**Candidate JWT lifecycle:**
Token minted on invite dispatch. `used_at` atomically set on `/start` endpoint success. Single-use semantics: replay returns 409. Rejoin of in-progress session (Phase 3D) uses the same JWT to fetch a fresh LiveKit room token without a second single-use consumption.

---

## Database schema

Two Alembic migrations. All tables follow the canonical RLS pattern established in Phase 2A hardening: `tenant_isolation` with `USING` + `WITH CHECK` + `NULLIF(current_setting('app.current_tenant', true), '')::uuid`, plus `service_bypass`. All tenant-scoped tables added to `_TENANT_SCOPED_TABLES` in `app/main.py` so the startup RLS assertion enforces correctness at boot.

### Migration 0013 — Candidates core (Phase 3B)

**`candidates`** — identity + PII, one row per unique person per tenant

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `tenant_id` | UUID NOT NULL | FK `clients` + RLS |
| `name` | TEXT NOT NULL | |
| `email` | TEXT NOT NULL | Unique per tenant via partial index (see constraints) |
| `phone`, `location`, `current_title`, `linkedin_url` | TEXT NULL | Optional profile fields |
| `resume_s3_key` | TEXT NULL | S3 object key (private bucket, pre-signed URL access) |
| `resume_uploaded_at` | TIMESTAMPTZ NULL | |
| `notes` | TEXT NULL | Long-form recruiter notes |
| `source` | TEXT NOT NULL | `manual` \| `csv` \| `ceipal` \| `greenhouse` \| ... |
| `external_id` | TEXT NULL | ATS-side ID when sourced externally |
| `source_metadata` | JSONB NULL | Whatever the source wanted to preserve |
| `created_by` | UUID NOT NULL | FK `users` |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | `set_updated_at` trigger |
| `pii_redacted_at` | TIMESTAMPTZ NULL | GDPR hard-delete marker — when set, PII columns nulled |
| `pii_redacted_by` | UUID NULL | |

**Constraint:** `UNIQUE (tenant_id, email) WHERE pii_redacted_at IS NULL` — email unique per tenant, but only for non-redacted rows. After redaction the constraint no longer applies (allowing the email address to be reused for a new candidate record if needed).

**Indexes:** `(tenant_id, created_at DESC)` for list pagination; `(tenant_id, email)` for dedup lookup during create.

**`candidate_job_assignments`** — many-to-many candidate↔JD with per-assignment status and kanban position

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL | |
| `candidate_id` | UUID NOT NULL | FK `candidates` |
| `job_posting_id` | UUID NOT NULL | FK `job_postings` |
| `current_stage_id` | UUID NOT NULL | FK `job_pipeline_stages` — which kanban column |
| `status` | TEXT NOT NULL | `active` \| `archived` \| `hired` \| `rejected` \| `withdrawn` |
| `status_changed_at` | TIMESTAMPTZ NOT NULL | |
| `assigned_by` | UUID NOT NULL | |
| `assigned_at`, `updated_at` | TIMESTAMPTZ NOT NULL | |

**Constraint:** `UNIQUE (candidate_id, job_posting_id)` — one assignment per candidate per JD. Status flips allow un-rejecting without row duplication.

**Indexes:** `(tenant_id, job_posting_id, status)` for kanban queries; `(candidate_id)` for "where is Alice?" lookups.

**`candidate_stage_progress`** — append-only audit log of stage transitions

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL | |
| `assignment_id` | UUID NOT NULL | FK `candidate_job_assignments` |
| `stage_id` | UUID NOT NULL | Which stage this row is for |
| `entered_at` | TIMESTAMPTZ NOT NULL | |
| `exited_at` | TIMESTAMPTZ NULL | Null = currently in this stage |
| `outcome` | TEXT NULL | `advanced` \| `rejected` \| `withdrawn` \| null (still active) |
| `moved_by` | UUID NULL | Null when future auto-advance fires from Phase 3F classifications |
| `override` | BOOLEAN NOT NULL DEFAULT FALSE | True = manual override of system recommendation |
| `reason` | TEXT NULL | Optional free-form |

**Index:** `(tenant_id, stage_id) WHERE exited_at IS NULL` for the load-bearing kanban query.

### Migration 0014 — Sessions + tokens (Phase 3C)

**`sessions`** — one row per interview attempt

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL | |
| `assignment_id` | UUID NOT NULL | FK `candidate_job_assignments` |
| `stage_id` | UUID NOT NULL | FK `job_pipeline_stages` — which stage this session is for |
| `state` | TEXT NOT NULL | `created` \| `waiting` \| `pre_check` \| `consented` \| `active` \| `completed` \| `cancelled` \| `error` |
| `state_changed_at` | TIMESTAMPTZ NOT NULL | |
| `consent_recorded_at` | TIMESTAMPTZ NULL | AIVIA audit marker |
| `otp_required` | BOOLEAN NOT NULL DEFAULT FALSE | Schema ready in 3C; UX wired in Phase 3D |
| `scheduled_for` | TIMESTAMPTZ NULL | Future: explicit scheduling |
| `started_at`, `completed_at` | TIMESTAMPTZ NULL | |
| `livekit_room_name` | TEXT NULL | Populated in Phase 3D |
| `recording_s3_key` | TEXT NULL | Populated post-Egress (Phase 3D) |
| `created_by` | UUID NOT NULL | Recruiter who dispatched |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | |

**Indexes:** `(tenant_id, assignment_id, state)` for active-sessions-per-assignment queries.

**`candidate_session_tokens`** — single-use JWT tracking + audit

| Column | Type | Notes |
|---|---|---|
| `jti` | UUID PK | JWT ID claim |
| `tenant_id` | UUID NOT NULL | |
| `session_id` | UUID NOT NULL | FK `sessions` |
| `issued_at`, `expires_at` | TIMESTAMPTZ NOT NULL | |
| `used_at` | TIMESTAMPTZ NULL | First successful `/start` call timestamp |
| `used_ip`, `used_user_agent` | TEXT NULL | Audit trail |

**Single-use query:**
```sql
UPDATE candidate_session_tokens
SET used_at = now(), used_ip = $1, used_user_agent = $2
WHERE jti = $3 AND used_at IS NULL
RETURNING *
```
Zero rows returned → token was already used → replay → 409 response + `session.token_replay_blocked` audit event.

---

## API surface

All dashboard endpoints authenticated via existing `AuthMiddleware`. Candidate-facing endpoints under `/api/candidate-session/{token}/...` have the token extracted from the URL path by the middleware (already working in Phase 1).

### Candidates endpoints (Phase 3B)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/api/candidates` | `candidates.manage` | Create candidate (manual source only in 3B) |
| `GET` | `/api/candidates` | `candidates.view` | List/search — query params: `q` (substring on name/email), `job_id`, `stage_id`, `status`, pagination |
| `GET` | `/api/candidates/{id}` | `candidates.view` | Detail: profile + all assignments + per-assignment session history |
| `PATCH` | `/api/candidates/{id}` | `candidates.manage` | Update profile fields |
| `POST` | `/api/candidates/{id}/resume` | `candidates.manage` | Returns `{upload_url, s3_key}` (pre-signed for direct S3 upload) |
| `POST` | `/api/candidates/{id}/resume/confirm` | `candidates.manage` | Body: `{s3_key}`. Backend HEAD on S3 → if object exists + content-type is PDF → update `resume_s3_key` + `resume_uploaded_at` |
| `DELETE` | `/api/candidates/{id}/resume` | `candidates.manage` | Delete S3 object + clear columns |
| `POST` | `/api/candidates/{id}/redact-pii` | `candidates.manage` + super_admin | GDPR hard-delete of PII columns, body requires explicit confirmation token |
| `POST` | `/api/candidates/{id}/assignments` | `candidates.manage` + `jobs.manage` | Assign to JD. Body: `{job_posting_id, target_stage_id?}` (defaults to JD's first stage). Creates assignment + first progress row. |
| `PATCH` | `/api/candidates/{id}/assignments/{assignment_id}` | `candidates.manage` + `jobs.manage` | Update status |
| `POST` | `/api/candidates/{id}/assignments/{assignment_id}/transition` | `candidates.manage` + `jobs.manage` | Kanban drag-drop. Body: `{target_stage_id, reason?}`. Sets `exited_at` on current progress row + inserts new one. |
| `GET` | `/api/jobs/{job_id}/candidates/kanban` | `jobs.view` + `candidates.view` | Optimized board render: returns `{stages: [{stage_id, stage_name, candidates: [...]}, ...]}` in a single query |

### Scheduler endpoints (Phase 3C)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/api/scheduler/invites` | `candidates.manage` + `jobs.manage` | Send interview invite. Body: `{assignment_id, stage_id, otp_required?}`. Creates `sessions` row + `candidate_session_tokens` row + dispatches email via notifications module. Validates `stage.stage_type == 'ai_interview'` (422 otherwise). Returns `{session_id, token_expires_at}`. |
| `POST` | `/api/scheduler/invites/{session_id}/resend` | `candidates.manage` + `jobs.manage` | Supersedes prior token, mints new one, resends email |
| `POST` | `/api/scheduler/invites/{session_id}/revoke` | `candidates.manage` + `jobs.manage` | Invalidates outstanding token, marks session `cancelled` |

### Session endpoints (Phase 3C)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `GET` | `/api/sessions/{id}` | `jobs.view` on session's JD ancestry | Session detail for recruiter dashboard |
| `GET` | `/api/sessions` | `jobs.view` | List sessions with filters (`assignment_id`, `state`, date range) |

### Candidate-facing endpoints (Phase 3C)

Token extracted from URL path by `AuthMiddleware`, attached to `request.state.candidate_token_payload`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/candidate-session/{token}/pre-check` | Returns session context (company name, role, duration, stage name, consent text). Token NOT marked used. Idempotent. |
| `POST` | `/api/candidate-session/{token}/consent` | Body: `{consented: true, user_agent}`. Records consent timestamp. Session state → `consented`. Token still not marked used. |
| `POST` | `/api/candidate-session/{token}/start` | Atomic single-use check (see Database schema). On first success: returns 501 with sentinel error code `LIVEKIT_INTEGRATION_PENDING` (Phase 3C). On replay: returns 409 `TOKEN_ALREADY_USED`. In Phase 3D: returns `{server_url, participant_token}`. |

### Error codes

| Code | Status | Used by |
|---|---|---|
| `LIVEKIT_INTEGRATION_PENDING` | 501 | `/start` endpoint in Phase 3C (sentinel for frontend to display "coming soon" message) |
| `TOKEN_ALREADY_USED` | 409 | `/start` endpoint on replay |
| `INVALID_STAGE_TYPE_FOR_INVITE` | 422 | Scheduler invite when stage is not `ai_interview` |
| `CANDIDATE_HAS_ACTIVE_SESSION` | 409 | GDPR redact when any assignment has a session in `active` state |
| `ASSIGNMENT_ALREADY_EXISTS` | 409 | Assignment create when candidate already assigned to JD |
| `STAGE_NOT_IN_PIPELINE` | 422 | Transition / assignment create when target stage doesn't belong to JD's pipeline |

---

## Authz

### New permissions

Two additions to `app/modules/auth/permissions.py`:

- `candidates.view` — read candidate profiles, assignments, session history
- `candidates.manage` — create / edit / assign / transition / send invites / revoke / redact PII

### Seeding (migration 0013)

| Role | `candidates.view` | `candidates.manage` |
|---|---|---|
| Admin | ✅ | ✅ |
| Recruiter | ✅ | ✅ |
| Hiring Manager | ✅ | ❌ |
| Interviewer | ❌ | ❌ |
| Observer | ❌ | ❌ |

### New helper: `require_candidate_access`

Lives in `app/modules/candidates/authz.py`, mirrors `require_job_access` in `jd/authz.py`:

1. Load candidate (tenant RLS handles 404)
2. Super admin short-circuit
3. Fetch all assignments for this candidate
4. If assignments exist: walk each JD's org unit ancestry, check `candidates.{action}` — allow on any match
5. If no assignments (unassigned talent-pool candidate): check if user has `candidates.{action}` anywhere in their role assignments — allow on match
6. Otherwise 403 with clear detail

### Candidate list endpoint authz

Following the phase-2a ancestry-filter pattern on jobs list:
- Super admin sees all tenant candidates
- Non-super-admin sees union of: (a) candidates assigned to any JD in their ancestry-reachable org units, (b) unassigned candidates if they have `candidates.view` anywhere

Single query with `DISTINCT` join.

### Compound checks for assignment operations

Assignment create / update / transition endpoints require BOTH `require_candidate_access(action="manage")` AND `require_job_access(action="manage")` on the target JD. Distinct error messages depending on which check rejected.

### Scheduler invite authz

1. `require_candidate_access(action="manage")`
2. `require_job_access(action="manage")` on `assignment.job_posting_id`
3. Stage-type guard: `assignment.current_stage.stage_type == 'ai_interview'` (422 otherwise)

### Audit log integration

Every state-changing operation writes to `audit_log` via `log_event()`:

| Action | Event type | Subject |
|---|---|---|
| Candidate created | `candidate.created` | candidate_id |
| PII redacted | `candidate.pii_redacted` | candidate_id |
| Assignment created | `candidate.assigned` | candidate_id |
| Stage transition | `candidate.stage_transitioned` | assignment_id |
| Invite dispatched | `session.invite_sent` | session_id |
| Invite revoked | `session.invite_revoked` | session_id |
| Consent recorded | `session.consent_recorded` | session_id (AIVIA trail) |
| Session token used | `session.token_used` | jti |
| Session token replay attempt | `session.token_replay_blocked` | jti |

---

## Frontend structure

### Dashboard route: `/candidates`

```
frontend/app/app/(dashboard)/candidates/
├── page.tsx                          ← Server component: auth guard
├── ClientCandidatesPage.tsx          ← Client component: URL state, JD picker, view toggle
├── CandidateListView.tsx             ← Flat list with search + filters
├── CandidateKanbanView.tsx           ← Kanban board (requires JD selected)
├── CandidateKanbanColumn.tsx         ← Single stage column with drag-drop
├── CandidateKanbanCard.tsx           ← Candidate card with status + session badges
├── AddCandidateDialog.tsx            ← Modal form (RHF + Zod)
├── ResumeUploadField.tsx             ← Two-step S3 upload
├── SendInviteDialog.tsx              ← Confirmation before dispatch (3C)
└── [candidateId]/
    ├── page.tsx                      ← Detail page
    ├── CandidateProfileTab.tsx
    ├── CandidateAssignmentsTab.tsx
    └── CandidateSessionsTab.tsx
```

**URL shape:**

| URL | View |
|---|---|
| `/candidates` | Cross-JD flat list |
| `/candidates?jd=<id>` | JD-scoped list |
| `/candidates?jd=<id>&view=kanban` | JD-scoped kanban (default for JD-selected) |
| `/candidates/<id>` | Detail |

### Candidate-facing route group: `(interview)` (Phase 3C)

```
frontend/app/app/(interview)/
├── layout.tsx                        ← Minimal full-viewport, no nav
└── [token]/
    ├── pre-check/
    │   ├── page.tsx                  ← Fetches /pre-check endpoint
    │   ├── ConsentPanel.tsx          ← Proctoring messages + consent checkbox
    │   ├── CameraMicTest.tsx         ← getUserMedia, local preview, test phrase
    │   └── StartInterviewButton.tsx  ← Calls /start → handles 501 in 3C, redirects in 3D
    └── error/page.tsx                ← Expired / used / invalid token landing
```

### Shared components

```
components/dashboard/candidates/
├── StatusBadge.tsx                   ← Assignment status (active/archived/etc.)
├── SessionStatusBadge.tsx            ← Session state (not-invited/invited/etc.)
├── StageTransitionDropdown.tsx       ← Shared: kanban drag + assignment table
└── JdPicker.tsx                      ← Searchable JD selector
```

### Patterns (follow existing conventions)

- `lib/api/candidates.ts`, `lib/api/scheduler.ts`, `lib/api/candidate-session.ts` — typed API namespaces
- `lib/hooks/use-candidates-list.ts`, `use-candidate.ts`, `use-kanban-board.ts`, `use-transition-candidate.ts`, `use-send-invite.ts`, `use-resume-upload.ts`
- Query keys: `['candidates-list', filters]` / `['candidates', id]` / `['candidates-kanban', jobId]` / `['sessions', assignmentId]` — all distinct prefixes per existing Batch A query-key discipline fix
- Drag-drop via `@dnd-kit` with `KeyboardSensor` for a11y (matches `PipelineFlowColumn.tsx` pattern)
- Optimistic mutation on drag-drop; rollback on 409
- Forms via React Hook Form + Zod with field-level error mapping from 422 responses
- Toast via existing `sonner` Toaster in `DashboardProviders`

---

## Testing strategy

### Backend unit tests

| Module | Focus |
|---|---|
| `candidates/service` | CRUD + dedup on (tenant, email), resume two-step upload (mocked S3 HEAD), PII redaction atomicity, `ManualSource` writes expected metadata shape |
| `candidates/authz` | `require_candidate_access` — assigned + unassigned + super admin + ancestry walk + compound check |
| Stage transitions | Drag forward/backward/skip, concurrent transition row lock, audit log shape |
| `scheduler/service` | Invite creates session + token atomically, stage-type guard (422), resend supersedes prior token, revoke invalidates |
| `session/service` | Single-use token atomicity (parallel UPDATE race: exactly 1 wins), rejoin flow (active → fresh stub; completed → 409), consent idempotency |
| RLS boot check | Startup assertion fails if any new table lacks policies — enforced via `_TENANT_SCOPED_TABLES` |

### End-to-end happy path

Real Postgres + Redis, mocked S3 + Resend:

1. Create candidate Alice with resume
2. Assign Alice to JD X at stage 0
3. Drag Alice from stage 0 → stage 1 (progress rows correct)
4. Send invite for stage 1
5. Simulate candidate `/pre-check` → state = pre_check
6. Simulate `/consent` → state = consented, timestamp recorded
7. Simulate `/start` → atomic UPDATE succeeds, 501 `LIVEKIT_INTEGRATION_PENDING`
8. Simulate `/start` replay → 0 rows affected, 409 `TOKEN_ALREADY_USED`
9. Audit log has one row per step
10. Redact Alice's PII → columns nulled, session + audit trail intact

### Frontend tests (Vitest + Testing Library)

- `AddCandidateDialog` form validation
- Kanban drag-drop optimistic update + rollback on 409
- Resume upload two-step flow (pre-sign → PUT → confirm)

### Manual demo checklist

- Add 5 candidates with varied fields
- Assign each to 2 different JDs (exercise many-to-many)
- Kanban view for one JD → drag through all stages
- Send invite for 1 candidate → receive email via Resend dry-run provider
- Click email link → pre-check loads
- Consent → verify state + timestamp
- Start Interview → friendly "integration pending" message
- Reload pre-check page → still works (token not used)
- Click Start again → 409 replay blocked
- Revoke an active invite → email becomes invalid, state = cancelled
- GDPR-redact → PII gone, audit trail intact

---

## Known gaps / deferred

**In 3B:**
- Bulk CSV import (future `CsvBulkSource`)
- ATS integrations under existing `CandidateSource` protocol
- Resume parsing / pre-interview fit scoring (Phase 3G)
- Candidate tagging, custom fields, saved search filters
- Fuzzy duplicate detection (MVP: exact email match)
- Candidate communication history
- Full-text search (MVP: ILIKE on name + email — fine up to ~10k candidates per tenant)

**In 3C:**
- LiveKit room creation + token minting (Phase 3D)
- Egress recording config (Phase 3D)
- Multi-party scheduling / live panel interviews
- Calendar `.ics` invites
- OTP at pre-check (schema ready via `sessions.otp_required`, UX in Phase 3D)
- Candidate timezone awareness
- SMS channel (email only)
- Rescheduling UX (recruiter can revoke + re-send, but no "move to Tuesday 3pm")

**Pre-Phase-3 blocker landing in 3C:**
- Candidate-JWT single-use enforcement — the TODO in `middleware/auth.py` resolved by the `candidate_session_tokens` atomic UPDATE

---

## Risks

| Risk | Mitigation |
|---|---|
| Kanban performance degrades at 500+ candidates per stage | Partial index on `(tenant_id, stage_id) WHERE exited_at IS NULL` + pagination in `/kanban`; revisit if real clients hit this |
| Candidate-facing JWT leaks via email forwarding | 72h TTL + single-use on start + audit trail; OTP available per-JD (schema ready, UX in Phase 3D) |
| Resume S3 uploads succeed but confirm step fails | Orphaned S3 objects → periodic cleanup via Dramatiq weekly task (implemented in 3B) |
| Concurrent stage transitions race | Row lock on `candidate_job_assignments` during transition + append-only progress rows means no corruption, just "last writer wins" on `current_stage_id` |
| GDPR redaction during active session | Redact endpoint returns 409 `CANDIDATE_HAS_ACTIVE_SESSION` if any assignment has a session in `active` state — must wait for completion |
| `pii_redacted_at IS NULL` partial unique index edge cases | Tested explicitly — redacted row must allow a new candidate with the same email |

---

## Open questions

None as of 2026-04-19. The following are explicitly resolved:
- Candidate uniqueness: per-tenant by email (partial unique index)
- Resume handling: optional PDF upload via pre-signed URL with two-step confirmation
- Kanban placement: top-level `/candidates` with JD picker, list ↔ kanban toggle
- Drag-drop rules: all forward/backward/skip allowed with audit trail (recruiter override always wins)
- Permission model: hybrid `candidates.X` + `jobs.X` seeded identically to start, split later if needed
- Bulk upload scope: manual-only in 3B, abstraction-ready for `CsvBulkSource`
- Delete semantics: per-assignment status enum + GDPR hard-delete with PII redaction preserving referential integrity
- Single-use JWT: DB-backed `candidate_session_tokens` + atomic UPDATE, invalidate on session start (not pre-check load), allow rejoin of active sessions
- Stage ↔ session model: orthogonal (separate tables, separate state machines)
- Phase split: 3B candidates-only (no invitations), 3C scheduler + session + pre-check UX (no LiveKit)

---

## Cross-references

- `backend/nexus/CLAUDE.md` — architectural rules, RLS pattern, auth abstraction, candidate JWT single-use TODO
- `frontend/app/CLAUDE.md` — frontend tech stack, component placement, Base UI gotchas, query-key discipline, drag-drop a11y
- `docs/phase-2a-implementation.md` — existing ancestry-walking authz pattern (`require_job_access`)
- `docs/phase-2c1-implementation.md` — pipeline builder architecture, stage model
- `docs/phase-hardening-implementation.md` — RLS canonical pattern, startup assertion, candidate JWT hardening gap
- `docs/superpowers/specs/2026-04-15-docs-refresh-design.md` — recent docs refresh (companion to this spec)
- `backend/nexus/migrations/versions/0012_rename_service_role_bypass.py` — current Alembic head; new migrations 0013, 0014 stack on top
- `backend/nexus/app/modules/jd/authz.py` — canonical authz helper pattern to mirror
- `backend/nexus/app/modules/notifications/` — existing provider-agnostic email dispatch (reused by scheduler)
- `backend/nexus/app/middleware/auth.py` — existing candidate JWT extraction path (no changes needed; single-use check lives in session service layer)
