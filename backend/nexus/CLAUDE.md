# ProjectX — Backend (Nexus)
## Claude Code Context (Backend)

> Read the root `CLAUDE.md` first. This file contains backend-specific rules that extend it.

@AGENTS.md
---

## What Nexus Is

**Nexus** is the FastAPI backend — a single deployable Docker container with clean internal module boundaries. It is a **modular monolith**, not microservices. No module is extracted into a standalone service unless it demonstrably requires independent scaling and a real client requirement triggers it.

- **Language:** Python 3.13
- **Framework:** FastAPI (async throughout)
- **DB driver:** asyncpg (direct PostgreSQL connection — NOT PostgREST)
- **ORM:** SQLAlchemy async (asyncpg driver)
- **Schema management:** Supabase SQL for the initial cut + Supabase-managed objects (auth hook, `supabase_auth_admin` grants); Alembic for every incremental change after that. `migrations/versions/` head is `0059_drop_knockout` (recent: `0048` drops the engine-selection flag → `0049` engine heartbeat → `0050` session recording columns → `0051` proctoring analysis → `0052` timeline thumbnails → `0053` session reels → … → `0059` drops the knockout columns).
- **Task queue:** Dramatiq + Redis. Broker setup is shared via `app/brokers.py`. Queues today: default (JD extraction/re-enrichment, question-bank generation), `report_scoring` (post-session report), `ats_poll` (manual ATS sync), `reel` + `vision` (run in the dedicated `nexus-vision-worker`, NOT the lean `nexus-worker`). Notification dispatch still runs via FastAPI `BackgroundTasks` (short, non-retryable).
- **Containerisation:** Docker + Docker Compose
- **Hosting MVP:** Railway
- **Hosting Enterprise:** AWS ECS Fargate (same Docker image, different target)
- **Real-time (LiveKit):** **self-hosted** SFU + Egress (replaced LiveKit Cloud 2026-06-09). The nexus app planes (API/engine/workers) stay on Railway/Fargate; the LiveKit SFU + Egress run on EC2/EKS (SFU needs host networking + UDP 50000–60000; Egress needs `--cap-add=SYS_ADMIN`). The engine agent connects **outbound** to the SFU, so it stays on Fargate. Cloud kept as a one-env-var fallback. See "Self-hosted LiveKit" below + `docs/deployment/2026-06-09-self-hosted-livekit-deployment.md`.

---

## Current State

- **Phase 1** — done: auth (Supabase + provider-agnostic interface), multi-tenancy with RLS, client provisioning, team invites, org units (hierarchical tree with typed nesting rules), roles & permissions, audit log, notification abstraction (Resend + dry-run).
- **Phase 2A** — done: JD pipeline (create → extract → confirm → re-enrich), signal schema v2 with provenance, Dramatiq worker, `app/ai/` provider-agnostic AI layer, OpenAI instructor + OpenTelemetry tracing (Langfuse was removed 2026-05-01 — OTel only).
- **Phase 2B** — done: signal editing with snapshot versioning + row-locked version conflict detection, company profile ancestry walk.
- **Phase 2C.1** — done: pipeline builder (templates + per-job instances + stages), drag-to-reorder, tenant-scoped template library, starter pipelines. Stage type collapsed to v5 (6 values) in migration 0016. Pipeline versioning + stage pause + stale-bank tracking added in 0018.
- **Phase 2C.2** — done: question bank generation (adaptive coverage, mandatory demotion, per-stage LLM call, bundling discipline, SSE progress stream).
- **Phase 3B** — done: candidates module (`candidates`, `candidate_job_assignments`, `candidate_stage_progress` tables; resume upload + S3; PII redaction gate; kanban board; created in migration 0013).
- **Phase 3C.1** — done: scheduler invite/resend/revoke flow; candidate JWT mint + supersession chain; OTP code (CSPRNG + HMAC-SHA256 hash + constant-time verify); session pre-check / consent / OTP / start endpoints; **single-use token enforcement** via atomic `UPDATE … WHERE used_at IS NULL RETURNING` (`session/service.py:412–426`).
- **Phase 3C.2** — done: `/start` provisions a LiveKit room + mints candidate access token + dispatches the engine agent worker (`session/livekit.py`). The `interview_runtime` module exposes `build_session_config` + `record_session_result` — both called in-process by the engine entrypoint (single-image compose service from the same codebase). Defense layer is RLS-only: every query in `interview_runtime.service` filters by an explicit `tenant_id` argument under a bypass-RLS session.
- **Phase 3D.engine — the interview engine (gen-3 / "Path A+").** `app/modules/interview_engine/` is the sole interview engine: a **two-LLM-tier conversation/control split** running under LiveKit **native turn detection** — no LLM owns turn-taking (that was the gen-2 mistake). `agent.py` is the LiveKit entrypoint (+ `server`/`prewarm`/`entrypoint` worker bootstrap) and the ONLY livekit-importing module. The `AgentSession` runs the `MultilingualModel` turn detector + dynamic endpointing; `on_user_turn_completed` submits the full final transcript to a per-session `CommittedTurnSource` and raises `StopResponse` (the engine drives ALL speech). **Read `interview_engine/AGENT_ARCHITECTURE.md` for the deep dive.**
  - **mouth.bridge** (`mouth/bridge.py`, `gpt-5.4-mini`, ~100–300ms) — an immediate, intent-aware spoken beat fired the instant a turn commits, IN PARALLEL with the brain (masks brain latency). Sees only the candidate's words; never the rubric.
  - **brain** (`brain/`, `gpt-5.4-mini`, ~2–3s, async/parallel, never on the path to first audio) — the control plane (`brain/service.py` = `ControlPlane`): grades the answer vs the active question's rubric, tracks signal `coverage` (`brain/input_builder.py` `CoverageProjection`), runs deterministic policy gates (`brain/policy.py`: no-leak `scrub_composed_say`, `coerce_probe_dimension` — fire-once per dimension slug + hard per-thread probe cap), picks the next bank question via the deterministic `brain/resolver.py`, and emits exactly one `Directive` from a closed move set (ask / probe / clarify / confirm / redirect / reassure / hold / answer_meta / repeat / close).
  - **mouth.real_line** (`mouth/service.py` = `ConversationPlane`, persona "Arjun") — renders the `Directive` as natural spoken Indian English, continuing from the bridge. NEVER sees the rubric (no-leak by construction); candidate speech is fenced as DATA (identity lock).
  - **Turn assembly (livekit-free):** `turn_assembler.py` (`TurnAssembler`) merges fragmented committed turns into ONE logical answer BEFORE the brain runs — committed fragments are buffered and flushed as one `AssembledTurn` once VAD `user_state_changed` + a short grace timer show the candidate has settled (so a paused answer's opening fragment is never processed alone). A continuation arriving just after a flush is merged back via a single deterministic checkpoint at the note-commit point-of-no-return in `loop.py` (no preemption, no supersession of committed evidence). Knobs: `engine_assembly_enabled/grace_s/max_duration_s` (config.py). Spec: `docs/superpowers/specs/2026-06-10-turn-assembly-design.md`.
  - **Orchestration core (livekit-free, unit-testable):** `driver.py` (`SessionDriver.handle_turn(turn=AssembledTurn)` — one logical turn → `loop.run_turn` → state update + `NoteLog`; `build_session_driver` factory), `loop.py` (`run_turn`: bridge ∥ brain → mouth real line + the `ABORTED` merge-back checkpoint), `contracts.py` (`Directive`/`BrainDecision`/`BridgeRequest`), `turn_source.py` (`CommittedTurnSource` + `AssembledTurn`), `turn_taking.py` (`is_backchannel` allowlist), `notes.py` (append-only `NoteLog` + `compute_provenance`).
  - **Durable output:** an append-only **`SessionEvidence`** (per-signal notes with stance/texture/verbatim-quote/provenance + `QuestionRecord` closures + transcript), persisted via `record_session_evidence` → `sessions.session_evidence_json`. The reporting module consumes THIS. (The legacy `coverage_summary` / `record_session_result` path is superseded.)
  - **Turn-handling config is consolidated in `config.py` → "Interview engine — turn handling" (SINGLE SOURCE OF TRUTH):** `engine_turn_detector_unlikely_threshold` (default `None` = model per-language tuned default), `engine_endpointing_mode` (`dynamic`), `engine_endpointing_min_delay_s` (`1.5`), `engine_endpointing_max_delay_s` (`4.0`). Verified semantics (livekit-agents 1.5.7 `voice/audio_recognition.py`): `delay = min_delay; if eou_prob < unlikely_threshold: delay = max_delay` — so a LOW threshold DISABLES patience (the prior `0.011` override fragmented answers; see `interview_engine/AGENT_ARCHITECTURE.md` + the 2026-06-10 turn-handling cleanup). The dead gen-2 `engine_v2_*` EOU/hold-space/unresponsive-ladder/backchannel knobs were removed 2026-06-10.
  - **Prompts** live under `prompts/v4/engine/` (`brain.system.txt`, `mouth/*` per-act incl. `bridge.txt`/`intro.txt`), read fresh per-session by `PromptLoader`. **Real-API prompt-evals** opt-in via `pytest -m prompt_quality` — validate prompt behavior against the real OpenAI endpoint, not just mocks.

  Schema surface consumed by the engine:
  - `stage_questions.question_kind` + `primary_signal` + per-question `difficulty` — the bank taxonomy, signal-coverage map, and grading strictness; the bank-generator emits them.
  - `tenant_settings.engine_agent_name` — read at session start (the mouth persona falls back to it; `engine_mouth_persona_name` default "Arjun").
- **Phase 3D.audio-pipeline (2026-05-06 / updated 2026-06-04)** — done. LK Cloud cutover; subsequently updated to self-hosted audio path (2026-06-04). Interruption is `mode="vad"` VAD-mode barge-in (no adaptive interruption); VAD is **Silero** (prewarm-loaded in agent.py; a top-level `# noqa: F401` registration import in `agent.py` bakes the model via Dockerfile `download-files`). **No server-side NC** (`build_noise_cancellation` removed); browser does light NS (`noiseSuppression: true`). `echoCancellation: true`, `autoGainControl: true` unchanged. New JSONB column `sessions.audio_tuning_summary` (migration `0028`) persists per-session pause/interruption/latency percentiles for empirical tuning. **`preemptive_generation={"enabled": False}`** in `TurnHandlingOptions` (`agent.py` — quality-before-latency lock; preemptive generation is intentionally OFF). Frontend reads server-authoritative audio constraints via the `audio_processing_hints` field on the `/start` response. Dev toggles: `AUTO_SCORE_SESSION_REPORTS` (default `true` — skip report scorer in dev) and `AUTO_ANALYZE_PROCTORING` (default `true` — skip vision analysis in dev). See spec `docs/superpowers/specs/2026-06-04-self-hosted-audio-turn-taking-design.md`.
- **Phase 3D.simplification (2026-05-12)** — done. Stripped the orchestrator's five layered race-condition mitigations (continuation coalescing, stale-turn drop-and-drain, post-Judge resumption gate, must-deliver whitelist, and supporting timestamp tracking). STT was Sarvam (`saaras:v3`) then; it has since moved to **Deepgram `nova-3` (`en-IN`) + per-session keyterm prompting** as the default (`interview_stt_provider="deepgram"`, migration `0041`), Sarvam kept as the alternate (Deepgram **Flux** is still ruled out — English-only). Turn detection stays the `MultilingualModel` (`unlikely_threshold` is `None` = model default, with `engine_endpointing_max_delay` raised to 10.0 for patient think-pauses). Hardened the Judge fallback path: the `push_back+concrete` cross-field check no longer raises (the State Engine's new `inverse_quality_gate` downgrades to `probe`), and `validation_error` synthesizes `clarify` instead of force-advancing the queue. Added `state.snapshot` and `speaker.input` audit events; populated `judge.call.input_summary` with the full `JudgeInputPayload` (was hardcoded empty). `engine_endpointing_max_delay` lowered 6.0s → 3.0s (LK default); VAD-mode barge-in `min_duration` raised 0.5s → 1.0s. Supersedes `docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md`. Spec: `docs/superpowers/specs/2026-05-12-engine-simplification-design.md`.
- **Phase 3D.analysis** — real-time per-turn grading / signal coverage / probe selection is done **in-session by the engine's brain** (it emits `SessionResult` + `coverage_summary` + the audit trail). The standalone `app/modules/analysis/` module is a **dead stub** (`GET /api/analysis/{id}/signals` → `not_implemented`) — its role was absorbed by the engine; it survives only as leftover scaffolding.
- **Phase 3D.reporting — done.** `app/modules/reporting/` compiles the post-session report: a 3-layer hybrid that consumes the engine's append-only **`SessionEvidence`** (`sessions.session_evidence_json`) — per-question grade vs the bank rubric (`scoring/question_grade.py`, prompts `prompts/v4/report_scorer/`) → roll-up to per-(primary-)signal → LLM narrative. Verdict-driven fit-score (Overall/Technical/Behavioral/Communication ints → tier → verdict; state×texture bluff-aware; fit-ceiling-capped + ±5 holistic; Borderline always human-held). `session_reports` table (migration `0047`, UNIQUE on `session_id`); Dramatiq actor `score_session_report` (queue `report_scoring`) enqueued when the engine persists `SessionEvidence` (`record_session_evidence`); `/api/reports/*` (hub index, report view, presigned recording playback, proctoring view, human-decision write). Prompts under `prompts/v4/report_scorer/`. Spec: `docs/superpowers/specs/2026-05-28-verdict-driven-fit-score-design.md`.
- **Phase 3D.recording — done.** Session recordings via **LiveKit Auto Egress → Cloudflare R2** (S3-compatible). `session/livekit.py::build_room_egress` (RoomComposite, `speaker` layout, `H264_720P_30`), pull-based reconcile via `get_room_egress_status` + `session/recording.py` reconcile hardening (R2 `head` fallback to `ready`; stuck-`recording` expiry past `recording_stuck_timeout_seconds`=900). Recording lifecycle columns in migration `0050`. Storage is vendor-blind through `app/storage/` (boto3 SigV4, path-style). Resumes stay on AWS S3 (`candidates/resume_service.py`); recordings/reels/thumbnails on R2. Egress is now the **self-hosted** OSS `livekit/egress` service (see "Self-hosted LiveKit" below) — but the app recording code is unchanged (auto-egress + per-request `S3Upload`; egress service carries no R2 creds, the backend supplies them per-request).
- **Self-hosted LiveKit (2026-06-09) — done.** The LiveKit **SFU** (`livekit/livekit-server`) and **Egress** (`livekit/egress`) are now self-hosted, replacing LiveKit Cloud, to permanently fix the egress transcode-minute quota and gain scaling/security control. A self-hosted Egress can't drive Cloud (Egress coordinates with the SFU over a **shared Redis** Cloud doesn't expose), so self-hosting egress forces self-hosting the SFU too. Both images pinned **`v1.13.x`** (aligned to candidate `livekit-client` 2.18.8 — older servers 404'd on `/rtc/v1/validate`). Local dev: `docker compose -f docker-compose.yml -f docker-compose.livekit.yml up -d` adds `livekit-server` + `livekit-egress` + dedicated `livekit-redis` (host networking; LiveKit's Redis on **6380** to avoid the Dramatiq broker on 6379); configs under `config/livekit/{livekit,egress}.yaml`. `LIVEKIT_URL=ws://host.docker.internal:7880` (engine, bridged), `LIVEKIT_PUBLIC_URL=ws://localhost:7880` (browser). **CSP gotcha:** the candidate app's `connect-src` must allow **both** the `ws(s)://` and `http(s)://` SFU origins (`NEXT_PUBLIC_LIVEKIT_WS_URL` in prod) — the `livekit-client` also does an HTTP `fetch` to the SFU (`/rtc/v1/validate` + `prepareConnection`); `ws://` alone leaves the room stuck on "Connection lost". Cloud kept as a one-env-var fallback. Spec: `docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md`; deploy doc: `docs/deployment/2026-06-09-self-hosted-livekit-deployment.md`.
- **Phase 3D.reel — done.** `app/modules/reel/` — Candidate Reel highlight video (see "Reel module" below).
- **Phase 3D.vision — POC.** `app/modules/vision/` — server-side gaze proctoring (see "Vision proctoring" below) + the client deterrent classified in `session/proctoring.py`.

