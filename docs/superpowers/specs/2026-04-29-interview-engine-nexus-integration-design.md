# Interview Engine ↔ Nexus Integration — Design

**Status:** Draft for user review · **Date:** 2026-04-29 · **Phase:** 3C.2

## Summary

Wire the standalone LiveKit agent at `backend/interview_engine/` into the FastAPI backend at `backend/nexus/` so that when a candidate clicks "Start interview", Nexus provisions a LiveKit room, dispatches an agent loaded with the real job + candidate + question-bank context, and the agent conducts an actual screening interview. Replaces the current 501 stub at `backend/nexus/app/modules/session/router.py:154-187`.

Out of scope this round: server-side recording, recruiter live observer view, AI Copilot panel, the 2×2 video grid, mid-session reconnect, scoring + reporting (Phase 3D).

## Decisions locked in brainstorming

| # | Decision | Choice |
|---|---|---|
| 1 | Code layout / process model | Sibling package, **shared code, separate process** (Option B). |
| 2 | Session-context handoff | Tiny dispatch payload (`session_id` + engine JWT), agent fetches `SessionConfig` from a new `/api/internal/*` endpoint (Option B), authed by a Nexus-minted **single-use HS256 JWT** (Option B.ii). |
| 3 | Recording / egress | **Skip this round.** Persist `transcript` JSONB; defer S3 + `egress_ended` webhook to Phase 3D. |
| 4 | Frontend live UI scope | **Minimal voice + transcript** (Option A). Single agent voice tile + candidate self-view + transcript pane. No 2×2 grid yet. No `@agents-ui` shadcn components — `frontend/app/CLAUDE.md` is shadcn-free. |
| 5 | Result persistence schema | **JSONB blob on `sessions` + summary columns** (Option C). Defer normalized observation tables to Phase 3D. |
| 6 | AI provider plumbing | **`app/ai/realtime.py` factory** owns `livekit.plugins.*` instantiation. Engine never imports vendor SDKs. Prompts move to `nexus/prompts/v1/interview/`. |
| 7 | `/start` semantics | Synchronous return + frontend 30s grace timeout (Option A). **Mint LiveKit + dispatch first, consume token last** (sub-7a Option 2). |

## 1 — Topology & shared code layout

Two long-running Python processes, one shared codebase.

```
backend/
├── nexus/                                  # FastAPI + Dramatiq
│   ├── app/
│   │   ├── ai/
│   │   │   ├── realtime.py                 # NEW — LiveKit plugin factories
│   │   │   └── config.py                   # EXTENDED — interview_*, stt_*, tts_*
│   │   ├── modules/
│   │   │   ├── session/
│   │   │   │   ├── livekit.py              # NEW — room/token/dispatch helpers
│   │   │   │   └── …                       # service.py + router.py edited
│   │   │   ├── interview_runtime/          # NEW — internal API + result service
│   │   │   │   ├── router.py
│   │   │   │   ├── service.py
│   │   │   │   ├── schemas.py              # SessionConfig + SessionResult lifted from engine
│   │   │   │   └── authz.py                # verify_engine_token wrapper
│   │   │   └── auth/service.py             # EXTENDED — verify_engine_token
│   │   └── main.py                         # routes /api/internal/* added
│   └── prompts/v1/interview/interviewer.txt  # MOVED from engine
└── interview_engine/                       # LiveKit agent worker
    ├── pyproject.toml                      # path-dep on ../nexus
    ├── Dockerfile                          # builds from backend/ context
    ├── livekit.toml                        # KEPT (informational; only matters for `livekit deploy`)
    ├── agent.py                            # REFACTORED entrypoint
    ├── agents/interviewer.py               # REFACTORED result post
    ├── nexus_client.py                     # NEW — httpx client for /config + /results
    ├── state_machine.py                    # UNCHANGED
    ├── prompt_builder.py                   # MINOR — load via nexus prompt_loader
    ├── context_loader.py                   # SIMPLIFIED to fixture | nexus_api
    ├── config.py                           # SHRUNK to InterviewEngineConfig
    ├── models.py                           # DELETED — SessionConfig moves to nexus
    └── fixtures/sample_session.json        # KEPT for engine pytest
```

### Engine `pyproject.toml`

```toml
[project]
name = "interview-engine"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "livekit-agents",
  "livekit-plugins-openai",
  "livekit-plugins-deepgram",
  "livekit-plugins-cartesia",
  "livekit-plugins-silero",
  "livekit-plugins-turn-detector",
  "livekit-plugins-ai-coustics",
  "httpx",
  "nexus @ file:///app/nexus",   # path dep, resolved at image build via COPY
]
```

### Engine `Dockerfile` (build context = `backend/`)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY nexus /app/nexus
COPY interview_engine /app/interview_engine
RUN pip install -e /app/nexus -e /app/interview_engine
WORKDIR /app/interview_engine
CMD ["python", "agent.py"]
```

### `docker-compose.yml` addition

```yaml
services:
  interview-engine:
    build:
      context: .                            # backend/
      dockerfile: interview_engine/Dockerfile
    env_file: ./nexus/.env
    environment:
      NEXUS_INTERNAL_BASE_URL: http://nexus:8000
      AGENT_NAME: Dakota-1785
    depends_on: [nexus, redis]
