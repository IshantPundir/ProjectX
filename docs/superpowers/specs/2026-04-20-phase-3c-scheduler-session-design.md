# Phase 3C — Scheduler + Session (pre-LiveKit) Design Spec

**Date:** 2026-04-20
**Status:** Draft (awaiting user review)
**Owner:** Ishant
**Scope:** `backend/nexus/app/modules/scheduler/`, `backend/nexus/app/modules/session/`, `backend/nexus/app/middleware/auth.py`, `backend/nexus/app/modules/notifications/templates/`, `frontend/app/app/(interview)/` (new route group), additive surgical edits to dashboard candidates UI

**Supersedes:** the 3C portion of `docs/superpowers/specs/2026-04-19-candidates-scheduler-design.md`. That earlier document was the joint 3B + 3C design. Phase 3B shipped at tag `phase-3b-complete` (merge commit `eb67a7a`). This spec re-scopes Phase 3C with two post-3B deltas:

- **OTP UX lands in 3C, not 3D.** Candidate enters a 6-digit code requested via a [Send code] button during the pre-check wizard. The earlier spec kept only a schema flag.
- **Phase split.** 3C ships as two sub-phases — **3C.1 backend** then **3C.2 frontend** — merged back-to-back as one rollup to `main`.

---

## Goal

Build the invitation layer between a confirmed candidate assignment and the (future) LiveKit interview room. A recruiter sends an invite for an `ai_interview` stage; the candidate receives a branded email with a single-use JWT link; the candidate clicks through a guided pre-check wizard (consent → OTP → camera/mic test → Start); the Start button runs the atomic single-use check and returns a `LIVEKIT_INTEGRATION_PENDING` sentinel response. Replay attempts return 409 with an audit log entry.

LiveKit wiring is explicitly deferred to Phase 3D.

## Non-goals

- LiveKit room creation, participant token minting, Egress recording (Phase 3D)
- Post-session scoring + report compilation (Phase 3F)
- AI Copilot panel for live observers (Phase 3E)
- Live panel interviews with human interviewers (future)
- SMS invitation / OTP channel (email only; SMS remains a notifications-module config swap when Twilio is wired)
- Calendar `.ics` invites + candidate timezone awareness
- Rescheduling UX beyond revoke + resend
- Bulk CSV import of candidates (future — orthogonal to this phase)

## Success criteria

1. A recruiter can send an interview invite from the kanban card or assignment row for a candidate in an `ai_interview` stage. Non-`ai_interview` stages are rejected with 422 `INVALID_STAGE_TYPE_FOR_INVITE`.
2. The candidate receives a single email containing a link (`/interview/<token>`) + company / role / duration context.
3. Clicking the link loads the pre-check page idempotently as many times as needed; the backend state and the wizard step resume correctly across reloads.
4. The wizard presents four sequential steps — Consent, OTP (if required), Camera + microphone, Start — with server-verified state gating each transition.
5. A candidate can request a fresh OTP via a `[Send code]` button (rate-limited 1 req / 60 s). The code is a 6-digit numeric, valid for 10 minutes, with a 3-attempt cap before requiring re-issue.
6. Clicking "Start Interview" fires the atomic single-use token check. First success returns `501 LIVEKIT_INTEGRATION_PENDING`; replay returns `409 TOKEN_ALREADY_USED` and writes an audit event.
7. Revoke invalidates the outstanding token and moves the session to `cancelled`. Resend supersedes the prior token (mints a fresh JWT + new `candidate_session_tokens` row, resets OTP state, invalidates the prior token atomically).
8. Every state transition, consent event, OTP issuance, and OTP verification writes to `audit_log`. GDPR-compliant audit trail preserved through PII redaction of the linked candidate.
9. The candidate-JWT single-use enforcement TODO in `middleware/auth.py` is resolved as part of 3C.1 — the atomic `UPDATE … WHERE used_at IS NULL RETURNING` lives in the session-start service path.
10. The dashboard's `SessionStatusBadge` on kanban cards now reflects real session state. The candidate detail page's Sessions tab lists session history.

---

## Background

Phase 3B delivered candidate identity + assignments + kanban + resume + PII redaction — the full pipeline-management surface minus any candidate-facing activity. Phase 3C fills the gap between a confirmed assignment and a (future) LiveKit interview room. The end-to-end testability of 3C hinges on the fact that LiveKit is *explicitly* out of scope: the `/start` endpoint returns a 501 sentinel that the frontend renders as a friendly "integration coming soon" panel. This lets us validate the invite → pre-check → consent → OTP → start flow without any real-time media infrastructure.

### Why OTP moved forward

The earlier joint spec left OTP as schema-only (a `sessions.otp_required` boolean) with UX deferred to 3D, reasoning that OTP is a bolt-on to a working session flow. Pulling it into 3C has three benefits:

1. **UX completeness.** The pre-check wizard ships with its real final shape rather than adding a new step later.
2. **No post-launch schema churn.** Adding OTP fields to `sessions` in a later migration would require careful state-machine coexistence; doing it now keeps the shape stable.
3. **Anti-link-sharing posture.** The OTP is the friction that makes candidate invites meaningfully single-person even though the link is delivered to the same inbox as the OTP email. Without it, `otp_required=true` never actually gates anything in 3C — just a dead column.

### Why the 3C.1 / 3C.2 split

Shipping backend + frontend together produces a big merge diff and makes code review expensive. Splitting lets us:

- Land migration 0014 + scheduler/session services under tests before any candidate-facing UI exists (lower blast radius on review).
- Exercise backend endpoints via integration tests and manual curl before the browser is on the path.
- Keep the frontend merge focused on the wizard UX.

Both sub-phases merge back-to-back; there is no intermediate "released at 3C.1 only" state.

---

## Approach

### Selected approach: OTP as session-level flag + dedicated request/verify endpoints

- `sessions.otp_required` is copied from `job_pipeline_stages.otp_required_default` at invite dispatch; recruiters may override per invite.
- `POST /api/candidate-session/{token}/request-otp` mints a fresh 6-digit code, hashes it with a cryptographic function appropriate for short codes (SHA-256 HMAC with a server-side secret is sufficient; bcrypt is acceptable but overkill given a 10⁶ search space and hash-wipe-on-verify), writes `otp_hash` + `otp_issued_at` + resets `otp_attempts = 0`, and dispatches an email through the existing notifications module. Rate-limited.
- `POST /api/candidate-session/{token}/verify-otp` validates the code against the hash, increments `otp_attempts` on miss, caps at 3 attempts before forcing re-issue, wipes the hash on success and stamps `otp_verified_at`.
- `/start` requires `state == consented` AND (if `otp_required`) `otp_verified_at IS NOT NULL` before the atomic single-use UPDATE.

OTP verification is a *flag* on the session row, not a new state in the state machine. This keeps the state-machine surface small (`created → pre_check → consented → active → completed/cancelled/error`).

### Alternatives considered

| Alternative | Why not |
|---|---|
| OTP posted inline with `/start` body | No "code verified ✓" feedback before the candidate commits — failed OTP during the critical single-use call is hostile UX. |
| OTP emailed alongside the invite link in the same message | Candidate would have the code upfront but the code's expiry window is fuzzy (tied to token TTL, ~72h) — weaker attack story and no way to re-issue. User confirmed "Send code" button model. |
| JD-level `otp_required_default` | Less granular than stage-level. Some pipelines mix casual phone screens with formal panels; stage-level gives the right control knob. |
| Per-candidate OTP column (instead of hashing + wiping) | Storing plaintext codes expands blast radius on DB compromise. Hashing + wipe-on-verify gives us audit presence without replay surface. |

---

## Phase split

Two sub-phases with a strict one-way code dependency: 3C.2 imports from 3C.1 only.

### Phase 3C.1 — Backend

- Alembic migration 0014:
  - `job_pipeline_stages.otp_required_default BOOLEAN NOT NULL DEFAULT FALSE`
  - Upgrade of existing `sessions` stub: drop `candidate_id`, add `assignment_id`, `stage_id`, full state-machine columns, OTP columns
  - New `candidate_session_tokens` table + RLS + `_TENANT_SCOPED_TABLES` registration
- `app/modules/candidates/__init__.py` unchanged — 3B code untouched
- `app/modules/scheduler/` fleshed from stub: service + router + schemas + errors + authz
- `app/modules/session/` fleshed from stub: service + router + schemas + errors (state machine module)
- `app/modules/notifications/templates/interview_invite.html` new
- `app/modules/auth/service.py` — `create_candidate_token()` helper
- `app/middleware/auth.py` — atomic single-use token verification
- `app/main.py` — register new routers + add both new tables to `_TENANT_SCOPED_TABLES` + 6 new exception handlers
- Full pytest coverage; integration test covering invite → consent → request-otp → verify-otp → start → replay

**Testable end state:** recruiter dispatches via curl, candidate exercises all five candidate-facing endpoints via curl, integration test green, startup RLS assertion green.

### Phase 3C.2 — Frontend

- New `(interview)` route group at `frontend/app/app/(interview)/` with minimal full-viewport layout (no dashboard chrome, no Supabase auth dependency)
- `/interview/[token]` → server-side check of session state via `/pre-check`, renders the appropriate wizard step
- Four wizard step components: `<ConsentStep>`, `<OtpStep>`, `<CameraMicStep>`, `<StartStep>`
- API namespace at `lib/api/candidate-session.ts` (token-scoped — no Supabase bearer)
- Hooks: `use-candidate-session.ts`, `use-request-otp.ts`, `use-verify-otp.ts`, `use-start-session.ts`
- Additive dashboard changes:
  - `SendInviteDialog` from kanban card + assignment row
  - `SessionStatusBadge` shows real states
  - Candidate detail page Sessions tab populated (server-side list via new `GET /api/sessions` filter by `assignment_id`)
  - Scheduler API namespace at `lib/api/scheduler.ts`
  - Hooks: `use-send-invite.ts`, `use-revoke-invite.ts`, `use-resend-invite.ts`, `use-assignment-sessions.ts`