Genuinely stubbed modules (routers registered, no business logic): `analysis` only. (`ats` and `reporting` are now implemented — see below.)

---

## Module Structure

```
backend/nexus/
├── app/
│   ├── main.py                  ← FastAPI app factory, middleware + router registration, _assert_rls_completeness startup check
│   ├── config.py                ← Settings via pydantic-settings (env vars only)
│   ├── database.py              ← SQLAlchemy async engine + Base; 3 session types (tenant/bypass/raw); SET LOCAL ROLE nexus_app on every request
│   │                              (ORM classes live in per-module models.py — see "Module public API" below; Base.registry.configure() runs at lifespan startup)
│   ├── worker.py                ← Dramatiq worker entrypoint — imports all actor modules to register with broker
│   ├── middleware/
│   │   ├── auth.py              ← JWT extraction via JWKS, attaches token_payload to request.state
│   │   └── tenant.py            ← Binds tenant_id to structlog context
│   ├── ai/                      ← Provider-agnostic AI layer (Phase 2A)
│   │   ├── config.py            ← AIConfig — env-driven model IDs and reasoning_effort
│   │   ├── client.py            ← get_openai_client() — instructor.AsyncInstructor + plain openai.AsyncOpenAI
│   │   ├── otel.py              ← TracerProvider bootstrap, OpenAI auto-instrumentor
│   │   ├── tracing.py           ← set_llm_span_attributes() — adds prompt metadata to active OTel span
│   │   ├── prompts.py           ← PromptLoader — versioned prompt file reader, in-memory cache
│   │   └── schemas.py           ← EnrichmentOutput, SignalExtractionOutput, ReEnrichmentOutput — structured output schemas with provenance validators
│   └── modules/
│       ├── auth/                ← JWT verification, UserContext, RBAC guards, /me endpoint, invite claiming
│       │   ├── service.py       ← verify_access_token(), verify_candidate_token(), require_projectx_admin()
│       │   ├── context.py       ← UserContext dataclass, get_current_user_roles(), require_super_admin()
│       │   ├── schemas.py       ← TokenPayload, MeResponse, CandidateTokenPayload
│       │   ├── permissions.py   ← 16 canonical permission constants (frozenset)
│       │   └── router.py        ← /api/auth/* (verify-invite, accept-invite, me, onboarding/complete)
│       ├── admin/               ← ProjectX internal ops: provision-client, list clients
│       │   ├── service.py       ← provision_client() — creates tenant + sends Company Admin invite
│       │   ├── schemas.py
│       │   └── router.py        ← /api/admin/* (requires is_projectx_admin)
│       ├── settings/            ← Team management: invite, resend, revoke, deactivate
│       │   ├── service.py       ← create_team_invite(), _delete_auth_user() via Supabase Admin API
│       │   ├── schemas.py
│       │   └── router.py        ← /api/settings/team/* (requires super_admin for writes)
│       ├── org_units/           ← Org unit CRUD, member/role assignment, company profile ancestry
│       │   ├── service.py       ← create/list/update/delete org units, assign/remove roles, company profile hooks
│       │   ├── schemas.py       ← CompanyProfile strict schema, org unit request/response models
│       │   └── router.py        ← /api/org-units/*
│       ├── roles/               ← Role listing (system + tenant custom)
│       │   ├── schemas.py
│       │   └── router.py        ← /api/roles
│       ├── notifications/       ← Provider-agnostic email dispatch (Resend or dry-run)
│       │   ├── service.py       ← send_email(), DryRunProvider, ResendProvider, render_template()
│       │   ├── schemas.py
│       │   ├── router.py
│       │   └── templates/       ← HTML email templates (company_admin_invite, team_invite)
│       ├── audit/               ← Audit log writer — tenant-scoped append-only trail (Batch A fixed its RLS INSERT gap in migration 0008)
│       ├── ats/                 ← [Stub] Per-ATS adapters, polling, outbound sync
│       ├── jd/                  ← JD pipeline (Phase 2A + 2B)
│       │   ├── router.py        ← /api/jd/* (create, get, list, stream status, re-enrich, confirm signals)
│       │   ├── service.py       ← create_job_posting, confirm_signals, save_signals (FOR UPDATE row lock + version conflict detection)
│       │   ├── schemas.py       ← JobPosting request/response models
│       │   ├── errors.py        ← IllegalTransitionError (409), CompanyProfileIncompleteError (422)
│       │   ├── state_machine.py ← JD state machine with audit trail
│       │   ├── authz.py         ← require_job_access() — ancestry-walking authorization
│       │   ├── actors.py        ← Dramatiq actors: extract_and_enhance_jd (Call 1), reenrich_jd (Call 2)
│       │   └── sse.py           ← SSE status stream — routes every poll through get_tenant_session (Batch F)
│       ├── pipelines/            ← Phase 2C.1 — template library, per-job instance, stage CRUD, drag-to-reorder
│       │   ├── router.py         ← /api/pipelines/* templates + /api/jobs/{id}/pipeline instance
│       │   ├── service.py        ← ensure_minimal_pipeline_for_job, PipelineAlreadyExistsError
│       │   ├── authz.py          ← require_template_access — ancestry-walking template authz
│       │   └── errors.py
│       ├── question_bank/        ← Phase 2C.2 — AI generation, adaptive coverage, mandatory demotion, bundling
│       │   ├── router.py         ← /api/jobs/{id}/banks/* — list (idempotent GET), generate, regenerate, update, stream
│       │   ├── service.py        ← get_banks_for_pipeline (bulk 4-query load), ensure_bank_exists
│       │   ├── refine.py         ← Per-question draft + refine LLM calls (uses get_openai_client)
│       │   ├── context.py        ← Context-stack builder for per-stage prompts
│       │   ├── authz.py          ← require_bank_access_by_stage, require_question_access
│       │   ├── state_machine.py  ← IllegalTransitionError, ReorderMismatchError
│       │   ├── actors.py         ← Dramatiq actors: generate_question_bank_stage (per-stage LLM call)
│       │   └── sse.py            ← SSE status stream — routes every poll through get_tenant_session (Batch F)
│       ├── candidates/           ← Phase 3B — candidate CRUD, resume upload, kanban board, assignments
│       │   ├── router.py         ← /api/candidates/* + /api/candidates/kanban
│       │   ├── service.py        ← create/get/list/update/redact_pii, kanban aggregation, stage transitions
│       │   ├── resume_service.py ← S3 upload + content extraction
│       │   ├── sources.py        ← Source typing (manual / ats / referral)
│       │   ├── authz.py          ← require_candidate_access (ancestry-walking)
│       │   ├── schemas.py        ← CandidateCreate / CandidateResponse / KanbanBoardResponse
│       │   └── errors.py         ← DuplicateEmailError, CandidateHasActiveSessionError
│       ├── scheduler/            ← Phase 3C.1 — invite send/resend/revoke, supersession chain
│       │   ├── router.py         ← /api/scheduler/*
│       │   ├── service.py        ← send_invite, resend_invite, revoke_invite (mints candidate JWT + token row)
│       │   ├── authz.py          ← require_scheduler_access
│       │   └── errors.py
│       ├── session/              ← Phase 3C — candidate session state machine + atomic single-use enforcement
│       │   ├── router.py         ← candidate_session_router (/api/candidate-session/{token}/*) + session_router (/api/sessions/*)
│       │   ├── service.py        ← pre_check, consent, request/verify OTP, start (LiveKit room + dispatch), supersession + replay gates
│       │   ├── livekit.py        ← Phase 3C.2 — mint_candidate_lk_token, mint_engine_dispatch_jwt, dispatch_agent, cancel_room
│       │   ├── state_machine.py  ← created → pre_check → consented → active → completed | cancelled | error
│       │   ├── otp.py            ← CSPRNG generate_code + HMAC-SHA256 hash + constant-time verify
│       │   ├── schemas.py
│       │   └── errors.py         ← TokenAlreadyUsedError, TokenSupersededError, OtpInvalidError, AgentDispatchFailedError
│       ├── interview_runtime/    ← Helpers the in-process engine calls (post-Phase-3 merge)
│       │   ├── service.py        ← build_session_config, record_session_result, record_engine_heartbeat (called by agent.py)
│       │   ├── schemas.py        ← SessionConfig, SessionResult, QuestionConfig, QuestionRubric, WordTiming (wire contract)
│       │   ├── transcript_timing.py ← question_asked_at_ms / relative_words / turn_bounds (shared by engine + reporting + reel/vision)
│       │   └── errors.py         ← CompanyProfileMissingError, EmptySignalMetadataError, QuestionBankNotReadyError, …
│       ├── interview_engine/     ← Phase 3D — the interview engine (gen-3, native turn detection): bridge ∥ brain/ → mouth/ ; agent.py (LiveKit entrypoint + worker bootstrap — only livekit-importing file), driver.py (SessionDriver orchestration core), loop.py (run_turn: bridge ∥ brain → mouth + ABORTED merge-back checkpoint), contracts.py (Directive/BrainDecision), turn_assembler.py (TurnAssembler — fragment assembly before the brain), turn_source.py (CommittedTurnSource + AssembledTurn), turn_taking.py (is_backchannel), notes.py (NoteLog + compute_provenance), brain/ (service=ControlPlane, input_builder=CoverageProjection, resolver, policy), mouth/ (service=ConversationPlane, bridge, persona); AGENT_ARCHITECTURE.md + README.md
│       ├── reporting/            ← Phase 3D — post-session report generator
│       │   ├── service.py        ← build_report (coverage projection → LLM recheck → LLM narrative)
│       │   ├── scoring/          ← signal scoring (state×texture), fit ceilings, holistic delta, verdict, recheck, judge, narrative, engine_signals
│       │   ├── actors.py         ← score_session_report (queue report_scoring)
│       │   └── models.py / schemas.py / router.py ← session_reports + /api/reports/* (hub, view, recording, proctoring, decision)
│       ├── reel/                 ← Phase 3D — Candidate Reel highlight video
│       │   ├── director.py       ← generate_edl (gpt-5.4) + validate_edl (pure, hallucination-guarded)
│       │   ├── timing.py         ← live-VAD → recording-ms map (per-session measured pipeline lag)
│       │   ├── render.py / clips.py / cards.py / captions.py / tts.py ← ffmpeg pipeline (runs in vision image)
│       │   ├── actors.py         ← generate_session_reel (queue reel)
│       │   └── service.py / router.py ← session_reels + /api/reports/session/{id}/reel*
│       ├── vision/               ← Phase 3D — server-plane gaze proctoring (POC; nexus-vision-worker only)
│       │   ├── gaze/mobilegaze.py ← ONNX gaze estimator (resnet34, NON-COMMERCIAL weights — GA blocker)
│       │   ├── actors.py         ← analyze_session_proctoring (queue vision)
│       │   └── …                 ← RetinaFace detect + frame sampling + risk band; persists FEATURES ONLY (session_proctoring_analysis)
│       ├── tenant_settings/      ← per-tenant engine + proctoring configuration
│       │   ├── models.py         ← TenantSettingsModel (PK = tenant_id, FK clients.id ON DELETE CASCADE)
│       │   ├── schemas.py        ← TenantSettings (engine_agent_name, proctoring_enabled, proctoring_soft_violation_limit, proctoring_fullscreen_grace_seconds)
│       │   └── service.py        ← get_tenant_settings — lazy-default read (no row → schema defaults)
│       ├── ats/                  ← Ceipal sync — vendor-blind orchestrator + CeipalAdapter + poll_ats_connection actor (manual-trigger)
│       └── analysis/             ← [Dead stub] role absorbed by interview_engine/brain — returns not_implemented
│   └── storage/                 ← app/storage/ — provider-agnostic object storage (S3CompatibleStorage: AWS S3 resumes + Cloudflare R2 recordings/reels/thumbnails)
├── worker.py + vision_worker.py + brokers.py  ← Dramatiq entrypoints (vision_worker runs `-Q vision reel`) + shared broker init
├── prompts/                     ← versioned prompt files (PromptLoader reads `v{N}/…`, in-memory cache)
│   ├── v1/                       ← JD pipeline prompts (jd_enrichment / jd_signal_extraction / jd_reenrichment)
│   ├── v2/                       ← question-bank prompts (ACTIVE — `question_bank_prompt_version="v2"`): question_bank_common + question_bank_<stage_type>
│   ├── v3/                       ← reel/ (director.txt) [+ legacy report_scorer]
│   └── v4/                       ← engine/ (brain.system, mouth/* per-act incl. bridge/intro) · report_scorer/ (question_grade, communication, holistic, narrative)
├── migrations/                  ← Alembic — head is `0059_drop_knockout`
│   └── versions/
├── tests/
│   └── conftest.py              ← AsyncClient fixture
├── Dockerfile
├── docker-compose.yml           ← nexus + nexus-worker + redis services
├── pyproject.toml
├── .env.example                 ← All required env vars documented
└── CLAUDE.md                    ← you are here
```