```

The engine container reads the same `.env` as Nexus — single source of truth for `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `LIVEKIT_*`, `INTERVIEW_ENGINE_JWT_SECRET`. Agents UI / `livekit deploy` is not used. `livekit.toml` stays as documentation of the cloud project link but is not consulted by our deploy path.

## 2 — Context handoff & session lifecycle

### 2.1 New internal endpoints (Nexus)

Both under `/api/internal/*`, served from `app/modules/interview_runtime/`. The reverse proxy (Railway / ECS LB) is configured to **not route this prefix from the public hostname** — defense in depth. The JWT is the actual gate.

```
GET  /api/internal/sessions/{session_id}/config
POST /api/internal/sessions/{session_id}/results
```

### 2.2 Engine JWT (B.ii)

A new `verify_engine_token()` lives alongside `verify_access_token` and `verify_candidate_token` in `app/modules/auth/service.py`.

- **Algorithm pinning:** `algorithms=["HS256"]`. `none` / `RS256` / `ES256` rejected.
- **Signing key:** `INTERVIEW_ENGINE_JWT_SECRET` (new env var, 32 random bytes).
- **Claims:** `sub = session_id` (UUID), `tenant_id` (UUID), `purpose = "interview_engine"`, `iat`, `exp`, `jti`.
- **Lifetime:** `min(stage.duration_minutes + 5, 90)` minutes.
- **Single-use per (jti, endpoint):** `INSERT INTO engine_token_uses (jti, endpoint) VALUES (…) ON CONFLICT DO NOTHING RETURNING jti`. 0 rows → 401 `TOKEN_REPLAYED`. The same JWT validly authenticates `/config` once and `/results` once.
- **Path/claim cross-check:** path's `{session_id}` must equal claim `sub`; claim `tenant_id` must match `sessions.tenant_id` of the loaded row. Mismatch → 401 (no info leak in the body).

### 2.3 `SessionConfig` payload built by `build_session_config(db, session_id, tenant_id)`

Shape lifts directly from `backend/interview_engine/models.py:116`. Key fact: the existing `CandidateContext` declares `name: str` and an optional `email`. **The Nexus copy of CandidateContext drops the `email` field** — PII discipline (root `CLAUDE.md` → "no raw PII in logs"; SessionConfig is logged at engine-info level on dispatch). Engine only ever knows the candidate's name.

```python
class CandidateContext(BaseModel):
    name: str          # = candidates.name
    # NO email, NO phone, NO resume contents.
```

Build steps:
1. Load `sessions` row → `candidate_job_assignments` → `job_postings` → `candidates`.
2. Walk org-unit ancestry via existing `find_company_profile_in_ancestry()` for `company.about` / `company.hiring_bar`.
3. Load `stage_question_banks` for the assignment's `current_stage_id`. **Reject if `status != 'ready'` or `is_stale = true`** → 409.
4. Load published `stage_questions` ordered by `(is_mandatory DESC, position ASC)`.
5. Load JD signals from the latest signals snapshot for the job.
6. Compose into `SessionConfig`.

**Stage-type allowlist:** the endpoint refuses any stage where `stage_type NOT IN ('ai_screening', 'phone_screen')` → 422 `STAGE_TYPE_NOT_AI_DRIVEN`. The other v5 stage types (`intake`, `human_interview`, `debrief`, `take_home`) do not run an AI agent in this phase.

### 2.4 Result post — `record_session_result(db, session_id, tenant_id, result)`

1. Load session row, verify `state == 'active'` (else 409, idempotent for already-`completed`).
2. Verify `tenant_id` from JWT == `sessions.tenant_id` (defense-in-depth even with bypass session).
3. Single update gated by `state = 'active'`:
   ```sql
   UPDATE sessions
   SET raw_result_json    = :raw,
       transcript         = :transcript,
       questions_asked    = :qa,
       probes_fired       = :pf,
       agent_completed_at = NOW(),
       result_status      = :status,        -- ok | partial
       state              = 'completed',
       state_changed_at   = NOW()
   WHERE id = :id AND tenant_id = :tenant AND state = 'active'
   RETURNING id;
   ```
4. If `RETURNING` yields 0 rows, follow up with `SELECT state, agent_completed_at FROM sessions WHERE id = :id AND tenant_id = :tenant`. State `completed` + `agent_completed_at IS NOT NULL` → idempotent 204 (no-op for retry / second-worker race). Any other state → 409 `SESSION_NOT_ACTIVE`.
5. Audit event: `actor_id=NULL`, `actor_email=NULL`, `action='engine.session.completed'`, `resource='session'`, `resource_id=session_id`, `payload={"jti_prefix": "<first-8>", "questions_asked": N, "result_status": "ok|partial"}`.
6. Return 204.

### 2.5 New `/start` flow (`session/service.py::start_session`)

Order-of-operations chosen per Q7a Option 2: mint LiveKit creds + fire dispatch first, consume token + transition state last.