**Testable end state:** full manual demo checklist — recruiter clicks Send Invite, candidate email lands in Resend dry-run log, link opens the wizard, all four steps pass, 501 sentinel rendered, replay blocked, state transitions visible in dashboard.

---

## Architecture

### Module boundaries

```
┌──────────────────┐
│  candidates (3B) │  unchanged
└────────┬─────────┘
         │  reads via Candidate / CandidateJobAssignment
         ▼
┌──────────────────┐        ┌──────────────────┐
│  scheduler       │───────▶│  session         │  owns session state machine,
│  (3C.1)          │ writes │  (3C.1)          │  candidate JWT, atomic single-use,
└────────┬─────────┘        │                  │  OTP lifecycle
         │                  └──────────────────┘
         │ dispatches via notifications.send_email
         ▼
┌──────────────────┐
│  notifications   │  existing Resend + DryRun providers + new template
│  (Phase 1)       │
└──────────────────┘
```

- `session` never imports `scheduler`. `scheduler` calls into `session.create_session(...)`, `session.mint_token(...)`, and `session.supersede_token(...)`.
- `middleware/auth.py` performs JWT *verification* only: signature, expiry, JTI exists in `candidate_session_tokens`, and `superseded_at IS NULL`. It never touches `used_at`.
- The atomic single-use UPDATE (`used_at = now() WHERE used_at IS NULL`) lives in `session/service.py` and is invoked exclusively from the `/start` endpoint service path. Other candidate-session endpoints (pre-check, consent, request-otp, verify-otp) pass JWT verification without consuming the token.

### State machines

**Session lifecycle (unchanged from 3B+3C joint spec):**

```
created → pre_check → consented → active → completed
                                     ├→ cancelled
                                     └→ error
```

- `created` — invite dispatched, email queued, token minted, candidate has not interacted
- `pre_check` — candidate loaded pre-check page (first `GET /pre-check` sets this)
- `consented` — candidate POSTed consent; `consent_recorded_at` stamped
- `active` — candidate successfully hit `/start`; token is marked used
- `completed` — (Phase 3D)
- `cancelled` — recruiter revoked before `/start`
- `error` — (Phase 3D — unrecoverable runtime)

**OTP is a flag, not a state.** Columns `otp_required` / `otp_hash` / `otp_issued_at` / `otp_attempts` / `otp_verified_at` on the `sessions` row. `/start` gating logic:

```python
if session.state != "consented":
    raise IllegalStartStateError                    # 409 INVALID_SESSION_STATE
if session.otp_required and session.otp_verified_at is None:
    raise OtpRequiredError                          # 422 OTP_REQUIRED
# atomic single-use UPDATE on candidate_session_tokens
```

### Candidate JWT structure

Mint at invite dispatch via new `app.modules.auth.service.create_candidate_token()`. HS256, signed with `CANDIDATE_JWT_SECRET` (existing env var).

Claims:

| Claim | Purpose |
|---|---|
| `jti` | UUID — PK in `candidate_session_tokens`, used by single-use UPDATE |
| `sub` | `candidate_id` |
| `session_id` | FK to `sessions.id` |
| `tenant_id` | used by RLS context |
| `iat` | issued-at |
| `exp` | 72 h from `iat` (configurable via `CANDIDATE_JWT_TTL_HOURS`) |

Not used: `aud`, `iss` (HS256 path is fully internal; no algorithm confusion surface).

### Candidate-JWT single-use — the atomic UPDATE

`middleware/auth.py` currently has a `# TODO` for candidate JWTs. The resolution:

- All candidate-session endpoints other than `/start` verify the JWT (signature + expiry) **without** marking used. The token's JWT claims are safe to read across the idempotent endpoints.
- `/start` alone invokes:

```sql
UPDATE public.candidate_session_tokens
SET used_at = now(),
    used_ip = $1,
    used_user_agent = $2
WHERE jti = $3
  AND used_at IS NULL
  AND expires_at > now()
  AND superseded_at IS NULL
RETURNING jti;
```

Zero rows returned → 409 `TOKEN_ALREADY_USED` (+ `session.token_replay_blocked` audit event).
One row → mark session state → active, return `LIVEKIT_INTEGRATION_PENDING` sentinel.

The atomic `WHERE used_at IS NULL` is the single-use guarantee. Multiple concurrent `/start` requests on the same token: exactly one wins.

### Why not enforce single-use in middleware for all endpoints

The pre-check page can be reloaded, the OTP can be re-requested, consent can be re-posted (though idempotently). These are all legitimate re-uses of the same JWT. Single-use is specifically about *session start*, not token authentication. Middleware authenticates; `/start` consumes.

---

## Database schema

One Alembic migration: **0014_sessions_scheduler_core**. Down-revision: `0013_candidates_core`.

### `job_pipeline_stages` — ALTER