---

## Organizational Unit Types

Valid types (as of Phase 2): `company`, `division`, `client_account`, `region`, `team`

### Rules

**`company`**
- Auto-created during onboarding. Never manually created by recruiters.
- Non-deletable (`is_root = true`).
- Exactly one per tenant. `parent_unit_id` must be NULL.
- `unit_type` is immutable once set to `'company'`.
- `company_profile` (JSONB) is required — cannot be null.

**`client_account`**
- Available to every tenant. The previous `workspace_mode='agency'` gate was removed in migration 0020 — every tenant can model itself as a staffing agency, an in-house employer, or a hybrid.
- `company_profile` (JSONB) is required — cannot be null.
- Cannot be nested under another `client_account` or under a `team`.
- Can be nested under `company`, `division`, or `region`.

**`division`**
- No special data requirements. General intermediate grouping.
- Cannot be nested under a `team`.

**`region`**
- No special data requirements. Geographic grouping.
- Cannot be nested under a `team`.

**`team`**
- Leaf node. No child units of any type allowed under a team.
- Enforced on the parent side at `create_org_unit` time.

### Nesting Rule Enforcement

All nesting rules are enforced in `create_org_unit` in `app/modules/org_units/service.py`.
The single check `parent.unit_type == 'team' → reject` covers all child types.
The extra check `unit_type == 'client_account' and parent.unit_type == 'client_account' → reject` handles the client_account-under-client_account case.