```python
async def start_session(db, *, session_id, jti, ip_address, user_agent):
    session, candidate, stage = await _load_session_for_start(db, session_id)
    _require_state(session, "consented")                   # IllegalStartStateError
    _require_otp_verified_if_required(session, stage)      # OtpRequiredError
    _peek_token_unused(db, jti)                            # read-only check, no consume

    correlation_id = _new_correlation_id()                 # uuid4
    room_name = f"session-{session.id}"                    # idempotent
    candidate_lk_token = mint_candidate_lk_token(
        room_name=room_name,
        identity=f"candidate-{candidate.id}",
        name=candidate.name,                               # for agent greeting only
        ttl_minutes=stage.duration_minutes + 10,
    )
    engine_jwt = await mint_engine_dispatch_jwt(
        db,
        session_id=session.id,
        tenant_id=session.tenant_id,
        ttl_minutes=min(stage.duration_minutes + 5, 90),
    )   # Inserts the row in engine_dispatch_tokens and returns the signed JWT.
    dispatch_metadata = json.dumps({
        "session_id": str(session.id),
        "tenant_id": str(session.tenant_id),
        "engine_jwt": engine_jwt,
        "correlation_id": correlation_id,
    })
    try:
        await livekit_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                agent_name=settings.interview_agent_name,    # "Dakota-1785"
                room=room_name,
                metadata=dispatch_metadata,
            )
        )
    except Exception as e:
        # Token NOT consumed; candidate can retry.
        raise AgentDispatchFailedError(detail=str(e)) from e

    # ----- last: atomic consume + transition -----
    consumed = await consume_candidate_token(db, jti, ip_address, user_agent)
    if not consumed:
        # Race lost: cancel the room we just minted.
        with contextlib.suppress(Exception):
            await livekit_api.room.delete_room(name=room_name)
        raise TokenAlreadyUsedError()

    await transition_session(db, session.id, "active")
    await session_repo.update(
        db, session.id,
        livekit_room_name=room_name,
        started_at=now(),
    )

    return StartSessionResponse(
        livekit_url=settings.livekit_url,
        livekit_token=candidate_lk_token,
        room_name=room_name,
    )
```

The router decorator changes `status_code=501` → `status_code=200`. `StartSessionPendingResponse` is replaced by `StartSessionResponse(livekit_url, livekit_token, room_name)`.

**Note on correlation IDs:** `sessions.correlation_id` is **not** a stored column today — correlation flows via the `x-correlation-id` request header. We mint a fresh `correlation_id` at `/start` (uuid4), embed it in dispatch metadata, and bind it on every subsequent log line. If we later want a stable ID across the session row's lifetime, we add a column in a follow-up migration; not required for this round.

### 2.6 Engine entrypoint (preserves all existing plugins)

```python
@server.rtc_session(agent_name=engine_cfg.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    metadata = json.loads(ctx.job.metadata)
    structlog.contextvars.bind_contextvars(
        session_id=metadata["session_id"],
        correlation_id=metadata.get("correlation_id"),
    )

    config = await fetch_session_config(
        session_id=metadata["session_id"],
        jwt=metadata["engine_jwt"],
        base_url=engine_cfg.nexus_internal_base_url,
    )

    agent = InterviewerAgent(
        session_config=config,
        engine_config=engine_cfg,
        nexus_jwt=metadata["engine_jwt"],
        nexus_base_url=engine_cfg.nexus_internal_base_url,
    )

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],                       # Silero, prewarmed
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),           # MultilingualModel
            preemptive_generation={"enabled": False},
            endpointing={
                "min_delay": engine_cfg.endpointing_min_delay,
                "max_delay": engine_cfg.endpointing_max_delay,
            },
        ),
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=build_noise_cancellation(),  # ai_coustics
            ),
        ),
    )
```

`app/ai/realtime.py` exports five factories: `build_stt_plugin()`, `build_llm_plugin()`, `build_tts_plugin()`, `build_turn_detector()`, `build_noise_cancellation()`. All read from `AIConfig` (and through it, `settings.*`). The engine never imports `livekit.plugins.*` directly.

### 2.7 Failure-recovery posture

| Failure | Behavior |
|---|---|
| LiveKit dispatch raises | 502 `AGENT_DISPATCH_FAILED`. Token intact. Candidate retries. |
| Candidate-token race lost | Room created but no candidate. Best-effort `room.delete_room`; 409 `TOKEN_ALREADY_USED`. |
| Agent worker crashes mid-session | LiveKit auto-redispatches. Second entrypoint fetches config again, re-joins room, but state machine is rebuilt fresh. **Acknowledged limitation**, flagged for Phase 3D. Result post on the second worker is rejected by the idempotency guard. |
| `/results` POST fails | Engine retries 3× with exponential backoff. On final failure, writes `<session_id>.json` to `/tmp/interview_results/` (container-local). Logs CRITICAL `engine.fallback.local_results_write`. Session stays `active`; ops alert. |
| Candidate disconnects mid-session | LiveKit auto-reconnect window (~30s). State machine treats permanent disconnect as `candidate_disengaged` → CLOSE → results posted with `result_status='partial'`. |
| Agent never joins (worker pool down) | Frontend's 30s grace timeout fires `AGENT_NO_SHOW`. Backend mirrors via a Phase 3D cleanup job (not in this round). The session sits `active` until manual intervention; recruiter can resend invite. |

## 3 — Schema migration `0024_interview_engine_integration`

### 3.1 New table `engine_dispatch_tokens`