Add `otp_required_default BOOLEAN NOT NULL DEFAULT FALSE`. Nullable not needed — pipeline stages created pre-migration default to `false`. This is additive; no existing reads change shape.

### `sessions` — UPGRADE

Existing 2A-era stub columns:

```
id UUID PK
tenant_id UUID
job_posting_id UUID
candidate_id UUID               ← drop
status TEXT                     ← rename to `state`, migrate values
started_at TIMESTAMPTZ
completed_at TIMESTAMPTZ
created_at, updated_at TIMESTAMPTZ
```

Post-migration shape:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID NOT NULL | FK `clients` + RLS |
| `assignment_id` | UUID NOT NULL | FK `candidate_job_assignments` (ON DELETE CASCADE) |
| `stage_id` | UUID NOT NULL | FK `job_pipeline_stages` |
| `state` | TEXT NOT NULL | CHECK IN ('created','pre_check','consented','active','completed','cancelled','error'). Default 'created'. |
| `state_changed_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `consent_recorded_at` | TIMESTAMPTZ NULL | AIVIA compliance marker |
| `otp_required` | BOOLEAN NOT NULL DEFAULT FALSE | Copied from stage default at invite; recruiter can override |
| `otp_hash` | TEXT NULL | Cryptographic hash of current OTP (SHA-256 HMAC recommended); NULL when none issued or already verified |
| `otp_issued_at` | TIMESTAMPTZ NULL | For 10-minute lifetime check |
| `otp_attempts` | INTEGER NOT NULL DEFAULT 0 | 3 max before re-issue required |
| `otp_verified_at` | TIMESTAMPTZ NULL | Set on verify success; wipes `otp_hash` |
| `scheduled_for` | TIMESTAMPTZ NULL | Future use; unset in 3C (invites are "come anytime within 72h") |
| `started_at` | TIMESTAMPTZ NULL | Set on `/start` success |
| `completed_at` | TIMESTAMPTZ NULL | Phase 3D |
| `livekit_room_name` | TEXT NULL | Phase 3D |
| `recording_s3_key` | TEXT NULL | Phase 3D (post-Egress) |
| `created_by` | UUID NOT NULL | FK `users` — the recruiter who dispatched |
| `created_at`, `updated_at` | TIMESTAMPTZ NOT NULL | `set_updated_at` trigger |

**Migration of existing data:** the `sessions` stub is empty in all non-local environments (confirmed — Phase 2A/2B/2C never wrote to it). Local dev may have orphan rows; migration truncates the stub and rebuilds with the new shape. Safer than a column-by-column ALTER because the stub's `candidate_id` had no FK and the `status` default differs.

**Indexes:**
- `(tenant_id, assignment_id, state)` — recruiter "active sessions for this assignment" query
- `(tenant_id, state, state_changed_at DESC) WHERE state IN ('created','pre_check','consented')` — pending-invites dashboard query

**RLS:** canonical pair (tenant_isolation USING + WITH CHECK, service_bypass USING) with `NULLIF(...)::uuid`.

### `candidate_session_tokens` — NEW

| Column | Type | Notes |
|---|---|---|
| `jti` | UUID PK | JWT ID claim |
| `tenant_id` | UUID NOT NULL | FK `clients` + RLS |
| `session_id` | UUID NOT NULL | FK `sessions` (ON DELETE CASCADE) |
| `issued_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `expires_at` | TIMESTAMPTZ NOT NULL | `issued_at + CANDIDATE_JWT_TTL_HOURS` |
| `used_at` | TIMESTAMPTZ NULL | Atomic single-use — set ONLY by `/start` |
| `used_ip` | INET NULL | Audit |
| `used_user_agent` | TEXT NULL | Audit |
| `superseded_at` | TIMESTAMPTZ NULL | Non-NULL when resend minted a fresh token for the same session |
| `superseded_by` | UUID NULL | FK to the successor `candidate_session_tokens.jti` |

**Indexes:**
- `(tenant_id, session_id)` — for resend flow's "find the live token for this session" lookup
- `(tenant_id, expires_at) WHERE used_at IS NULL AND superseded_at IS NULL` — reaping expired unused tokens (housekeeping)

**RLS:** canonical pair.

---

## API surface

### Recruiter-side (dashboard, authenticated via Supabase JWT)

Prefix: `/api/scheduler` for invite lifecycle; `/api/sessions` for read-side list/detail.

