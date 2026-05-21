# OTP-Required toggle on the Bot stage — design

**Date:** 2026-05-21
**Status:** Approved (design)
**Surfaces:** `frontend/app` (recruiter dashboard), `backend/nexus` (pipelines + scheduler)

## Problem

Recruiters configuring an AI-Screening ("Bot") stage from the tracker board
(`/tracker/[jobId]`) have an **Auto-invite** toggle in the kanban column header,
but no way to control whether the candidate must pass an **OTP** gate before the
session starts. Today the auto-invite drag-drop path and the card "Resend invite"
action both hardcode `otp_required: true`, so OTP is always on and not
configurable from the board.

We want a parallel **OTP Required** toggle next to Auto-invite. Because OTP is a
security/compliance control (not just a UX convenience like Auto-invite, which is
browser-local), the toggle must be **backend-persisted** so it is durable and
shared across recruiters/browsers.

## Current state (verified)

- `job_pipeline_stages.otp_required_default BOOLEAN NOT NULL DEFAULT false` exists
  (migration `0014`). ORM: `JobPipelineStage.otp_required_default`
  (`app/modules/pipelines/models.py:156`).
- `sessions.otp_required` is the per-session copy, set once at invite time
  (`app/modules/session/models.py`).
- `scheduler.send_invite` already resolves OTP correctly:
  `request.otp_required if not None else stage.otp_required_default`
  (`app/modules/scheduler/service.py:74-77`).
- The OTP field-rules matrix (`app/modules/pipelines/schemas.py:134-171`) makes
  `otp_required` **OPTIONAL** for `phone_screen` / `ai_screening` /
  `human_interview` and **FORBIDDEN** for `intake` / `debrief` / `take_home`.
- **Gap 1 (read path):** `_stage_row_to_response`
  (`app/modules/pipelines/router.py:116-141`) never maps `otp_required_default`
  → the `otp_required` response field, so the pipeline GET always returns
  `otp_required: null`. The frontend toggle has no real initial-state source.
- **Gap 2 (no write path from the board):** the only writer is the bulk
  `PATCH /api/jobs/{id}/pipeline`, which does **not** persist
  `otp_required_default` (`service.py:750-762` + `_stage_input_to_row_dict`
  `:138-167`) **and** bumps `pipeline_version` + marks question banks stale
  (`service.py:819, 823`). Unsuitable for flipping one boolean.
- Frontend toggle precedent: **Auto-invite** is localStorage-only
  (`components/dashboard/tracker/auto-invite-storage.ts`), rendered in
  `CandidateKanbanColumn` for `ai_screening` only.
- Endpoint precedent: `POST /api/jobs/{jobId}/pipeline/stages/{stageId}/pause`
  + `/unpause` (`router.py`, `pipelinesApi.pauseStage`/`unpauseStage`) — dedicated
  per-stage endpoints returning the full `JobPipelineInstanceResponse`.
- The candidate flow already supports `otp_required=false` end-to-end:
  `start_session` only gates when `sess.otp_required and otp_verified_at is None`
  (`session/service.py`); the candidate-session frontend reads `otp_required` from
  pre-check. AIVIA consent is independent of OTP — disabling OTP does not affect
  consent/compliance.

## Decision

Persist the toggle to `job_pipeline_stages.otp_required_default` via a **dedicated
per-stage endpoint** (mirroring `pause`/`unpause`), NOT the bulk pipeline PATCH —
so toggling OTP never bumps `pipeline_version` or invalidates question banks.
The send paths stop hardcoding `otp_required` and let `send_invite` resolve from
the persisted stage default, making the DB column the single source of truth.

## Changes

### Backend (`backend/nexus`)

1. **Read-path fix** — `_stage_row_to_response` (`pipelines/router.py:123`): add
   `otp_required=getattr(row, "otp_required_default", None)`. `getattr` keeps
   `PipelineTemplateStage` rows (no such column) safe, matching the existing
   `paused_at=getattr(row, "paused_at", None)` pattern.

2. **Service writer** — new `set_stage_otp_required(db, *, instance, stage_id,
   otp_required, actor_id)` in `pipelines/service.py` (mirrors `pause_stage`):
   - Load the `JobPipelineStage` by id within `instance`; 404 if not found.
   - Guard `stage.stage_type ∈ {phone_screen, ai_screening, human_interview}`;
     otherwise raise a typed error → 422 (OTP is FORBIDDEN for the other types).
   - Set `existing.otp_required_default = otp_required`; `await db.flush()`.
   - Does **not** call `bump_pipeline_version` and does **not** recompute bank
     staleness.
   - `logger.info("pipelines.stage_otp_required_set", ...)`.