```sql
CREATE TABLE engine_dispatch_tokens (
    jti         UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX idx_engine_dispatch_tokens_session ON engine_dispatch_tokens(session_id);
ALTER TABLE engine_dispatch_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON engine_dispatch_tokens
  USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
CREATE POLICY "service_bypass" ON engine_dispatch_tokens
  USING (current_setting('app.bypass_rls', true) = 'true');
GRANT SELECT, INSERT, UPDATE ON engine_dispatch_tokens TO nexus_app;
```

### 3.2 New table `engine_token_uses`

No `tenant_id` (inherited from the parent `engine_dispatch_tokens.jti`). Service-bypass policy only. Tenant scope is enforced at the application layer via the JWT claim — `_assert_rls_completeness` is given a small "bypass-only" exception entry in code with a comment explaining why.

```sql
CREATE TABLE engine_token_uses (
    jti       UUID NOT NULL,
    endpoint  TEXT NOT NULL CHECK (endpoint IN ('config', 'results')),
    used_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_ip   INET,
    PRIMARY KEY (jti, endpoint),
    FOREIGN KEY (jti) REFERENCES engine_dispatch_tokens(jti) ON DELETE CASCADE
);
ALTER TABLE engine_token_uses ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_bypass" ON engine_token_uses
  USING (current_setting('app.bypass_rls', true) = 'true')
  WITH CHECK (current_setting('app.bypass_rls', true) = 'true');
GRANT SELECT, INSERT ON engine_token_uses TO nexus_app;
```

### 3.3 New columns on `sessions`

```sql
ALTER TABLE sessions
    ADD COLUMN raw_result_json     JSONB,
    ADD COLUMN transcript          JSONB,
    ADD COLUMN questions_asked     INT,
    ADD COLUMN probes_fired        INT,
    ADD COLUMN agent_completed_at  TIMESTAMPTZ,
    ADD COLUMN result_status       TEXT CHECK (result_status IN ('ok', 'partial', 'error')),
    ADD COLUMN error_code          TEXT;
```

`livekit_room_name` and `recording_s3_key` already exist (`models.py:571-572`), so they're not re-added. State CHECK constraint stays unchanged.

### 3.4 Down migration

Symmetric: drop the two tables and seven columns. The migration's docstring warns that down loses `raw_result_json` and `transcript` for completed sessions; the rollback runbook (CLAUDE.md mandates one) requires a backup export first.

### 3.5 ORM additions in `app/models.py`

Two new SQLAlchemy classes (`EngineDispatchToken`, `EngineTokenUse`) and seven columns added to the `Session` class. Idiomatic with existing patterns.

### 3.6 RLS startup-assertion update

`app/main.py::_assert_rls_completeness` adds `engine_dispatch_tokens` to its tenant-scoped allowlist. `engine_token_uses` is registered separately as bypass-only (new minor extension to the helper, with an inline comment naming it as the precedent).

## 4 — Engine refactor (`backend/interview_engine/`)

### 4.1 Deletions

- `interview_engine/models.py` — `SessionConfig`, `CompanyContext`, `CandidateContext` (with `email` field stripped on the lift), `StageConfig`, `QuestionConfig`, `SteeringObservation`, `TranscriptEntry`, `QuestionResult`, `SessionResult`, and the enums `StageType` / `StageDifficulty` / `AdvanceBehavior` move to `backend/nexus/app/modules/interview_runtime/schemas.py`.
- `interview_engine/config.py` shrinks. Survives:
  ```python
  class InterviewEngineConfig(BaseSettings):
      agent_name: str = "Dakota-1785"
      max_probes_per_question: int = 3
      time_warning_threshold: float = 0.8
      endpointing_min_delay: float = 0.5
      endpointing_max_delay: float = 6.0
      nexus_internal_base_url: str
      results_fallback_dir: Path = Path("/tmp/interview_results")
  ```
  Removed (move to `app/config.py` as `settings.*`, exposed via `AIConfig`): all model/voice/effort/key fields.
- `interview_engine/prompts/interviewer.txt` → `backend/nexus/prompts/v1/interview/interviewer.txt`.
- `interview_engine/context_loader.py::room_metadata` mode (it was a `NotImplementedError` stub anyway). Survives: `fixture` mode (engine pytest only) + `nexus_api` mode (production).

### 4.2 Refactors

- `agent.py` — entrypoint per Section 2.6. `load_dotenv(".env")` is removed (env comes from compose). Plugin instantiation moves to `app.ai.realtime` factories; VAD prewarm hook unchanged.
- `agents/interviewer.py` — constructor gains `nexus_jwt: str` and `nexus_base_url: str`. `record_observation` tool unchanged in shape. The end-of-session result-write path replaces the local file-write with a call to `nexus_client.post_session_result(...)`. Forensic-fallback path writes to `engine_cfg.results_fallback_dir` only on POST failure after retries.
- `prompt_builder.py` — single line: load via `prompt_loader.load("interview/interviewer")` from `app.ai.prompts`. `string.Template` substitution unchanged.
- `state_machine.py` — **untouched**.
- `context_loader.py` — collapsed to:
  ```python
  async def load_session_config(*, session_id=None, jwt=None, fixture_path=None) -> SessionConfig:
      if fixture_path:
          return SessionConfig.model_validate_json(fixture_path.read_text())
      return await fetch_session_config(session_id, jwt, settings.nexus_internal_base_url)
  ```