| Method | Path | Perm | Purpose |
|---|---|---|---|
| `POST` | `/api/scheduler/invites` | `candidates.manage` + `jobs.manage` | Body `{assignment_id, otp_required?}`. Server derives `stage_id` from `assignment.current_stage_id` — the invite always targets the candidate's current kanban position. Validates `stage.stage_type == 'ai_interview'` (422 `INVALID_STAGE_TYPE_FOR_INVITE`), creates session row + token row, dispatches email. Returns `{session_id, token_expires_at}`. |
| `POST` | `/api/scheduler/invites/{session_id}/resend` | `candidates.manage` + `jobs.manage` | Rejected with 409 `SESSION_ALREADY_STARTED` if state ∈ {`active`, `completed`, `cancelled`, `error`}. Otherwise: supersedes live token (atomic UPDATE on prior row setting `superseded_at = now(), superseded_by = <new jti>`), mints fresh token, resets OTP state (`otp_hash = NULL, otp_issued_at = NULL, otp_attempts = 0, otp_verified_at = NULL`), resends email. **Preserves** `consent_recorded_at` (AIVIA record is session-lifetime, not token-lifetime) and leaves `state` unchanged — a candidate who already consented does not re-consent after resend. |
| `POST` | `/api/scheduler/invites/{session_id}/revoke` | `candidates.manage` + `jobs.manage` | Marks session state → `cancelled`, supersedes token. |
| `GET` | `/api/sessions/{id}` | `jobs.view` on ancestry | Session detail. |
| `GET` | `/api/sessions` | `jobs.view` | Filters: `assignment_id`, `state`, date range. Pagination. |

### Candidate-side (token extracted from URL path by `AuthMiddleware`)

Prefix: `/api/candidate-session/{token}`.

| Method | Path | Pre-conditions | Behavior |
|---|---|---|---|
| `GET` | `/pre-check` | Token valid (sig + exp + not superseded) | Returns session context + `otp_required` + current `state` + `otp_verified_at`. Idempotent. Advances state `created → pre_check` on first call **only**; at any later state (`consented`, `active`, etc.) the state is not mutated — the call is a pure read. Token never marked used. |
| `POST` | `/consent` | state ∈ `{pre_check, consented}` | Body `{consented: true, user_agent}`. Stamps `consent_recorded_at`, sets state → `consented`. Idempotent — re-posting refreshes `user_agent` but not the timestamp. |
| `POST` | `/request-otp` | state ≥ `consented` AND `otp_required` AND rate-limit allows | Generates new 6-digit code, hashes it (see OTP section), stores. Email via notifications module. Rate limit: 1 request per 60 seconds per session (check `now() - otp_issued_at < 60s` on the session row itself — no separate store). Returns 204. |
| `POST` | `/verify-otp` | state ≥ `consented` AND `otp_required` AND `otp_hash IS NOT NULL` | Body `{code}`. On match: wipes `otp_hash`, stamps `otp_verified_at`, returns 204. On mismatch: increments `otp_attempts`, returns 422 `INVALID_OTP` with `{attempts_remaining}`. At 3 attempts wipes hash (forces re-issue) and returns 422 `OTP_MAX_ATTEMPTS_REACHED`. On expired (>10 min since `otp_issued_at`): wipes hash, returns 422 `OTP_EXPIRED`. |
| `POST` | `/start` | state == `consented` AND (if `otp_required`) `otp_verified_at IS NOT NULL` | Atomic single-use UPDATE (see JWT section). On first success: state → `active`, `started_at = now()`, returns **501** with `{code: "LIVEKIT_INTEGRATION_PENDING", detail: "..."}`. On replay: 409 `TOKEN_ALREADY_USED` + audit event. |

### Error codes (full list)