---

## Absolute Rules

### Database Access
- **ALL data access goes through FastAPI.** Never expose or use PostgREST/Supabase auto-generated REST endpoints.
- Use `asyncpg` driver directly via SQLAlchemy async. No sync ORM calls anywhere.
- Every tenant-scoped query uses `get_tenant_db(request)` dependency which runs `SET LOCAL app.current_tenant = '<uuid>'`.
- Admin/internal operations use `get_bypass_db()` which runs `SET LOCAL app.bypass_rls = 'true'`.
- All three session types are defined in `app/database.py`.

### RLS runtime role — Load-Bearing

The Supabase local `postgres` role has `rolbypassrls=true`. A role with that attribute **ignores every RLS policy on every table, regardless of policy contents**. Connecting as `postgres` alone gives you zero tenant isolation at the database layer — only the application's WHERE clauses would keep tenants apart, with no backstop.

The fix: every `get_tenant_db` and `get_bypass_db` session runs `SET LOCAL ROLE nexus_app` at the top of its transaction. `nexus_app` is created by alembic migration `0010_create_nexus_app_role` with `NOBYPASSRLS`, so its queries are subject to the full policy pair:

- `tenant_isolation` grants row access when `tenant_id = current_setting('app.current_tenant')` (set by `get_tenant_db`)
- `service_bypass` grants row access when `current_setting('app.bypass_rls') = 'true'` (set by `get_bypass_db`)

The switch is controlled by `DB_RUNTIME_ROLE` in `.env` (default `nexus_app`). Leaving it empty disables the switch — **only safe in tests and in the first bootstrap of a fresh cluster before migration 0010 has run**. Never ship production config with it empty.

Alembic continues to run as `postgres` because only that role has CREATE privileges; migrations are the only place that should ever touch schema.

### RLS Pattern — Always Applied
Every tenant-scoped table MUST have this RLS policy pair (the canonical full-command form — NO `FOR SELECT`):
```sql
-- Tenant isolation — applies to SELECT/INSERT/UPDATE/DELETE
CREATE POLICY "tenant_isolation" ON <table_name>
  USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- Service role bypass (for internal admin ops)
CREATE POLICY "service_bypass" ON <table_name>
  USING (current_setting('app.bypass_rls', true) = 'true');
```

**Two traps to avoid:**

1. **`FOR SELECT USING (...)` without a matching `WITH CHECK`** silently blocks INSERT/UPDATE/DELETE from tenant-scoped sessions because the implicit CHECK falls through to `service_bypass`, which is false when `app.bypass_rls` is unset. Phase 1 tables (clients, users, organizational_units, user_role_assignments, user_invites, audit_log) were originally written with this broken pattern; migrations 0008 and 0009 corrected them.

2. **Raw `::uuid` cast without `NULLIF`.** `SET LOCAL app.current_tenant = '<uuid>'` reverts at transaction end, but for a custom GUC that was never declared at session boot, PostgreSQL restores it to empty string `""` — **not** NULL. The next pooled request that evaluates `current_setting('app.current_tenant', true)::uuid` crashes with `invalid input syntax for type uuid: ""`. Migration 0011 wraps every tenant-filter policy in `NULLIF(current_setting(...), '')::uuid`. Any new policy must do the same.

**Do NOT use `auth.jwt()` in RLS policies.** It returns null when connecting via direct asyncpg — it only works through PostgREST. Using it will silently block all queries or fail to isolate tenants.

**Runtime verification**: `app/main.py` runs a startup assertion (`_assert_rls_completeness`) that queries `pg_policies` for every tenant-scoped table and aborts startup with a CRITICAL log if any table is missing `tenant_isolation` (with non-NULL `WITH CHECK`) or `service_bypass`. Skipped under `ENVIRONMENT=test` or when `DB_RUNTIME_ROLE` is unset. When adding a new tenant-scoped table, add it to the enumerated list in that helper.

### Auth Abstraction — Load-Bearing Constraint
JWT verification goes through a **provider-agnostic interface** in `app/modules/auth/`. Business logic never calls the Supabase Python SDK directly. This is what makes a future Cognito swap a config change, not a rewrite.

```python
# CORRECT — provider-agnostic interface
from app.modules.auth.service import verify_access_token
from app.modules.auth.context import get_current_user_roles, UserContext

# WRONG — hard couples to Supabase
from supabase import Client
```