### 4.3 New `interview_engine/nexus_client.py`

Thin httpx client (~80 lines):

- `fetch_session_config(session_id, jwt, base_url) -> SessionConfig` — 3 retries on 5xx / network errors, exponential backoff (1s, 2s, 4s). 401/404/409/422 raise typed errors immediately (no retry).
- `post_session_result(session_id, jwt, result, base_url) -> None` — 3 retries on 5xx / network errors. 204 → success, 409 → idempotent success, 401/422 → `ResultRejectedError`.
- One httpx client per call for now; pooling is a future optimization.

### 4.4 Tests stay green

- Existing engine pytest runs against `fixture` mode unchanged.
- New `tests/test_nexus_client.py` mocks `httpx.AsyncClient` and verifies retry/error paths.

## 5 — Frontend live UI

### 5.1 Dependencies (frontend/app/package.json)

Add `livekit-client`, `@livekit/components-react`, `@livekit/components-styles`. Skip `@agents-ui/*` shadcn components (`frontend/app/CLAUDE.md`: there is no shadcn in this codebase).

### 5.2 Lazy-load discipline

Per `frontend/app/CLAUDE.md`: "LiveKit-bearing routes are exempt from the JS budget but must lazy-load the SDK." `<LiveSessionShell>` is dynamically imported with `next/dynamic` from `WizardShell.tsx`. Pre-check / consent / OTP / camera-mic do not pull in LiveKit.

### 5.3 New files

```
app/(interview)/interview/[token]/
├── WizardShell.tsx           # EXTENDED — when state==='active' + creds, render <LiveSessionShell>
├── StartStep.tsx             # EXTENDED — handles 200 with creds (was 501 placeholder)
└── LiveSession/
    ├── LiveSessionShell.tsx  # mounts <LiveKitRoom>, manages 30s grace timeout
    ├── AgentTile.tsx         # voice visualizer + agent state badge
    ├── CandidateSelfView.tsx # local camera + mic indicator + permission-loss blocker
    ├── ProgressBanner.tsx    # "Q3 of 9 · 11 min remaining"
    ├── TranscriptPane.tsx    # live transcript, role-labeled bubbles, expanded by default
    ├── CompletionScreen.tsx  # end-of-session screen
    ├── DisconnectError.tsx   # blocking error (mic/camera/agent/network)
    └── hooks/
        ├── use-agent-state.ts
        ├── use-agent-grace-timeout.ts
        └── use-stage-progress.ts

lib/api/candidate-session.ts   # EXTENDED — startSession() return type
```

### 5.4 Component flow

After Start succeeds, `WizardShell` renders `<LiveSessionShell creds={creds} />` which mounts:

```jsx
<LiveKitRoom serverUrl={url} token={token} connect audio video>
  <RoomAudioRenderer />          {/* auto-renders agent's audio */}
  <ProgressBanner />              {/* sticky, attribute-driven */}
  <main className="grid">
    <AgentTile />
    <CandidateSelfView />
  </main>
  <TranscriptPane />              {/* useChat() — built-in transcript hook */}
  <DisconnectButton />
</LiveKitRoom>
```

### 5.5 Hooks

- **`use-agent-grace-timeout`** — 30s timer on `LiveKitRoom` connect. Polls `useRemoteParticipants()` for an identity prefixed `agent-`. If unmet at t=30s, render `<DisconnectError code="AGENT_NO_SHOW" />` and disconnect.
- **`use-agent-state`** — wraps `useVoiceAssistant()` to derive `connecting | listening | thinking | speaking | disconnected`.
- **`use-stage-progress`** — reads `current_question_index`, `total_questions`, `time_remaining_seconds` from agent participant attributes. The agent sets these via `room.local_participant.set_attributes(...)` after each `record_observation` (small engine-side addition).

### 5.6 Permission-loss blocker

`CandidateSelfView` listens for camera/mic track-publication events. Involuntary mute → full-screen `<DisconnectError code="MEDIA_LOST" />`. The agent server-side keeps running; if reconnected within ~30s, candidate picks back up.

### 5.7 Completion screen

Minimal: company logo, "Thanks for completing your interview. You can close this tab." No replay, no nav.

### 5.8 Tests (Vitest)

Composition tests under `tests/components/interview/` mocking `@livekit/components-react` at the module boundary:

- `LiveSessionShell.test.tsx` — grace timeout fires after 30s with no agent present.
- `ProgressBanner.test.tsx` — attribute-driven render.
- `CompletionScreen.test.tsx` — copy + absence of nav links.
- `StartStep.test.tsx` (extended) — mocks new 200 response shape, asserts wizard transitions to active.

We do **not** test the LiveKit data plane in Vitest. Manual end-to-end run is the verification (Section 7.4).

## 6 — Security & observability

### 6.1 Engine JWT — defenses (recap of 2.2)

- Distinct secret `INTERVIEW_ENGINE_JWT_SECRET`, 32-byte random, env-only. Documented in `nexus/.env.example` with a "DB-credential-grade sensitivity" note. Rotation every 90 days, on personnel change, or on incident — per CLAUDE.md.
- Algorithm pinning, purpose claim, tenant_id claim, sub claim cross-checked with path.
- Single-use per (jti, endpoint) — atomic INSERT with conflict.
- Lifetime ≤ 90 minutes.