| Code | HTTP | Endpoint |
|---|---|---|
| `INVALID_STAGE_TYPE_FOR_INVITE` | 422 | `POST /api/scheduler/invites` (stage isn't `ai_interview`) |
| `ASSIGNMENT_NOT_ACTIVE` | 422 | `POST /api/scheduler/invites` (assignment in archived/rejected/hired/withdrawn) |
| `SESSION_ALREADY_STARTED` | 409 | `POST /api/scheduler/invites/{id}/resend` after the candidate has hit `/start` |
| `INVALID_SESSION_STATE` | 409 | Any candidate-side endpoint called out of order |
| `OTP_REQUIRED` | 422 | `POST /start` when `otp_required=true` but not verified |
| `OTP_RATE_LIMITED` | 429 | `POST /request-otp` within 60 s of last issuance |
| `OTP_EXPIRED` | 422 | `POST /verify-otp` when `>10min` since `otp_issued_at` |
| `OTP_MAX_ATTEMPTS_REACHED` | 422 | `POST /verify-otp` on the 3rd miss |
| `INVALID_OTP` | 422 | `POST /verify-otp` on miss (attempts remaining > 0) |
| `LIVEKIT_INTEGRATION_PENDING` | 501 | `POST /start` first success (Phase 3C sentinel) |
| `TOKEN_ALREADY_USED` | 409 | `POST /start` on replay |
| `TOKEN_EXPIRED` | 401 | Any candidate endpoint after JWT `exp` |
| `TOKEN_SUPERSEDED` | 401 | Candidate uses a token invalidated by resend/revoke |

---

## Authz

### No new permission constants

- Recruiter-side endpoints reuse `candidates.manage` + `jobs.manage` (both introduced in Phase 3B / earlier).
- Candidate-side endpoints authenticate via the candidate JWT — no Supabase user, no role assignments, no permission grid.

### Stage-type guard on invite dispatch

`POST /api/scheduler/invites` validates `assignment.current_stage.stage_type == 'ai_interview'`. Other stage types (manual_review, bulk_screening, …) return 422. The rationale: 3C ships only the AI interview flow; when a human-panel stage type ships in a later phase, it gets its own invite dispatch path.

### Assignment status guard

Dispatch rejects if `assignment.status != 'active'` with 422 `ASSIGNMENT_NOT_ACTIVE`. Archived/rejected candidates shouldn't receive invites.

### Stage snapshot at dispatch time

The session's `stage_id` is a *snapshot* of `assignment.current_stage_id` at dispatch time. If the recruiter later drags the candidate to a different stage on the kanban, the already-dispatched invite still targets the original stage — this is intentional. The candidate interviews for the stage they were invited to. If the recruiter wants the invite to reflect the new stage, they revoke and re-dispatch.

### Rate-limiting scope

`POST /request-otp` rate limit is per `session_id`, not per candidate or tenant. The check is implemented directly against `sessions.otp_issued_at`: if the column is non-null and `now() - otp_issued_at < 60 seconds`, reject with 429 `OTP_RATE_LIMITED`. Simpler than introducing a new store or indexed audit_log query; acceptable given OTP-issue volume is tiny and one-per-minute is the only limit we actually need.

### Audit trail additions

| Action | Resource | Payload |
|---|---|---|
| `session.invite_sent` | `session` | `{assignment_id, stage_id, otp_required, token_jti, recipient_email}` |
| `session.invite_revoked` | `session` | `{revoked_token_jti}` |
| `session.invite_resent` | `session` | `{prior_token_jti, new_token_jti}` |
| `session.pre_check_loaded` | `session` | `{}` (first load only — idempotent calls don't spam) |
| `session.consent_recorded` | `session` | `{user_agent, ip}` |
| `session.otp_issued` | `session` | `{}` (no PII; used for rate-limit counting) |
| `session.otp_verified` | `session` | `{attempts_consumed}` |
| `session.otp_verification_failed` | `session` | `{reason: invalid|expired|max_attempts, attempts_consumed}` |
| `session.token_used` | `session` | `{jti, ip}` |
| `session.token_replay_blocked` | `session` | `{jti, ip, ua}` |

---

## Email template

`backend/nexus/app/modules/notifications/templates/interview_invite.html` — Jinja2 template rendered by the existing `notifications.service.render_template()` helper. Variables:

- `candidate_name` — from `candidates.name` (fallback `"there"`)
- `company_name` — resolved from the JD's org unit ancestry company profile
- `job_title`
- `stage_name`
- `duration_minutes` — from `job_pipeline_stages.duration_minutes`
- `invite_url` — `{frontend_base_url}/interview/{token}`
- `expires_at_pretty` — human-readable "expires in 72 hours" style

The OTP code itself is **not** in this template. OTP arrives in a separate email rendered from `otp_code.html` (second Jinja template). This matches the "[Send code] button" flow.

### `otp_code.html`

Minimal — just the 6-digit code, a 10-minute expiry notice, and a "you didn't request this?" footer.

---

## Frontend structure (3C.2)

### New route group: `(interview)`

```
frontend/app/app/(interview)/
├── layout.tsx                        ← full-viewport, no sidebar, no Supabase session read
└── [token]/
    ├── page.tsx                      ← server component: fetches /pre-check, routes to current step
    ├── WizardShell.tsx               ← client: step progress indicator + header
    ├── ConsentStep.tsx               ← step 1: proctoring text + checkbox + Continue
    ├── OtpStep.tsx                   ← step 2 (conditional): [Send code] + input + Verify; cooldown timer
    ├── CameraMicStep.tsx             ← step 3: getUserMedia + live preview + test phrase
    ├── StartStep.tsx                 ← step 4: [Start Interview] → handles 501 (and 409 on replay)
    └── error/
        └── page.tsx                  ← renders TOKEN_EXPIRED / TOKEN_SUPERSEDED / TOKEN_ALREADY_USED landing
```

The server-side `page.tsx` calls `GET /pre-check` to determine the resume step:

| Server state | Wizard step |
|---|---|
| `created` or `pre_check` | ConsentStep |
| `consented` AND `otp_required` AND `otp_verified_at IS NULL` | OtpStep |
| `consented` AND (not otp_required OR otp_verified) | CameraMicStep (local-only gate — Start is the real commit point) |
| `active` | Redirect to `/interview/{token}/active` (Phase 3D) — for 3C, render a "session already started" panel |
| `cancelled` / `error` | Redirect to error page |

### API namespace + hooks

```
frontend/app/lib/api/
├── candidate-session.ts              ← token-scoped (no Supabase bearer)
└── scheduler.ts                       ← Supabase-bearer (dashboard)

frontend/app/lib/hooks/
├── use-candidate-session.ts          ← GET /pre-check
├── use-consent.ts                    ← POST /consent
├── use-request-otp.ts                ← POST /request-otp
├── use-verify-otp.ts                 ← POST /verify-otp
├── use-start-session.ts              ← POST /start
├── use-send-invite.ts                ← POST /api/scheduler/invites
├── use-revoke-invite.ts
├── use-resend-invite.ts
└── use-assignment-sessions.ts        ← GET /api/sessions?assignment_id=
```

### Additive dashboard surface

- **Kanban cards** (`components/dashboard/candidates/SessionStatusBadge.tsx`) — drop the "Not invited" hardcoded default; render actual state from the kanban card's `latest_session_state`. The backend `KanbanCandidateCard.latest_session_state` field was added in 3B but left null; 3C populates it via a subquery in `get_kanban_board`.
- **Candidate detail Sessions tab** (`CandidateSessionsTab.tsx`) — replace empty-state with a table of sessions per assignment, using `useAssignmentSessions()` per visible assignment.
- **Kanban cards / assignment rows** — "Send Invite" action opens `SendInviteDialog` component (stage-type check + OTP toggle + Send).

### Wizard resume semantics

The wizard is deeply reloadable. Refreshing mid-OTP sends the user back to the OTP step because `otp_verified_at IS NULL` server-side. Refreshing mid-Cam/Mic sends them back to CameraMicStep (local state is lost but they've already passed the server gate). This is deliberate — the server is the single source of truth for "can this candidate proceed".

---

## Testing strategy

### Backend unit + integration tests

Mirror the Phase 3B structure. New files:

| File | Covers |
|---|---|
| `tests/test_migration_0014.py` | column presence + index shapes + RLS policies (4 new: 2 each per table) + `_TENANT_SCOPED_TABLES` extension |
| `tests/test_scheduler_service.py` | invite creation atomicity, stage-type guard, resend supersedes, revoke cancels |
| `tests/test_session_service.py` | state transitions (happy path + illegal combos), atomic single-use race (parallel UPDATE: exactly 1 wins), OTP request rate limit, OTP verify paths (valid/invalid/expired/max-attempts), consent idempotency |
| `tests/test_candidate_jwt.py` | `create_candidate_token` signs correctly, `verify_candidate_token` accepts fresh + rejects expired/superseded |
| `tests/test_scheduler_router.py` | HTTP contracts for the 3 scheduler endpoints + 2 session read endpoints |
| `tests/test_session_router.py` | HTTP contracts for the 5 candidate-facing endpoints — including 501 sentinel on `/start` first call and 409 on replay |
| `tests/test_session_integration.py` | full flow: invite → pre-check GET → consent → request-otp → verify-otp → start (501) → replay (409). Audit log row count asserted. |
| `tests/test_middleware_candidate_single_use.py` | atomic UPDATE verified under concurrent asyncio gather |

### Frontend tests (Vitest + Testing Library)

- `WizardShell` renders the correct step based on injected pre-check state
- `OtpStep` cooldown timer + attempts_remaining error display
- `CameraMicStep` stub-mocks getUserMedia and verifies error surfacing
- `SendInviteDialog` form validation + stage-type warning

### Manual demo checklist (Task 25 of 3C.2 plan)

1. Create candidate, assign to JD with an `ai_interview` stage that has `otp_required_default=true`
2. Hit "Send Invite" — verify email in Resend dry-run log, verify session + token rows in DB
3. Click link — pre-check wizard opens on Consent step
4. Consent → OTP step appears (because `otp_required=true`)
5. `[Send code]` — second email arrives with 6-digit code
6. Enter wrong code 3×, see `OTP_MAX_ATTEMPTS_REACHED`, `[Send code]` again
7. Enter correct code → advances to Cam/Mic
8. Complete cam/mic test → Start button enabled
9. Start → see "Integration coming soon" panel (501 sentinel)
10. Refresh link — "Session already started" panel (409 replay blocked)
11. Revoke from dashboard → state = cancelled, link redirects to error page
12. Audit log has all expected rows

---

## Known gaps / deferred

**In 3C.1:**

- LiveKit wiring itself (room creation, participant token minting, egress config) — Phase 3D
- SMS delivery of invites or OTP — notifications-module config swap, deferred until a tenant needs it
- Calendar `.ics` invites — deferred; requires timezone awareness to be meaningful
- Candidate timezone handling — deferred until scheduling UX lands
- Rescheduling UX — recruiter can revoke + resend, but no "move to Tuesday 3pm" — deferred
- Multi-party scheduling (live panel interviews) — deferred
- Observer joining — Phase 3E (AI Copilot panel)

**In 3C.2:**

- Recording playback UI — deferred to 3D
- Post-session report page — Phase 3F
- i18n of candidate-facing wizard — deferred
- Offline-fallback pre-check — deferred

**Pre-Phase-3 TODO landing in 3C.1:**

- Candidate-JWT single-use enforcement — the `# TODO` in `middleware/auth.py` resolved by the atomic `UPDATE … WHERE used_at IS NULL RETURNING` in session/service.py.

---

## Risks

| Risk | Mitigation |
|---|---|
| OTP email deliverability — candidate stuck waiting for the code | 10-min expiry + easy re-request + dry-run provider for local dev. Resend retries handled by Resend itself. |
| Concurrent `/start` on the same token (browser double-click, eager reload) | Atomic single-use UPDATE with `WHERE used_at IS NULL` — exactly one succeeds at the DB level. |
| Hash cost blocking the event loop during OTP verify | SHA-256 HMAC is microseconds — no wrapping needed. If bcrypt is chosen instead, wrap in `asyncio.to_thread` and tune cost factor to 10-12 (sub-100 ms per check). |
| Candidate abandons mid-wizard, token expires, resend path mints a new token — old token kept usable? | Resend sets `superseded_at = now()` on the prior token atomically. Prior token fails the `superseded_at IS NULL` guard in the single-use UPDATE. |
| `sessions` stub data loss during migration 0014 | Migration explicitly truncates stub rows; stub is confirmed empty in non-local envs. Local-dev truncation is acceptable and documented. |
| Rate-limit bypass via new session creation | `request-otp` rate-limit is per-session, not per-tenant. A recruiter would need to dispatch multiple invites to "reset" OTP attempts — audit trail makes this visible. Acceptable for 3C. |
| Clock skew between app and DB affecting OTP expiry check | Expiry checked in SQL (`now() - otp_issued_at`), not in Python. Both compared against the DB clock. |
| GDPR redaction while invite is live | Redact endpoint already checks for active sessions (3B's `CandidateHasActiveSessionError`). Extends naturally to 3C sessions in `active` state. |

---

## Edge cases

| Scenario | Behavior |
|---|---|
| Candidate clicks an already-revoked link | Middleware rejects with 401 `TOKEN_SUPERSEDED`. Error page renders a "this invite has been cancelled" panel with support-contact copy. |
| Candidate clicks a replaced link (recruiter resent) | Same 401 `TOKEN_SUPERSEDED` — the prior token's `superseded_at` is set. Candidate is told to use the latest email. |
| Candidate's browser lacks camera/mic (mobile browser, permission denied, headless) | `CameraMicStep` detects the `getUserMedia` rejection and renders a blocking error with retry. Start button stays disabled. No server state change — the test is purely client-side. |
| Candidate hits `/start` successfully, then reloads the invite link | `/pre-check` returns `state = active`. Wizard renders a "session already started" panel with guidance for Phase 3D rejoin (not a redirect yet). |
| Email client strips or re-wraps the invite URL | Token lives in the URL path, not the query string, so standard email URL rewriters (Outlook SafeLinks, Gmail AMP) wrap the whole link rather than corrupting it. Tested path only — no server-side mitigation needed. |
| Recruiter tries to resend a session in `active`/`completed`/`cancelled`/`error` | 409 `SESSION_ALREADY_STARTED`. Recruiter's only path is a fresh invite off a new assignment (or after the candidate is moved back to the stage — 3B drag-drop path). |
| Candidate refreshes during the 10-minute OTP window after exhausting attempts | `otp_hash` is NULL (wiped at attempt 3). Wizard shows "Request a new code" with `[Send code]` primary CTA. |
| Two candidates received the same invite (forwarded email) | Single-use on `/start` guarantees only the first click succeeds. The second gets 409 `TOKEN_ALREADY_USED`. OTP adds further friction: a forwarded email contains the link but not the on-demand OTP (delivered to the original inbox only). |

---

## Open questions

None as of 2026-04-20. The following are explicitly resolved from brainstorming:

- OTP channel — email only (Resend, existing notifications module)
- OTP delivery trigger — candidate clicks `[Send code]` button on OTP wizard step
- OTP policy — per-stage default (`job_pipeline_stages.otp_required_default`) + per-invite override
- OTP attempts — 3 max per code; on max, force re-issue via new `[Send code]` click
- OTP expiry — 10 minutes from issuance
- Wizard shape — stepped (Consent → OTP → Cam/Mic → Start) rather than single-page stacked sections
- OTP hash wiped on verify success — zero replay window
- Phase split — 3C.1 backend + 3C.2 frontend, back-to-back

---

## Cross-references

- `docs/superpowers/specs/2026-04-19-candidates-scheduler-design.md` — original joint 3B+3C spec; this supersedes its 3C section
- `backend/nexus/CLAUDE.md` — RLS canonical pattern, candidate JWT rules, single-use TODO
- `frontend/app/CLAUDE.md` — frontend tech stack, component placement, query-key discipline
- `backend/nexus/migrations/versions/0013_candidates_core.py` — 3B migration; 0014 chains onto this
- `backend/nexus/app/modules/jd/authz.py` — ancestry-walking authz pattern reused for session detail endpoints
- `backend/nexus/app/modules/notifications/` — provider-agnostic email dispatch (reused by scheduler)
- `backend/nexus/app/modules/auth/service.py` — existing `verify_candidate_token`; `create_candidate_token` lands here
- `backend/nexus/app/middleware/auth.py` — existing candidate JWT extraction; single-use atomic check added here