`verify_access_token()` enforces:
- **Algorithm pinning** — `algorithms=["ES256"]` only (Supabase's auth hook signs ES256; `RS256` was removed in Batch G to close an algorithm-confusion surface).
- **Audience** — `aud = "authenticated"` (Supabase's default role claim).
- **Issuer** — when `settings.supabase_url` is set, `iss` must equal `{supabase_url}/auth/v1`. This prevents a token minted by a different Supabase project that shares the JWKS path from authenticating to this backend.
- **JWKS caching** — fetched once, refreshed on key rotation.

Candidate JWTs (single-use session tokens, HS256, signed with `CANDIDATE_JWT_SECRET`) are the separate `verify_candidate_token()` path. Algorithm is hardcoded to `["HS256"]`; there is no algorithm allowlist confusion path.

**Single-use enforcement** (Phase 3C.1, **shipped**):
1. **Middleware gate** (`middleware/auth.py`) — verifies signature + expiry, then queries `candidate_session_tokens` by `jti`. Rejects with 401 if the JTI is unknown (`TOKEN_UNKNOWN`) or `superseded_at IS NOT NULL` (`TOKEN_SUPERSEDED`). The middleware does **not** consume `used_at` — that is the sole responsibility of the `/start` endpoint, by design (so a candidate can re-fetch pre-check / consent endpoints without burning their token).
2. **Atomic consume on `/start`** (`session/service.py:412–426`) — `UPDATE candidate_session_tokens SET used_at = now() WHERE jti = ? AND used_at IS NULL RETURNING jti`. If 0 rows, raises `TokenAlreadyUsedError` (409). Replay coverage in `tests/test_middleware_candidate_single_use.py`.
3. **Supersession chain** — when an invite is resent, `scheduler/service.py` mints a new token row and stamps `superseded_at = now()` + `superseded_by = <new_jti>` on the prior row. The middleware gate above rejects superseded tokens before they reach any handler.

The only direct Supabase HTTP call is in `settings/service.py::_delete_auth_user()` for user deactivation (hitting the Admin API with the service role key). This is isolated and would be the single swap point.

## AI Provider & Prompt Management — Load-Bearing

Phase 2A introduces `app/ai/` as the provider-agnostic AI layer.

- **AIConfig** (`app/ai/config.py`) — env-driven model IDs and `reasoning_effort`. Never hardcode. Swapping a model for a task is a single `.env` change.
- **PromptLoader** (`app/ai/prompts.py`) — reads versioned prompts from `prompts/v{N}/<name>.txt`, cached in memory. Prompt updates are file changes, not code deploys.
- **OpenAI client factory** (`app/ai/client.py`) — returns an `instructor.AsyncInstructor` wrapped around `openai.AsyncOpenAI`. OpenTelemetry tracing is wired separately at app startup via `app/ai/otel.py` — the OpenAI auto-instrumentor captures every `chat.completions.create` call as a span. Both exporters (Console for dev, OTLP for production) are off by default — see `.env.example` for the contract.
- **EnrichmentOutput** and **SignalExtractionOutput** schemas (`app/ai/schemas.py`) — strict Pydantic models for the two-phase JD pipeline. Phase 1 produces an enriched JD; Phase 2 produces signals + seniority + role_summary with provenance metadata. `source=ai_inferred` requires `inference_basis`; `ai_extracted` requires it to be null.

Business logic imports `get_openai_client()` and `prompt_loader` from `app.ai.*` — never openai/instructor directly. This is the single swap point for a future provider change.

```python
# CORRECT — provider-agnostic interface
from app.ai.client import get_openai_client
from app.ai.prompts import prompt_loader
from app.ai.config import ai_config

# WRONG — hard couples to OpenAI
from openai import AsyncOpenAI
```

**Documented carve-outs (allowed):**
- `app/modules/jd/errors.py` and `app/modules/jd/actors.py` import `openai` and `instructor.core.InstructorRetryException` *as types*, exclusively for retry/permanent-error classification (`_PERMANENT_EXCEPTIONS`) and user-safe error message mapping (`_SAFE_MESSAGES`). They never call the SDK. If the provider changes, this exception map moves with the new SDK; nothing else changes.
- `app/ai/realtime.py` is the second blessed import site for vendor SDKs. It owns LiveKit plugin instantiation (`livekit.plugins.openai`, `deepgram`, `cartesia`, `sarvam`, `silero`, `turn_detector`) so the interview-engine entrypoint never touches them directly. Reads model IDs / voices / effort from `AIConfig` — never from env or settings. (`interview_agent_name` is the only LiveKit fleet-wide routing label that lives in `settings`; AIConfig is for AI provider config only.) Lazy imports inside each factory keep the FastAPI nexus process free of the realtime plugin packages even though they're installed in the same image — the engine compose service is the only one that loads them. **`build_noise_cancellation()` no longer exists** (ai-coustics removed; no server-side NC). Current factories: `build_interruption_options()` (`mode="vad"` with `min_words=2`), `build_vad()` (returns Silero VAD — `silero.VAD.load()`), `build_stt_plugin()` (provider dispatcher: `deepgram` default — `nova-3`, `en-IN`, + per-session keyterm boosting — with `sarvam` as the alternate), `build_tts_plugin()` (provider dispatcher: `sarvam` default — `bulbul:v3` — with `openai` / `cartesia` alternates).
- A future cleanup is to lift the JD exception map into `app/ai/errors.py` and re-export typed sentinels so module code never references vendor exception classes by name. Tracked as tech-debt; not blocking.
- The engine runs as the `nexus-engine` compose service from the **same Docker image** as `nexus` and `nexus-worker`, just with command `python -m app.modules.interview_engine` (this entrypoint builds the `SessionConfig` and runs the single interview engine). Single venv. The two-venv layout that existed pre-Phase-3 was retired when the engine source tree merged into nexus. After editing engine prompts under `prompts/v4/engine/`, restart with `docker compose up -d --force-recreate nexus-engine` (a per-session `PromptLoader` reads fresh from the `.:/app` mount; agent.py code changes also need the restart).

### RBAC Enforcement
- Auth middleware extracts JWT and attaches `token_payload` to `request.state` before any route handler runs.
- Roles: `Admin`, `Recruiter`, `Hiring Manager`, `Interviewer`, `Observer` (system roles, seeded at migration)
- Super Admin is determined by `client.super_admin_id == user.id`, not a role assignment
- `UserContext` (loaded via `get_current_user_roles` dependency) provides `has_role_in_unit()`, `has_permission_in_unit()`, `all_permissions()` methods
- Authorization guards: `require_projectx_admin()` (JWT claim), `require_super_admin()` (DB check)
- Permissions are NOT in the JWT — they are loaded from the DB per-request via role assignments
- Candidates are **not Supabase Auth users**. They access sessions via signed single-use JWTs generated and verified entirely by the backend.

### Candidate JWT Rules
- Single-use JWT. Marked used on first verification — no replay possible.
- JWT signing key stored in env vars (MVP) / AWS Secrets Manager (Enterprise).
- Treat with the same sensitivity as a DB credential.
- OTP (6-digit code via email/SMS) is a configurable layer on top — set per JD, not globally.

### Secrets
- Load ALL configuration through `app/config.py` using `pydantic-settings`.
- Config reads from environment variables only.
- **Never hardcode secrets, API keys, or credentials anywhere in the codebase.**
- `.env` files are for local development only and must be in `.gitignore`.

---

## Module Responsibilities

### Phase 1 — Implemented

| Module | What It Owns |
|---|---|
| `auth` | JWKS-based JWT verification (provider-agnostic), `UserContext` RBAC, `require_projectx_admin()` / `require_super_admin()` guards, `/me` endpoint, invite claiming, onboarding completion |
| `admin` | ProjectX internal operations: `provision_client()` creates tenant + sends Company Admin invite. `list_clients()` for admin dashboard. Requires `is_projectx_admin` JWT claim. |
| `settings` | Team management: send invite, resend (supersedes old), revoke, deactivate user (including Supabase auth account deletion via Admin API) |
| `org_units` | Org unit CRUD (hierarchical tree), member/role assignment, visibility filtering (super admin sees all, others see assigned + ancestors) |
| `roles` | Role listing endpoint — returns system roles (visible to all) + tenant custom roles |
| `notifications` | Provider-agnostic email dispatch. `DryRunProvider` (logs to stdout) and `ResendProvider`. HTML template rendering for invite emails. |

### Phase 2A — Implemented

| Module | What It Owns |
|---|---|
| `ai` | Provider-agnostic AI layer. `AIConfig` (env-driven model/effort), `PromptLoader` (versioned prompts, in-memory cache), `get_openai_client()` (instructor + plain openai.AsyncOpenAI), `tracing.py` + `otel.py` (OpenTelemetry auto-instrumentation + prompt-attribute helper), `EnrichmentOutput` + `SignalExtractionOutput` schemas (split in the JD creation flow refinement) with provenance validators. (The original combined `ExtractionOutput` landed in Phase 2A; subsequently split into the two-phase form on 2026-04-28 — see docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md.) |
| `jd` | JD pipeline full implementation. `create_job_posting` with company profile ancestry gate, `extract_and_enhance_jd` + `reenrich_jd` Dramatiq actors (Call 1 + Call 2), state machine with audit trail, `require_job_access` ancestry-walking authz, SSE status stream via `get_tenant_session` (Batch F RLS fix), `x-correlation-id` header validation (Batch D), router with create/get/list/stream/re-enrich/confirm-signals endpoints. |
| `org_units` (extended) | `CompanyProfile` strict Pydantic schema, `find_company_profile_in_ancestry()` helper, `_validate_and_normalize_company_profile()` hook in create/update, `company_profile_completed_at/by` tracking stamps. |
| `audit` | Append-only audit log with tenant-scoped RLS (migration 0008 added the missing `FOR INSERT WITH CHECK` policy that was silently dropping tenant-scoped writes). |

**`enrichment_status` flow note:** The initial extraction actor (`extract_and_enhance_jd`) drives `enrichment_status` through `idle → streaming → completed` (or `failed`) as part of its two-phase run; `streaming` is written as a pre-mark before the phase-1 LLM call so the frontend can render a loading indicator. The legacy re-enrichment actor (`reenrich_jd`, Call 2, triggered after signal edits) also writes `streaming`/`completed`/`failed` but operates as an independent flow against an already-confirmed job. The `streaming` literal is therefore shared between both flows; retiring it is contingent on moving `reenrich_jd` to the same two-phase pattern as the initial extraction actor.

### Phase 2B — Implemented

| Module | What It Owns |
|---|---|
| `jd` (extended) | `save_signals` uses `SELECT … FOR UPDATE` on the parent job row to serialize concurrent writers, then inserts a new snapshot at `MAX(version) + 1`. A unique constraint on `(job_posting_id, version)` backstops the lock. There is no optimistic concurrency check — clients don't send an `expected_version` and the server doesn't raise a conflict error. `confirm_signals` treats `PipelineAlreadyExistsError` as an idempotent no-op; other errors continue through the error + audit path. |

### Phase 2C.1 — Implemented

| Module | What It Owns |
|---|---|
| `pipelines` | Pipeline template library scoped by org unit, per-job pipeline instance, stage CRUD (name/type/duration/difficulty/signal filter/pass criteria/advance behavior), drag-to-reorder, template swap, reset-to-source. `jd.confirm_signals` calls `ensure_minimal_pipeline_for_job` to auto-create the bookend Intake → Debrief pipeline on signal confirmation; the recruiter adds the middle stage(s) before the activation gate opens. Ancestry-walking authz via `require_template_access`. **Stage type v5 (migration 0016):** types collapsed from 9 → 6 (`intake`, `phone_screen`, `ai_screening`, `human_interview`, `debrief`, `take_home`); `recruiter`, `panel_interview`, `offer`, `ai_interview` hard-removed with no aliases. Instance stages carry participants (interviewers / observers / reviewers) via `pipeline_stage_participants`, gated by system-role lookup at the job's org unit ancestry; `pipeline_stage_participants` is in `_TENANT_SCOPED_TABLES` and covered by the startup RLS check. Templates remain staffing-agnostic (no participants). |

### Phase 2C.2 — Implemented

| Module | What It Owns |
|---|---|
| `question_bank` | Per-stage question bank generation via Dramatiq actor. Adaptive coverage (mandatory-fits-session validation, mandatory demotion auto-correction, duration as session time limit not generation budget), bundling discipline, per-stage-type prompts, coverage notes persistence for audit trail. `list_banks` GET is read-idempotent (Batch G — returns placeholder entries for stages without banks, does NOT create drafts on poll). State machine raises typed exceptions (`IllegalTransitionError`, `ReorderMismatchError`). Bulk `get_banks_for_pipeline` uses 4 constant queries instead of 1+2N. `refine.py` handles per-question draft + refine LLM calls. `set_llm_span_attributes()` calls on actors wire OTel span metadata (prompt name+version, bank id, stage id, tenant id). Bank staleness is tracked via `pipeline_version_at_generation` + `is_stale` (added in 0018) so pipeline edits invalidate banks deterministically. |

### Phase 3B — Implemented

| Module | What It Owns |
|---|---|
| `candidates` | Candidate CRUD with PII fields, partial-unique index on `(tenant_id, email) WHERE pii_redacted_at IS NULL` (duplicates raise `DuplicateEmailError`), resume upload via `resume_service.py` (S3 pre-signed URL pattern), source typing in `sources.py` (manual / ATS / referral). Kanban board aggregation (`/api/candidates/kanban`) and assignment-based stage transitions. PII redaction is a separate gate guarded by `CandidateHasActiveSessionError` — a candidate with any non-terminal session cannot be redacted. Ancestry-walking authz via `require_candidate_access`. |

### Phase 3C — Implemented

| Module | What It Owns |
|---|---|
| `scheduler` | Invite send / resend / revoke. `send_invite` mints a candidate JWT (HS256) and inserts the matching `candidate_session_tokens` row (jti, tenant, session_id, expires_at). Resend creates a new token row and stamps `superseded_at + superseded_by` on the prior row, building a per-session supersession chain. Notification dispatch via the provider-agnostic notifications module — notification dispatch reads settings.candidate_session_base_url (NOT frontend_base_url). |
| `session` | Two routers: `candidate_session_router` (candidate-facing, JWT in path) and `session_router` (recruiter-facing, read-only). State machine: `created → pre_check → consented → active → completed \| cancelled \| error`. OTP gate (CSPRNG 6-digit code, HMAC-SHA256 hash, 10-minute lifetime, max 3 attempts, 60s rate limit, constant-time compare). **Single-use token enforcement** is atomic on `/start` (`UPDATE … WHERE used_at IS NULL RETURNING`). Phase 3C.2 wired the LiveKit room + token provisioning: `/start` mints a candidate `room_join` token, mints + records an engine dispatch JWT, dispatches the agent, then atomically consumes the candidate token and transitions to `active` (502 `AGENT_DISPATCH_FAILED` if dispatch fails — token is NOT consumed in that case so the candidate can retry). LiveKit helpers live at `session/livekit.py`. |
| `interview_runtime` | In-process helpers the engine calls directly (post-Phase-3 merge). `build_session_config` walks session → assignment → candidate → job → stage → bank → snapshot → questions → ancestry-walked company profile to build the engine's `SessionConfig`. `record_session_result` atomically updates the session row gated on `state='active'`, idempotent on retry, writes an audit row; the `SessionResult` carries `coverage_summary`. Both run on a bypass-RLS session with explicit `tenant_id` filter on every query (RLS-only defense layer; no HTTP router, no engine-dispatch JWT — those were retired in migration `0025`). The wire-format Pydantic models (`SessionConfig`, `SessionResult`) live here for the engine to import. |
| `tenant_settings` | Per-tenant engine + proctoring configuration. ORM `TenantSettingsModel` (PK = `tenant_id`, FK `clients.id` ON DELETE CASCADE); Pydantic `TenantSettings` with `engine_agent_name: str \| None` and the proctoring trio `proctoring_enabled` (default True), `proctoring_soft_violation_limit` (3), `proctoring_fullscreen_grace_seconds` (10). `get_tenant_settings(db, tenant_id)` is the single read path with lazy-default semantics (no row → schema defaults). No router. |

### Phase 3D — Implemented

| Module | What It Owns |
|---|---|
| `reporting` | Post-session report generator. `build_report` runs a 3-layer hybrid: (1) deterministic — projects the engine's `coverage_summary` onto role signals, scores the four dimensions (Overall/Technical/Behavioral/Communication); (2) parallel LLM signal re-check (may override the engine state with a texture grade); (3) LLM narrative (sees final numbers, never changes them). Scoring is state×texture (bluff-aware), fit-ceiling-capped, ±5 holistic. `resolve_verdict` → advance / borderline / reject (Borderline never auto-resolved). `session_reports` table (UNIQUE on session_id), `score_session_report` actor (queue `report_scoring`), `/api/reports/*` (hub index, report view, presigned recording, proctoring view, `POST /{report_id}/decision` human decision). |
| `reel` | Candidate Reel (~45–60s highlight video). `director.generate_edl` (`gpt-5.4`, Responses API + structured output) reads report ground-truth THEN the word-indexed transcript and emits an ordered EDL (title → match → (point → clip)×N → outro); `validate_edl` is pure + hallucination-guarded (resolves word indices → ms, edge-trims, dedups, per-clip 16s cap, 80s total budget). `timing.py` maps live-VAD candidate spans to recording-ms via a per-session measured pipeline lag. `render.py` cuts clips from the R2 recording + builds branded cards under Arjun TTS narration with burned captions. `generate_session_reel` actor (queue `reel`, runs in `nexus-vision-worker`), uploads MP4 to R2. Eligibility: report ready + verdict advance/borderline + recording exists. |
| `vision` | Server-plane gaze proctoring (POC). `analyze_session_proctoring` actor (queue `vision`, `nexus-vision-worker` only) downloads the recording from R2, runs an ONNX gaze estimator (`gaze/mobilegaze.py`, resnet34 / Gaze360 — **non-commercial weights, dev/POC only, GA blocker**) + RetinaFace detection over ffmpeg-sampled frames, derives gaze features → risk band + flagged intervals + heatmap. Persists **features only, never frames** to `session_proctoring_analysis` (migration `0051`); timeline thumbnails to `session_timeline_thumbnails` (`0052`) on R2. Hard perf caps after the 2026-06-01 incident: `vision_sample_fps=2.0`, single ORT intra-op thread, `cpus: 4` backstop, GPU-first providers. The **client-side** deterrent is separate — `session/proctoring.py` classifies MediaPipe head-pose / focus / devtools / fullscreen violations reported by `frontend/session`. |
| `ats` | Ceipal sync. Vendor-blind `ATSSyncOrchestrator` (cursor-based job-driven sync, lazy entity materialization, quarantine, email-collision matrix, diff/audit), `CeipalAdapter`, `poll_ats_connection` actor (queue `ats_poll`, per-connection advisory lock), credential encryption (`crypto.py`), `/api/ats/*` (connections, sync, job-status-filter, sync-logs, stage-mappings, retry-import). **Manual-trigger only** — the scheduled auto-poll was removed. Greenhouse/Workday adapters not yet built. Migration `0036`. |

### Future Phases — Stubbed

| Module | What It Will Own |
|---|---|
| `analysis` | Dead stub — real-time scoring/probe selection was absorbed into `interview_engine/brain`. `GET /api/analysis/{id}/signals` returns `not_implemented`. Kept only as leftover scaffolding; remove or repurpose. |

---

## ATS Adapter Pattern

New ATS integrations only require a new adapter implementing the `ATSAdapter` interface. The core pipeline never changes.

```python
# Every ATS adapter must implement this interface
class ATSAdapter(Protocol):
    async def fetch_new_jobs(self, tenant: Tenant) -> list[Job]: ...
    async def fetch_new_candidates(self, tenant: Tenant, job_id: str) -> list[Candidate]: ...
    async def push_interview_outcome(self, tenant: Tenant, outcome: InterviewOutcome) -> None: ...
```

### Ceipal — Critical Details
- **Has NO webhooks.** Polling is the **sole** data pipeline — not a fallback.
- **Sync is manual-trigger today** (`POST /api/ats/.../sync`, `poll_ats_connection` actor on queue `ats_poll` with a per-connection advisory lock). The scheduled 15-min auto-poll cron was removed; re-introducing it is a worker-schedule change, not a rewrite.
- Delta detection: cursor-based, job-driven. Compare `ceipal_job_id` / `ceipal_submission_id` against DB on every poll.
- Auth: OAuth2. Access token + refresh token. Auto-refresh at 80% of token lifetime.
- Primary entity is **Submission** (recruiter submits candidate to job), not Applicant.
- Rate limits are undocumented. Test on first integration — run consecutive list calls and inspect response headers.

---

## Session State Machine

Live session state lives **in the engine process** for the duration of the LiveKit call; the durable record is the append-only **event-log envelope** (`engine-events/<session_id>.json`, optionally mirrored to an AWS S3 sink) plus the final `SessionResult` committed by `record_session_result`. Redis is the Dramatiq broker, not a session-state checkpoint store. A liveness heartbeat (`record_engine_heartbeat`, `sessions.last_engine_heartbeat_at`, migration `0049`) keeps the reaper from killing a long interview; on engine crash the session transitions to `error` (no mid-call checkpoint recovery today).

Conceptual state tracked per session by the engine:
- Conversation history
- Current question index
- Detected signals (depth, specificity, evidence quality)
- Running per-question scores
- AI Copilot buffer (transcript, signal cards, next probe)

**Session invariants:**
- AI Copilot pipeline runs for every session regardless of whether a human is present — data is stored in Redis and surfaces in the post-session report.
- Camera + mic are required from candidates throughout.
- Candidate consent timestamped event must be logged to the session record before recording begins.

---

## Task Queue (Dramatiq)

Dramatiq is chosen over Celery: no worker memory leaks, cleaner async process model, better retry and DLQ configuration.

Tasks that must be async (never block the request cycle):
- Question bank generation (LLM call, 10–30s)
- Post-session report compilation
- Candidate notification dispatch
- ATS outbound status sync after session completion

### Actor Discipline

- **Idempotency is mandatory.** Every actor must be safe to re-run. Use a database row-state check (e.g. `enrichment_status`, `bank_status`) at the top of the actor and short-circuit if the work is already done or in progress.
- **Retry policy is explicit.** Permanent errors (validation failures, missing rows, API 4xx that won't change on retry) raise a typed exception listed in `_PERMANENT_EXCEPTIONS`; Dramatiq does not retry those. Transient errors (network, 5xx, rate limits) retry with exponential backoff up to the actor's declared `max_retries`.
- **Dead-letter queue (DLQ).** Tasks that exhaust retries land in the DLQ — not the main broker. The DLQ is monitored; SEV3 fires if it fills above 0 for >24h.
- **Tracing.** Each actor's LLM call is auto-captured as an OpenTelemetry span by the OpenAI instrumentor. Actors call `set_llm_span_attributes()` from `app/ai/tracing.py` to add prompt name+version, tenant id, and correlation id. The correlation ID also flows through structured logs at every hop, so log-grep and trace-search produce the same picture.
- **No PII in actor arguments.** Pass IDs (job_id, candidate_id, session_id), not bodies. The actor reloads from Postgres under the right RLS context.
- **Bypass-RLS sessions in actors.** Worker tasks run outside a request — they don't have `app.current_tenant` set. Use `get_bypass_db()` and re-establish the tenant scope explicitly via `SET LOCAL app.current_tenant` if the actor needs RLS, or stay bypass-only for cross-tenant batch work.

---

## AI Pipeline Rules

### Async (OpenAI API — report quality, no latency pressure)
Used for: question bank generation, post-session reports, signal analysis, candidate summaries.
These are batch tasks. API latency is not a constraint.

### Real-time (OpenAI API (GPT-5 mini) — 1,200ms P50 budget end-to-end)
Used for: live answer scoring, probe selection, dynamic question generation during sessions.

Real-time latency budget:
| Step | Target |
|---|---|
| WebRTC transport in | 20–50ms |
| Voice activity detection | 100–150ms |
| Deepgram Nova-3 STT | 200–300ms |
| OpenAI API (GPT-5 mini) inference | 200–400ms |
| TTS first audio byte | 80–300ms |
| WebRTC transport out | 20–50ms |
| **Total P50** | **~620–1,250ms** |

TTS TTFB is the highest-leverage variable. TTS default is **Sarvam `bulbul:v3`** (voice `shubh`, en-IN); OpenAI (`gpt-4o-mini-tts`) and Cartesia (`sonic-2`) are env-selectable alternates via `build_tts_plugin()`. STT default is **Deepgram `nova-3`** (en-IN, transcribe mode, per-session keyterm boosting); Sarvam (`saaras:v3`) is the alternate. All real-time model IDs/voices live in `AIConfig`, never hardcoded.

### OpenTelemetry — Vendor-Neutral by Design
LLM traces flow through OpenTelemetry instrumentation, not a vendor-specific SDK. The `opentelemetry-instrumentation-openai-v2` auto-instrumentor captures every `chat.completions.create` call as a span; `app/ai/tracing.set_llm_span_attributes()` adds prompt metadata. Two opt-in exporters (`OTEL_DEV_CONSOLE_EXPORTER` for stdout, `OTEL_EXPORTER_OTLP_ENDPOINT` for production); both off by default. Spans contain candidate evaluation data, so the OTLP endpoint MUST point at a sink the operator controls — never a third-party-hosted backend without a signed sub-processor agreement.

---

## Notifications Abstraction

All email/SMS dispatch must go through the provider-agnostic `notifications` module interface. Never call Resend, Twilio, AWS SES, or AWS SNS directly from business logic.

```python
# CORRECT
from app.modules.notifications import send_email, send_sms

# WRONG — hard-couples to provider
import resend
```

### Invite / confirmation link URLs

Outbound emails build links pointing at one of two frontends. There are two distinct settings — pick the right one per email type:

| Email type | Setting | Frontend app |
|---|---|---|
| Company admin invite, team invite, team-invite resend | `settings.frontend_base_url` | `frontend/app` (recruiter dashboard) |
| Candidate interview invite, interview resend | `settings.candidate_session_base_url` | `frontend/session` (candidate interview surface) |

Pointing a candidate-bound email at `frontend_base_url` would land the candidate on the recruiter login page — the wrong surface entirely. The historical pattern `f"{settings.frontend_base_url}/interview/{token_str}"` was correct only because both surfaces lived in the same app; after the 2026-05-01 split, that pattern is a bug.

**Anti-regression grep**: `grep -rn "frontend_base_url.*interview\|frontend_base_url.*candidate-session" app/` must return zero matches. Pre-merge gate today; CI gate when CI lands.

Every environment must set BOTH settings explicitly. The localhost defaults are local-dev convenience only.

---

## Database Migrations

### Current State
The initial schema (6 tables, first-cut RLS policies, system role seeds, and the Supabase auth hook) lives in `backend/supabase/migrations/20260405000000_initial_schema.sql`. Every incremental change since Phase 2A has been an Alembic migration in `migrations/versions/`. Current head: `0059_drop_knockout`.

Migrations so far:
- `0001_phase_2b_columns` — signal editing + version columns
- `0002_add_updated_by_to_job_postings` — track the last editor for audit
- `0003_signal_schema_v2` — provenance-aware signal model
- `0004_pipeline_builder` — Phase 2C.1 tables
- `0005_simplify_signal_filter` — flatten legacy include/exclude nesting
- `0006_question_banks` — Phase 2C.2 tables
- `0007_add_coverage_notes` — audit trail for coverage decisions
- `0008_audit_log_tenant_insert` — **RLS hardening**: fixed `audit_log` silently dropping tenant writes
- `0009_phase1_rls_full_command` — **RLS hardening**: Phase 1 tables get full-command `tenant_isolation` (USING + WITH CHECK)
- `0010_create_nexus_app_role` — **RLS hardening**: dedicated `nexus_app` role with `NOBYPASSRLS` + grants; without this every policy is a runtime no-op because `postgres` has `rolbypassrls=true`
- `0011_rls_null_safe_current_tenant` — **RLS hardening**: wraps `current_setting('app.current_tenant', true)::uuid` in `NULLIF(..., '')::uuid` everywhere. Fixes a PG quirk where `SET LOCAL` reverts a custom GUC to empty-string rather than NULL on transaction end, making the cast crash on the next pooled request.
- `0012_rename_service_role_bypass` — **cleanup**: rename `service_role_bypass` → `service_bypass` on Phase 2A/2C tables for consistency with the canonical name used elsewhere.
- `0013_candidates_core` — **Phase 3B**: `candidates`, `candidate_job_assignments`, `candidate_stage_progress` tables; canonical RLS pair (with NULLIF) on each; permission seed for candidate_view / candidate_edit / candidate_delete.
- `0014_sessions_scheduler_core` — **Phase 3C.1**: reshape of `sessions` table; new `candidate_session_tokens` table (jti PK, tenant, session_id, expires_at, used_at, superseded_at, superseded_by); `job_pipeline_stages.otp_required_default BOOLEAN`.
- `0015_pipeline_stage_v4` — pipeline stage type allowlist broadened to 9 values (intermediate state — superseded by 0016).
- `0016_stage_v5_participants` — stage type enum collapsed from 9 → 6 values (`intake`, `phone_screen`, `ai_screening`, `human_interview`, `debrief`, `take_home`); legacy rows renamed (`recruiter`/`panel_interview` → `human_interview`, `ai_interview` → `ai_screening`); `offer` rows deleted. Adds `pipeline_stage_participants` (instance-only staffing table) with canonical RLS pair. Registered in `_TENANT_SCOPED_TABLES`.
- `0017_stage_questions_updated_at_trigger` — BEFORE UPDATE trigger using `clock_timestamp()` (not `NOW()`) so updated_at advances even within a single transaction.
- `0018_pipeline_versioning_and_pause` — adds `job_pipeline_instances.pipeline_version` (monotonic per-instance counter), `job_pipeline_stages.paused_at`, `stage_question_banks.{pipeline_version_at_generation, stage_config_snapshot, is_stale}`, `candidate_job_assignments.entered_at_pipeline_version`. Broadens `job_postings.status` CHECK to include `pipeline_built`, `active`, `archived`.
- `0019_relax_io_stage_columns` — relaxes NOT NULL on stage columns that are FORBIDDEN for `intake` / `debrief` stage types (duration_minutes, difficulty, signal_filter, advance_behavior). Pydantic field-rules validator is the source of truth.
- `0020_drop_workspace_mode` — removes `clients.workspace_mode`. The agency/enterprise distinction collapsed: every tenant can now nest `client_account` units regardless of how it identified during onboarding.
- `0021_add_clients_blocked_at` — adds `clients.blocked_at TIMESTAMP NULL`. Pairs with `deleted_at` to define three tenant states (active / blocked / deleted) enforced at `/api/auth/login` and `get_current_user_roles`.
- `0022_users_partial_unique_auth` — replaces the plain UNIQUE on `users.auth_user_id` with a partial unique index `WHERE deleted_at IS NULL`. Required so a re-invite after tenant soft-delete can re-bind the same Supabase Auth identity.
- `0023_tenant_hard_delete_cascade` — drops `audit_log_tenant_id_fkey` + `audit_log_actor_id_fkey` (audit history outlives the rows it references); converts every other `tenant_id` FK to `ON DELETE CASCADE` so `DELETE FROM clients WHERE id = ?` propagates cleanly.
- `0024_engine_integration` — Adds `engine_dispatch_tokens` + `engine_token_uses` tables (tenant-scoped + service-bypass) and 7 result columns to `sessions` (`livekit_room_name`, `agent_started_at`, `agent_completed_at`, `transcript`, `questions_asked`, `questions_skipped`, `total_probes_fired`). The two new tables were dropped in `0025`; the `sessions` columns remain.
- `0025_drop_engine_dispatch_tables` — Drops `engine_dispatch_tokens` + `engine_token_uses` (the engine now calls `interview_runtime.service` in-process, no HTTP boundary).
- `0026_question_kind_column` — Adds `stage_questions.question_kind` (TEXT NOT NULL DEFAULT `'technical_depth'`, CHECK in `('technical_depth','behavioral_star','compliance_binary','open_culture')`). Bank-generator emits the field on every question.
- `0027_tenant_settings` — Adds `tenant_settings` table (PK = `tenant_id`, FK→`clients` ON DELETE CASCADE, two columns: `engine_knockout_policy` enum (`'record_only' | 'close_polite'`, default `'record_only'`) + `engine_agent_name` nullable TEXT) and `sessions.knockout_failures JSONB NOT NULL DEFAULT '[]'`. Lazy-default read pattern: `get_tenant_settings` returns schema defaults when the tenant has no row. (The `engine_knockout_policy` + `knockout_failures` columns were later dropped in `0059`.)
- `0028_audio_tuning_summary` — Adds `sessions.audio_tuning_summary JSONB NULL`. Per-session snapshot of NC model + enhancement level + interruption mode + browser-side getUserMedia hints, used for empirical tuning and audit.
- `0041_extracted_keyterms` — Adds `stage_question_banks.extracted_keyterms JSONB NULL`. Populated by `question_bank/actors.py:generate_question_bank_stage` after refinement completes; consumed by the engine at session start for Deepgram nova-3 keyterm prompting. Legacy banks not backfilled (regeneration repopulates).
- `0042_question_difficulty` — Adds per-question `difficulty` to `stage_questions` (used by the engine's brain to calibrate grading strictness; the bank-generator emits it).
- `0043_session_proctoring` — Adds `sessions.proctoring_violations` (JSONB), `proctoring_outcome` (TEXT) + a terminated proctoring state and tenant proctoring config.
- `0044_interview_engine_version` — Added a nullable `job_postings.interview_engine_version` engine-selection column (historical; **dropped in `0048`** when the codebase collapsed to a single interview engine).
- `0045_bank_spoken_fields` — Adds `stage_questions.primary_signal` (nullable) and switches the `question_kind` CHECK to the engine's bank taxonomy. Dev-mode clear-and-regenerate (it `DELETE`s `stage_questions`, so banks are regenerated).
- `0046_reset_emptied_banks` — Data-consistency follow-up to `0045`: resets the bank/job rows the `0045` clear left inconsistent so regeneration starts clean.
- `0047_session_reports` — Adds the `session_reports` table for the post-session reporting work (compiled report + status per completed session).
- `0048_drop_interview_engine_version` — Drops `job_postings.interview_engine_version`; the engine-selection flag is retired — there is now a single interview engine.
- `0049_session_engine_heartbeat` — Adds `sessions.last_engine_heartbeat_at`. The engine pulses it so the reaper treats a long-running interview as alive instead of killing it.
- `0050_session_recording` — Adds the recording lifecycle columns (`recording_status`, `recording_s3_key`, `recording_egress_id`, `recording_duration_seconds`, bytes, …). Recordings land on Cloudflare R2 via LiveKit Auto Egress.
- `0051_session_proctoring_analysis` — Adds the `session_proctoring_analysis` table (server-plane vision features: risk band, flagged intervals, gaze heatmap, model versions — **features only, never frames**). Canonical RLS pair; registered in `_TENANT_SCOPED_TABLES`.
- `0052_session_timeline_thumbnails` — Adds `session_timeline_thumbnails` (report-timeline WebP thumbnails, stored on R2).
- `0053_session_reels` — Adds the `session_reels` table (Candidate Reel highlight-video state: edl, chapters, r2_key, duration, model_versions, status lifecycle, version/attempts). Registered in `_TENANT_SCOPED_TABLES`.
- `0059_drop_knockout` — Drops `tenant_settings.engine_knockout_policy` (+ its CHECK constraint), `sessions.knockout_failures`, and `session_reports.knockout_results` (the verified-knockout feature was deleted). Full downgrade restores all three.

### Going Forward
- Future schema changes should use Alembic migrations in `migrations/versions/`.
- Every new tenant-scoped table must include `tenant_id UUID NOT NULL` AND the canonical RLS policy pair (`tenant_isolation` with USING + WITH CHECK, `service_bypass` with USING). Use `NULLIF(current_setting('app.current_tenant', true), '')::uuid` — not the raw cast.
- The **startup RLS completeness check** in `app/main.py` (added in Batch G) queries `pg_policies` on app boot and aborts startup with a structured CRITICAL log if any tenant-scoped table is missing `tenant_isolation` (with non-NULL WITH CHECK) or `service_bypass`. The check is skipped when `ENVIRONMENT=test` or `DB_RUNTIME_ROLE` is unset. Add your new table to the enumerated list in that helper.
- Alembic runs as a pre-deployment step.
- A rollback script is required before any migration merges.
- If adding new auth-hook-dependent tables, update both the Supabase migration (for `supabase_auth_admin` grants) and Alembic (for the table DDL).

---

## Code Standards

> Cross-cutting enterprise standards (rate limiting, supply chain, secrets rotation, logging/PII, audit, code review, incident response, threat model) are defined **once in the root `CLAUDE.md` → Enterprise Operating Standards**. The rules below are backend-specific implementation details on top of those.

- **Python 3.13** — use modern syntax (match/case, `X | Y` unions, etc.)
- **Type hints required everywhere** — no untyped function signatures
- **Async throughout** — no sync blocking calls in async context. Use `asyncio.to_thread()` if a library is sync-only.
- **Pydantic v2** for all request/response schemas and config
- **Structured logging** via structlog with correlation IDs on every log record
- **OpenTelemetry** instrumentation on all module boundaries
- Follow existing module structure. New features go inside the correct module, not at root level.
- One responsibility per module. Cross-module imports go through defined interfaces, not internal paths.

### Logging & PII Discipline

- **No raw PII in `structlog` events.** Forbidden values: candidate emails (post-redaction phase or otherwise), resume contents, transcripts, OTP codes, full JWT bearer values, full token payloads, signing keys.
- Use redactors: log `candidate_id` not email; log `session_id` + `jti_prefix=<first 8>` not the full JWT; log lengths and hashes, not bodies.
- Errors raised from validation must scrub user input from the message before reaching the log handler. The `errors.py` per-module file is the single place to map raw exceptions to user-safe messages.
- `correlation_id` is required on every log record on every request path. Middleware injects it; never overwrite or strip it inside a handler.
- The candidate JWT (`token` path param in `/api/candidate-session/{token}/*`) MUST NOT appear in any structured log field, error message, or audit row. Use `jti_prefix=<first 8 chars of jti>` from the verified token's payload, never the raw bearer string.

### Rate Limiting

- Every public router declares a rate limit at the route level. Limits are in the root CLAUDE.md → "Rate Limiting & Abuse Posture" table. Adding an endpoint without a declared limit blocks merge.
- `/api/sessions/candidate/*` endpoints are the highest-risk surface — keep per-IP and per-session caps tight.
- `/api/admin/*` is operator-facing but still rate-limited (a compromised admin token must be capped).

### Database & Connection Pool

- One asyncpg pool per process. Pool size = `min(2 * cpu_count, 32)` for the API; workers run their own pool sized to `--processes * --threads`.
- `statement_cache_size=0` for the `nexus_app` role (RLS GUC values change per request — cached prepared statements would bind to the wrong tenant context). Confirm in `app/database.py` whenever pool config changes.
- Every tenant-scoped query goes through `get_tenant_db(request)`. Every admin/internal query goes through `get_bypass_db()`. Never construct a session manually outside these helpers.
- `SELECT … FOR UPDATE` on the parent row is the chosen concurrency primitive (see `jd/service.py::save_signals`). Optimistic concurrency is opt-in, not default.

### Tenant Hard-Delete Path

- The cascade configured in migration 0023 is the contract. New tenant-scoped tables must declare `ON DELETE CASCADE` on `tenant_id` (audit_log is the documented exception).
- Deletion order matters: app code soft-deletes first (`deleted_at`), and only the admin endpoint hard-deletes via PG cascade. Never delete tenants from a request handler.

---

## Module public API

Phase 4 of the modular-monolith refactor (umbrella spec at `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`) split the monolithic `app/models.py` per module and introduced public-API discipline at the module boundary. The previous shim is gone; every domain module under `app/modules/<m>/` declares its public surface via `__init__.py` + `__all__`.

**Cross-module callers MUST import through the destination module's public API**, never through internal files:

```python
# CORRECT — public API
from app.modules.org_units import OrganizationalUnit, get_org_unit_ancestry
from app.modules.audit import log_event, actions
from app.modules.jd import require_job_access, transition

# WRONG — cross-module deep import
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.audit.service import log_event
```

**Rule scope:**
- Applies to imports CROSSING module boundaries (`from app.modules.<other> import X`).
- INTRA-module deep imports are fine (`from app.modules.jd.service import X` inside any file under `app/modules/jd/`).
- Routers and Dramatiq actors are deep-imported by `app/main.py` and `app/worker.py` — those callers live outside `app/modules/` and do not trip the rule.

**Documented exceptions (each rationalized inline at the call site):**

1. **Cross-module `from app.modules.<m>.models import X`** is permitted for ORM data-class imports — the AST lint test (`tests/test_module_boundaries.py`) explicitly allows the `models` submodule cross-module. This carve-out exists because (a) data-class imports do not introduce business-logic dependencies, and (b) some legitimate cycle-breaking patterns (`auth.context` ↔ `org_units.service` ↔ `roles.models`) cannot route through the module's `__init__.py` without re-entering a partially initialized package. See the inline NOTE comments in `app/modules/auth/context.py` and `app/modules/org_units/service.py`.

2. **`auth → org_units` invite-acceptance edge.** `auth/router.py` imports `create_org_unit` from `org_units` (via the public API: `from app.modules.org_units import create_org_unit`) to seed the root company unit during invite acceptance. The foundational-module trio (auth/audit/notifications) nominally should not reach upward into a domain module, but this single site is preserved because (a) it's acyclic, (b) the alternative (post-invite-accept hook registry) is disproportionate to the boundary purity gain. If the call-site list ever grows beyond invite acceptance, extract an orchestrator module (e.g. `onboarding/`) that depends on both auth and org_units.

3. **Lazy intra-module imports** (function-body, intentional): `auth/admin/__init__.py:39` and `auth/admin/_factory.py:14` are lazy by design (singleton factory + circular config-import avoidance). `auth/router.py` keeps a small number of intra-module function-body imports for test mockability. All have inline comments explaining the rationale.

**Enforcement:** `tests/test_module_boundaries.py` walks the AST of every `app/modules/<m>/*.py` file and asserts no cross-module deep import remains (other than the `models` allowance). Adding a new domain module? Add its name to `KNOWN_DOMAIN_MODULES` in that file. Adding a new exception is NOT permitted — extend the destination module's `__all__` instead.

**Why:** every module ends up depending on a small set of well-named re-exports (`from app.modules.org_units import OrganizationalUnit`), so refactoring inside a module (renaming a service file, splitting a schemas file) doesn't ripple across the codebase. The rule also catches accidentally-deep imports introduced during PR review — they're a flashing-red asymmetric trip wire, not a "clean it up later" item.

---

## Dev Commands

```bash
# Start full stack (FastAPI + Redis + Postgres via Supabase local)
docker compose up --build

# Run migrations
docker compose run nexus alembic upgrade head

# Generate new migration
docker compose run nexus alembic revision --autogenerate -m "description"

# Run tests
docker compose run nexus pytest

# Run tests with coverage — see "Coverage in Docker" below for the
# pytest-cov + Python 3.13 + livekit segfault workaround.
docker compose run nexus pytest --cov=app --cov-report=term-missing

# Lint
docker compose run nexus ruff check .

# Type check
docker compose run nexus mypy app/
```

### Dramatiq worker (Phase 2A)

```bash
# Start worker alongside API + Redis
docker compose up nexus-worker

# Run directly (no container)
dramatiq app.worker --processes 2 --threads 4

# Worker logs
docker compose logs -f nexus-worker
```

The worker reads actor definitions from `app/worker.py`, which imports every actor module so they register with the Redis broker at startup.

### Coverage in Docker — pytest-cov + Python 3.13 segfault workaround

`pytest --cov=...` segfaults intermittently in the nexus container. Root cause: PyO3-backed native extensions in the LiveKit dependency tree (and possibly others) are compiled for an older CPython ABI than `coverage.py`'s tracer assumes; under Python 3.13 the interpreter aborts with `ImportError: PyO3 modules compiled for CPython 3.8 or older may only be initialized once per interpreter process` mid-collection. Reproduces consistently when the test run touches `livekit.agents` modules (the entire `tests/interview_engine/` subtree).

**Workaround** — run `coverage` directly inside the long-running container instead of through the pytest-cov plugin:

```bash
docker compose up -d nexus
docker compose exec nexus python -m coverage run --branch \
    --source=app/modules/interview_engine/tasks \
    -m pytest tests/interview_engine -m "not prompt_quality" -q
docker compose exec nexus python -m coverage report --show-missing
```

Adjust `--source=...` to the package(s) under test. The `--branch` flag matches the CLAUDE.md "100% branch coverage" gate. Coverage data is identical to a successful pytest-cov run; only the invocation path differs.

Why not pin `coverage` / `pytest-cov` to a pre-segfault version? The segfault is in the PyO3 ↔ Python-3.13 interaction inside livekit's deps, not in coverage.py itself — pinning would only mask the issue temporarily and could break the rest of the dev toolchain. The workaround is the supported path until the upstream native deps are rebuilt against 3.13.

---

## Human Review Required For

- Any change to `app/modules/auth/` (JWT verification, RBAC logic)
- Any new Alembic migration touching RLS policies or tenant isolation
- Any change to the session state machine
- Any change to the candidate scoring or classification thresholds
- The Ceipal polling adapter (primary data pipeline — no fallback exists)

---