### 6.2 Internal-endpoint exposure

- JWT is the actual gate.
- Reverse proxy excludes `/api/internal/*` from public routing (operations-level, documented).
- Per-source-IP rate limit: 60/min on the family. **This is intentionally tighter than root CLAUDE.md's "600/min for authenticated"** — these endpoints are service-to-service from one container to another; legitimate traffic is exactly 1 call to `/config` and 1 to `/results` per session. (Per-jti rate is implicit in single-use.)
- Audit log on every call: `actor_id=NULL`, `actor_email=NULL`, `action='engine.config.fetch' | 'engine.result.record'`, `payload={"jti_prefix": "<first-8>"}` (engine is a non-human actor; the existing `audit_log` schema reserves NULL `actor_id` for this case — there is no `actor_type` column).
- 401 errors return only `{"code": "ENGINE_TOKEN_INVALID"}` — no PII, no claim leakage.

### 6.3 RLS / tenant scoping inside internal endpoints

- Both endpoints use `get_bypass_db()`; the engine has no Supabase user context.
- Despite bypass, every query in `interview_runtime/service.py` filters explicitly by `tenant_id = jwt.tenant_id`. Code-review rule: any query in this module that omits the explicit filter is a blocker.
- Org-unit ancestry walk for `company_profile` is tenant-scoped via the starting `org_unit_id` (existing helper).

### 6.4 LiveKit candidate access tokens

- Minted in `session/livekit.py::mint_candidate_lk_token()` using `livekit.api.AccessToken`.
- Identity: `candidate-{candidate.id}` (UUID, not email).
- Name claim: `candidate.name` — visible in the room participant list, the agent reads it for personalized greeting.
- Grants: `room_join`, `can_publish`, `can_subscribe`. **Not** `can_publish_data` for now.
- TTL: `stage.duration_minutes + 10`.
- Single-issuance: minted at `/start`. Refresh = re-mint = currently a 409 (no rejoin flow this round; flagged for Phase 3D).

### 6.5 PII discipline

- `CandidateContext` in the SessionConfig drops `email`. Engine never sees candidate email, phone, full address, or raw resume bytes.
- Self-hosted Langfuse only — `app/ai/client.py`'s `_is_langfuse_cloud_host()` guard already raises against cloud hosts; the realtime path inherits the same check.
- AIVIA / GDPR consent gate is already in place (`sessions.consent_recorded_at`) and is required before `/start` reaches the dispatch path.

### 6.6 Correlation IDs

- `sessions` does **not** carry a stored `correlation_id` column today. Correlation flows via the `x-correlation-id` request header per the existing JD pipeline pattern (`backend/nexus/app/modules/jd/router.py:50-65`).
- For this integration, `/start` mints a fresh `correlation_id = uuid4()` and embeds it in the dispatch metadata payload.
- The engine binds it via `structlog.contextvars.bind_contextvars(...)` at the top of the entrypoint. Every engine log line carries it for the session's lifetime.
- Engine HTTP calls to `/config` and `/results` set `x-correlation-id`. Nexus middleware reads it and binds for the duration of the request.
- Adding a stable session-row `correlation_id` column is a Phase 3D follow-up if reporting needs persistence.

### 6.7 Structured logging — engine

The engine bootstraps structlog via the shared `app.logging` config (existing module). Forbidden values in engine logs (per CLAUDE.md): candidate email, full transcript text, OTP codes, full JWT, signing keys. Allowed: session_id, candidate_id, correlation_id, jti_prefix (first 8), question_id, position_index, agent_state.

Specific events: `engine.dispatch.received`, `engine.config.fetched | fetch_failed`, `engine.session.started | completed`, `engine.result.posted | post_failed`, `engine.fallback.local_results_write` (CRITICAL).

### 6.8 Threat-model delta

`docs/security/threat-model.md` gains entries (creating that file in this PR if absent; CLAUDE.md mandates the same-PR creation rule):

| Boundary | New element | STRIDE | Mitigation |
|---|---|---|---|
| Public → LiveKit room | Candidate access token | T | TTL = stage_duration + 10min, single-use bound to consumed candidate session token |
| LiveKit → engine worker | Dispatch JWT in metadata | T | Single-use `engine_token_uses`, jti bound to session_id, 90min cap |
| Engine → Nexus internal API | HTTP w/ engine JWT | E, I | HS256 pinning, purpose claim, explicit-filter queries under bypass session, audit log on every call |
| Engine → OpenAI/Deepgram/Cartesia | Audio + transcript | I | Consent gate (existing), no email/raw-resume in prompts, self-hosted Langfuse only |

### 6.9 Out of scope