3. **New endpoint** — `PATCH /api/jobs/{jobId}/pipeline/stages/{stageId}/otp-required`
   in `pipelines/router.py`:
   - Body: `StageOtpRequiredRequest { otp_required: bool }`
     (`extra="forbid"`), defined in `pipelines/schemas.py`.
   - `require_instance_access(db, job_id, user, "manage")`; 404 if no instance.
   - Call `set_stage_otp_required(...)`, reload via `get_job_pipeline_with_stages`,
     return `JobPipelineInstanceResponse` (same shape as `pause`/`unpause`).
   - Audit: `log_event(action="session.stage_otp_required_set", resource="pipeline_stage",
     resource_id=stage_id, payload={otp_required, job_id})` — OTP is security-relevant.

### Frontend (`frontend/app`)

4. **API client** — `pipelinesApi.setStageOtpRequired(token, jobId, stageId, otpRequired)`
   in `lib/api/pipelines.ts`, mirroring `pauseStage`/`unpauseStage`
   (`apiFetch<JobPipelineInstance>`, `method: 'PATCH'`).

5. **Mutation hook** — `use-set-stage-otp.ts` in `lib/hooks/`, mirroring the
   pause/unpause hook: on success `qc.setQueryData(['job-pipeline', jobId], instance)`
   (or invalidate); surface errors via toast.

6. **Toggle UI** — `OtpRequiredToggle` in `CandidateKanbanColumn.tsx`, rendered
   next to `AutoInviteToggle`, `ai_screening` only. Initial `checked` from pipeline
   data (new `otpRequired` prop threaded from `CandidateKanbanView` exactly like
   `stageType`). Optimistic local state (mirrors `AutoInviteToggle`'s
   `useState` + `useEffect`-sync), fires the mutation on change, reverts + toasts
   on error. Label: "OTP", with a `title` explaining it.

7. **Stop hardcoding OTP on send paths:**
   - `CandidateKanbanView.tsx:63-66` (auto-invite): drop `otp_required: true` from
     the `sendInvite` body → backend resolves from the stage default. Toast reads
     the target stage's `otp_required` from pipeline data
     ("Invite sent — OTP required" / "Invite sent").
   - `CandidateKanbanCard.tsx:166` (kebab "Resend invite (with OTP)"): drop
     `otp_required: true`; relabel to "Resend invite". (This action calls
     `POST /api/scheduler/invites` = a fresh `send_invite`, so it inherits the
     stage default too.)

### Out of scope (follow-ups)

- Wiring the `StageConfigDrawer` Phase-3 OTP placeholder (separate surface).
- Fixing the bulk `PATCH /pipeline` persistence drop for `otp_required_default`
  (needs null-coalescing semantics + the drawer UI). The dedicated endpoint leaves
  the bulk path untouched; because the update loop never assigns
  `otp_required_default`, a subsequent bulk edit preserves the toggled value.

## Tests

**Backend** (mirror `test_pipelines_pause.py`, `test_scheduler_service.py`,
`test_pipelines_router.py`):
- New endpoint sets `otp_required_default` and returns it in the response.
- New endpoint rejects `intake`/`debrief`/`take_home` with 422.
- New endpoint does **not** bump `pipeline_version` and does **not** mark banks stale.
- Pipeline GET response now includes `otp_required` for an instance stage.
- `send_invite` inherits `stage.otp_required_default` when `otp_required` is omitted
  (true and false cases).

**Frontend** (composition style, mock at the API boundary; mirror
`SendInviteDialog.test.tsx`):
- OTP toggle renders for `ai_screening`, reflects pipeline `otp_required`, and
  flipping it calls `setStageOtpRequired`.
- Auto-invite drag path sends an invite body **without** `otp_required: true`.

## Security / compliance notes

- New endpoint is `manage`-gated via `require_instance_access`, tenant-isolated by
  RLS like all pipeline routes; audited.
- No PII in logs (job/stage ids + boolean only).
- Disabling OTP removes only the OTP gate; AIVIA consent and single-use token
  enforcement are unaffected.