- mTLS engine ↔ Nexus (Phase 4 enterprise hardening).
- LiveKit webhook signature verification (we don't subscribe to webhooks until egress / recording lands).
- Recruiter live-observer auth surface (Phase 3D).

## 7 — Testing strategy

### 7.1 Backend pytest (under `backend/nexus/tests/`)

- `test_interview_runtime_authz.py` — 100% branch coverage on `verify_engine_token`. Cases: valid; replay; wrong endpoint vs same jti (must succeed); algorithm `none` / `RS256` / `ES256` rejection; wrong purpose; tenant mismatch; sub-vs-path mismatch; expired; tampered signature; missing header.
- `test_interview_runtime_config.py` — `build_session_config()` happy + reject paths: stage type allowlist; bank not ready or stale; cross-tenant; missing company profile; no candidate `email` in serialized output (negative-control assertion).
- `test_interview_runtime_results.py` — `record_session_result()` happy + idempotent re-post; state guard; cross-tenant guard; audit row written.
- `test_session_start_livekit.py` — happy `/start` returns the new 200 shape; LiveKit dispatch failure preserves token; race-condition test where the consume loses (room cleaned up); engine JWT claim shape.
- `test_session_state_machine_engine.py` — `active → completed` only via `record_session_result`; the `error_code='AGENT_NO_SHOW'` state path is reachable.

LiveKit API client is mocked via a `livekit_api_stub` fixture in `conftest.py` — no real LiveKit instance for pytest.

### 7.2 Engine pytest (under `backend/interview_engine/tests/`)

- Existing `state_machine` tests stay green.
- New `tests/test_nexus_client.py` — happy / 5xx-retry / 401-no-retry / network-error-then-success cases for `fetch_session_config` and `post_session_result`.

### 7.3 Frontend Vitest (Section 5.8)

- Composition tests with `@livekit/components-react` mocked at the module boundary.

### 7.4 Manual end-to-end runbook

`docs/onboarding/interview-engine-e2e.md` (new):

1. `cp backend/nexus/.env.example backend/nexus/.env`; fill `LIVEKIT_*`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `INTERVIEW_ENGINE_JWT_SECRET`, `CANDIDATE_JWT_SECRET`, `FRONTEND_BASE_URL`, `NEXUS_INTERNAL_BASE_URL=http://nexus:8000`.
2. `supabase start`.
3. `cd backend && docker compose up --build` — three containers boot: `nexus`, `nexus-worker`, `interview-engine`.
4. Verify `interview-engine` logs: `engine.worker.registered agent_name=Dakota-1785`.
5. Frontend: `cd frontend/app && npm install && npm run dev`.
6. Create job → confirm signals → wait for question bank ready.
7. Add candidate, send invite, open Inbucket (`http://localhost:54324`) for the candidate link.
8. Walk Consent → OTP → Camera/Mic → Start.
9. **Verify:** within ~3s of Start, the candidate hears the agent greeting; question banner advances; transcript populates; agent reaches CLOSE; completion screen renders.
10. **Verify Postgres:** `SELECT state, questions_asked, probes_fired, agent_completed_at, jsonb_array_length(transcript) FROM sessions WHERE id = ?` — state=completed, transcript populated.
11. **Verify Langfuse:** trace appears with `correlation_id`.

### 7.5 Coverage gates (per CLAUDE.md)

- 100% branch on `verify_engine_token` and the new `/start` flow.
- 80% line coverage project-wide.
- New tenant-scoped table (`engine_dispatch_tokens`) — cross-tenant read returns 0 rows. Verified in test.

### 7.6 Out of scope

- Load test (concurrency targets — Phase 3D scoring).
- Chaos / fault injection.
- Tool-input fuzzing.
- Full a11y audit.

## 8 — Build sequence

| Chunk | Goal | Done means |
|---|---|---|
| 1. Schema + ORM + RLS check | Tables and columns exist; startup assertion passes; no functional change. | `docker compose up` boots; existing tests green. |
| 2. `app/ai/realtime.py` + `AIConfig` extension + prompt move | Factories exist; no observable change to JD / question bank. | Existing tests green. |
| 3. Internal API (`interview_runtime`) | `/api/internal/sessions/{id}/{config,results}` live and authed by engine JWT. Engine doesn't exist yet. | Pytest green; manual `curl` with hand-minted JWT returns SessionConfig. |
| 4. `/start` LiveKit provisioning | Nexus `/start` returns 200 with creds; LiveKit creates the room; the dispatch is queued but no worker exists to pick it up yet. The candidate's browser would join the room and see no agent. (The frontend grace timeout doesn't ship until Chunk 6, so manual verification of Chunk 4 happens via `curl` + LiveKit logs, not via the wizard.) | Pytest green; `curl /api/sessions/candidate/{token}/start` returns 200 with `{livekit_url, livekit_token, room_name}`; LiveKit's API confirms the room exists and the dispatch was queued. |
| 5. Engine refactor | Engine boots, registers as worker, fetches config, runs an actual interview, posts results. | First end-to-end manual run succeeds. |
| 6. Frontend live UI | Section 5 in full. | Candidate completes a screening end-to-end; tests green. |
| 7. Docs + threat model + runbook | All CLAUDE.md-mandated docs in place. Phase 3C.2 marked done. | New engineer can clone + run the full pipeline. |

Chunks 1–4 can ship as one PR (backend-only) without user-visible change beyond the 501 → "hanging dispatch" degradation. Chunks 5 and 6 each ship independently. Chunk 7 bundles docs at the end (or folds into 3 + 5 inline if a reviewer prefers).

## 9 — Decisions deferred

| Topic | Why deferred | Owner phase |
|---|---|---|
| Server-side recording (egress to S3) | Keeps blast radius small this round; transcript persistence covers reporting's text needs | Phase 3D |
| Mid-session reconnect (refresh-tab rejoin) | Out of scope; first refresh hits existing 409 | Phase 3D |
| Stable session `correlation_id` column | Header-flow is sufficient for now | Future, only if reporting needs it |
| 2×2 video grid + AI Copilot panel | Section 4 brainstorming Q4 — Option A scope | Phase 3D / panel mode |
| Recruiter live observer view | Tied to AI Copilot panel | Phase 3D |
| Normalized observation tables | YAGNI until Phase 3D's scoring queries are concrete | Phase 3D |
| LiveKit webhook handler (e.g. egress_ended) | We don't subscribe to webhooks this round | Phase 3D when egress lands |
| Multi-worker pool / autoscale | Single replica in compose for now; LiveKit's dispatch round-robin handles N>1 transparently | When concurrency demands |

## 10 — File-level change summary (for the writing-plans hand-off)

**New files**
- `backend/nexus/migrations/versions/0024_interview_engine_integration.py`
- `backend/nexus/app/ai/realtime.py`
- `backend/nexus/app/modules/interview_runtime/{__init__,router,service,schemas,authz}.py`
- `backend/nexus/app/modules/session/livekit.py`
- `backend/nexus/prompts/v1/interview/interviewer.txt` (moved from engine)
- `backend/nexus/tests/test_interview_runtime_authz.py`
- `backend/nexus/tests/test_interview_runtime_config.py`
- `backend/nexus/tests/test_interview_runtime_results.py`
- `backend/nexus/tests/test_session_start_livekit.py`
- `backend/interview_engine/nexus_client.py`
- `backend/interview_engine/tests/test_nexus_client.py`
- `frontend/app/app/(interview)/interview/[token]/LiveSession/{LiveSessionShell,AgentTile,CandidateSelfView,ProgressBanner,TranscriptPane,CompletionScreen,DisconnectError}.tsx`
- `frontend/app/app/(interview)/interview/[token]/LiveSession/hooks/{use-agent-state,use-agent-grace-timeout,use-stage-progress}.ts`
- `frontend/app/tests/components/interview/{LiveSessionShell,ProgressBanner,CompletionScreen}.test.tsx`
- `docs/security/threat-model.md` (delta or new file)
- `docs/security/key-rotation-runbook.md` (new file with `INTERVIEW_ENGINE_JWT_SECRET` + `LIVEKIT_API_SECRET` entries)
- `docs/onboarding/interview-engine-e2e.md`

**Edited files**
- `backend/nexus/app/main.py` — register `interview_runtime_router` under `/api/internal/*`; `_assert_rls_completeness` allowlist updates.
- `backend/nexus/app/config.py` — new fields: `interview_llm_model`, `stt_*`, `tts_*`, `interview_reasoning_effort`, `interview_engine_jwt_secret`, `interview_agent_name`.
- `backend/nexus/app/ai/config.py` — new properties exposing the above.
- `backend/nexus/app/models.py` — `EngineDispatchToken`, `EngineTokenUse`, seven new columns on `Session`.
- `backend/nexus/app/modules/auth/service.py` — `verify_engine_token()`.
- `backend/nexus/app/modules/session/router.py` — `/start` decorator status_code 501 → 200; new response model.
- `backend/nexus/app/modules/session/service.py` — `start_session()` rewrite.
- `backend/nexus/app/modules/session/schemas.py` — new `StartSessionResponse`, deprecate `StartSessionPendingResponse`.
- `backend/nexus/.env.example` — `INTERVIEW_ENGINE_JWT_SECRET`, `INTERVIEW_AGENT_NAME`, `INTERVIEW_LLM_MODEL`, `STT_*`, `TTS_*`.
- `backend/nexus/CLAUDE.md` — new module entry; `app/ai/realtime.py` carve-out; migration 0024.
- `backend/interview_engine/{pyproject.toml,Dockerfile,agent.py,config.py,context_loader.py,prompt_builder.py,agents/interviewer.py}`.
- `backend/interview_engine/AGENTS.md` — describes the new architecture.
- `backend/docker-compose.yml` — `interview-engine` service.
- `frontend/app/package.json` — `livekit-client`, `@livekit/components-react`, `@livekit/components-styles`.
- `frontend/app/lib/api/candidate-session.ts` — `startSession()` return shape.
- `frontend/app/app/(interview)/interview/[token]/{WizardShell,StartStep}.tsx`.
- `frontend/app/CLAUDE.md` — note Phase 3 client deps installed.
- `CLAUDE.md` (root) — phase status table: 3C.2 done.

**Deleted files**
- `backend/interview_engine/models.py`
- `backend/interview_engine/prompts/interviewer.txt`

## 11 — Open questions for spec review

None at the time of writing — all seven brainstorming questions resolved, the three Section-2 flags accepted, the Section-3 `engine_token_uses` no-tenant_id design accepted, the Section-5 transcript-default-on / lazy-import accepted, the Section-6 container-local fallback accepted.

If review surfaces a concern, the most likely revision sites are: (a) tightening Section 2.5's failure-recovery posture if the "agent-no-show" path needs an explicit cleanup endpoint sooner than Phase 3D, (b) electing to add a stored `correlation_id` column on `sessions` now if observability tooling demands it, and (c) trimming `app/ai/realtime.py`'s factory surface if VAD / turn-detector / noise-cancellation belong outside `app/ai/` by convention.
