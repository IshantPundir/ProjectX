# Self-Hosted LiveKit (SFU + Egress) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LiveKit Cloud with a self-hosted LiveKit SFU + Egress service (local dev now, production path documented), permanently fixing the egress transcode-minute quota and giving full control over transport scaling and security.

**Architecture:** Add three infrastructure services to local docker-compose — `livekit-server` (SFU), `livekit-egress` (Chrome+GStreamer recorder), and a dedicated `livekit-redis` (the bus they coordinate over), all on host networking. The application's recording code is unchanged in behavior: auto-egress (`CreateRoom.egress`) still uploads one MP4 to Cloudflare R2 via the S3 protocol. The only code change is hardening the recording-reconcile path. Config is env-driven so dev defaults to self-hosted while LiveKit Cloud stays a one-env-var fallback.

**Tech Stack:** LiveKit OSS (`livekit/livekit-server`, `livekit/egress`), Redis, Docker Compose (host networking, `--cap-add=SYS_ADMIN`), FastAPI/SQLAlchemy/pytest (backend), Next.js/zod/Vitest (frontend session), Cloudflare R2 (unchanged sink).

**Spec:** `docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md`

---

## Background the engineer must know

- **Why this exists:** the free LiveKit Cloud "Build" plan caps Egress at 60 transcode-minutes/month (hard cap, ~4 of our 15-min interviews). Self-hosting removes the cap. A self-hosted Egress service can only talk to a self-hosted SFU (they share a Redis that Cloud doesn't expose), so we self-host both.
- **What does NOT change:** `app/modules/session/livekit.py` (`build_room_egress`, `create_room`, `dispatch_agent`), `app/storage/`, the report viewer, the engine `agent.py`, the audio path. The recording is requested identically; only *where* LiveKit runs changes.
- **The recording sink stays Cloudflare R2.** Our `build_room_egress` already passes the S3 upload config **per-request** (`_build_recording_file_output` → `S3Upload(... endpoint, force_path_style ...)`). Because the per-request config overrides the egress service's own file-upload config, the **egress service config needs NO S3/R2 credentials** — they stay in the backend `.env`. R2 is an officially supported egress S3 target (`force_path_style: true` required, which our code already sets).
- **Two Redises, kept separate:** the existing Dramatiq broker Redis (`redis` service, host port 6379) is untouched. LiveKit gets its own `livekit-redis` on host port **6380**.
- **Host networking (Linux):** `livekit-server`, `livekit-egress`, and `livekit-redis` all run with `network_mode: host`. The candidate browser (on the host) reaches the SFU at `ws://localhost:7880`; the engine container (bridged, with `host.docker.internal`) reaches it at `ws://host.docker.internal:7880`. These are already two separate config fields (`livekit_public_url` vs `livekit_url`).

## File structure (created / modified)

**Backend (`backend/nexus/`):**
- Modify: `app/config.py` — add `recording_stuck_timeout_seconds`.
- Modify: `app/modules/session/recording.py` — reconcile hardening (R2 fallback + stuck timeout).
- Modify: `tests/test_session_recording.py` — tests for the new branches.
- Create: `config/livekit/livekit.yaml` — SFU config.
- Create: `config/livekit/egress.yaml` — Egress service config (no S3 block).
- Create: `docker-compose.livekit.yml` — the LiveKit plane (server + egress + redis).
- Modify: `.env.example` — self-hosted LiveKit guidance.

**Frontend (`frontend/session/`):**
- Modify: `proxy.ts` — env-driven LiveKit connect-src origin (prod-readiness; dev already works).
- Modify: `lib/env.ts` — optional `NEXT_PUBLIC_LIVEKIT_WS_URL`.
- Modify: `tests/lib/env.test.ts` + a `proxy` CSP test — assert the origin handling.

**Docs:**
- Create: `docs/deployment/2026-06-09-self-hosted-livekit-deployment.md` — production path (Tier 1 VM+Caddy, Tier 2 EKS+Helm).

---

## Part 1 — Backend recording reconcile hardening (TDD)

### Task 1: Add the stuck-timeout setting

**Files:**
- Modify: `backend/nexus/app/config.py:280` (in the recording settings block, after `recording_key_prefix`)

- [ ] **Step 1: Add the setting**

In `app/config.py`, immediately after the `recording_key_prefix` line (currently line 280), add:

```python
    # When LiveKit reports no egress for a completed session (egress never
    # started — e.g. a quota rejection — or LiveKit purged the record) AND the
    # recording is absent from object storage, a still-"recording" row is
    # advanced to "failed" once this many seconds have elapsed since
    # agent_completed_at. Prevents an eternal "processing" spinner on the
    # report page. See docs/.../2026-06-09-self-hosted-livekit-egress-design.md §4.2.
    recording_stuck_timeout_seconds: int = 900
```

- [ ] **Step 2: Verify config still loads**

Run: `docker compose run --rm nexus python -c "from app.config import settings; print(settings.recording_stuck_timeout_seconds)"`
Expected: prints `900`

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/config.py
git commit -m "feat(recording): add recording_stuck_timeout_seconds setting"
```

---

### Task 2: R2 fallback — mark `ready` when the egress record is gone but the MP4 exists

**Files:**
- Modify: `backend/nexus/app/modules/session/recording.py`
- Test: `backend/nexus/tests/test_session_recording.py`

**Context:** Today `_reconcile` returns silently when `get_recording_status` yields `None` (no egress for the room). LiveKit purges old egress records, and a quota-rejected egress never creates one — but in the success case the MP4 is still in R2. We resolve from R2 directly. `ObjectStorage.head(key)` already exists (`app/storage/base.py:43`) and returns `ObjectMeta | None` with `.size_bytes`. The deterministic key is `recording_object_key(tenant_id=..., session_id=...)` from `app/modules/session/livekit.py`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_session_recording.py`. First extend the `fake_storage` fixture to also mock `head` (replace the existing fixture body):

```python
@pytest.fixture
def fake_storage(monkeypatch):
    storage = MagicMock()
    storage.presign_get_url = AsyncMock(return_value="https://signed.example/v.mp4?sig=x")
    storage.head = AsyncMock(return_value=None)
    monkeypatch.setattr(recording_mod, "get_object_storage", lambda: storage)
    return storage
```

Then add the test:

```python
async def test_no_egress_but_object_in_storage_marks_ready(db, fake_storage, monkeypatch):
    """Egress record gone (purged or never created) but the MP4 exists in R2 →
    resolve to ready from storage, not an eternal spinner."""
    from app.storage.base import ObjectMeta

    tenant_id, session_id = await _seed_session(db, recording_status="recording")
    monkeypatch.setattr(
        recording_mod, "get_recording_status", AsyncMock(return_value=None)
    )
    fake_storage.head = AsyncMock(
        return_value=ObjectMeta(key="k", size_bytes=98765, content_type="video/mp4")
    )

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "ready"
    assert out.signed_url == "https://signed.example/v.mp4?sig=x"
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "ready"
    assert row.recording_bytes == 98765
    assert row.recording_ready_at is not None
    assert row.recording_s3_key is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/test_session_recording.py::test_no_egress_but_object_in_storage_marks_ready -v`
Expected: FAIL — status is `"recording"`, not `"ready"` (current code returns on `snap is None`).

- [ ] **Step 3: Implement the R2 fallback**

In `app/modules/session/recording.py`, add the import near the top (with the other `session.livekit` import on line 28):

```python
from app.modules.session.livekit import get_recording_status, recording_object_key
```

Replace the `if snap is None:\n        return` block inside `_reconcile` (currently lines 102-103) with:

```python
    if snap is None:
        await _reconcile_without_egress(db, sess)
        return
```

Then add this new helper directly above `_reconcile`:

```python
async def _reconcile_without_egress(db: AsyncSession, sess: Session) -> None:
    """Resolve a still-"recording" row when LiveKit has no egress record.

    Either the egress never started (e.g. a quota rejection) or LiveKit purged
    a finished egress. The recording in object storage is the authoritative
    ground truth, so check it directly; only when it's genuinely absent do we
    apply the stuck-timeout. Best-effort — never raises into the playback path.
    """
    key = recording_object_key(tenant_id=sess.tenant_id, session_id=sess.id)
    try:
        meta = await get_object_storage().head(key)
    except Exception:  # noqa: BLE001 — storage hiccup leaves the row for next read
        log.warning("recording.storage_head_failed", session_id=str(sess.id), exc_info=True)
        return
    if meta is not None:
        sess.recording_status = "ready"
        sess.recording_ready_at = datetime.now(UTC)
        sess.recording_s3_key = key
        sess.recording_bytes = meta.size_bytes
        await db.flush()
        return
    # Object truly absent — handled by the stuck-timeout in Task 3.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `docker compose run --rm nexus pytest tests/test_session_recording.py::test_no_egress_but_object_in_storage_marks_ready -v`
Expected: PASS

- [ ] **Step 5: Run the full recording suite (no regressions)**

Run: `docker compose run --rm nexus pytest tests/test_session_recording.py -v`
Expected: all PASS (the existing `test_reconcile_swallows_livekit_errors` still passes — the exception path returns before reaching `snap is None`).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/session/recording.py backend/nexus/tests/test_session_recording.py
git commit -m "feat(recording): resolve recording from object storage when egress record is absent"
```

---

### Task 3: Stuck-timeout — mark `failed` when there is no egress and no object past the grace window

**Files:**
- Modify: `backend/nexus/app/modules/session/recording.py`
- Test: `backend/nexus/tests/test_session_recording.py`

**Context:** When there's no egress record AND no object in storage, the row would otherwise spin on `"recording"` forever (the quota symptom). After `recording_stuck_timeout_seconds` past `agent_completed_at`, fail it honestly. `agent_completed_at` is a tz-aware column on `Session` (`models.py:86`).

- [ ] **Step 1: Write the failing tests**

First, extend the `_seed_session` helper to accept `agent_completed_at` (add the kwarg + assignment). Replace the helper signature/body header:

```python
async def _seed_session(
    db, *, recording_status, egress_id=None, key=None, transcript=None,
    agent_completed_at=None,
):
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)
    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
        state=SessionState.COMPLETED.value,
        livekit_room_name="session-test",
        recording_status=recording_status,
        recording_egress_id=egress_id,
        recording_s3_key=key,
        transcript=transcript,
        agent_completed_at=agent_completed_at,
    )
    db.add(sess)
    await db.flush()
    return tenant.id, sess.id
```

Then add the two tests:

```python
async def test_no_egress_no_object_past_grace_marks_failed(db, fake_storage, monkeypatch):
    from datetime import UTC, datetime, timedelta

    completed = datetime.now(UTC) - timedelta(seconds=3600)
    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", agent_completed_at=completed
    )
    monkeypatch.setattr(recording_mod, "get_recording_status", AsyncMock(return_value=None))
    fake_storage.head = AsyncMock(return_value=None)

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "failed"
    assert out.signed_url is None
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "failed"


async def test_no_egress_no_object_within_grace_stays_recording(db, fake_storage, monkeypatch):
    from datetime import UTC, datetime

    tenant_id, session_id = await _seed_session(
        db, recording_status="recording", agent_completed_at=datetime.now(UTC)
    )
    monkeypatch.setattr(recording_mod, "get_recording_status", AsyncMock(return_value=None))
    fake_storage.head = AsyncMock(return_value=None)

    out = await recording_mod.get_session_recording_playback(
        db, session_id=session_id, tenant_id=tenant_id
    )

    assert out.status == "recording"
    row = await db.get(SessionRow, session_id)
    assert row.recording_status == "recording"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/test_session_recording.py::test_no_egress_no_object_past_grace_marks_failed -v`
Expected: FAIL — status is `"recording"`, not `"failed"`.

- [ ] **Step 3: Implement the stuck-timeout**

In `app/modules/session/recording.py`, replace the trailing comment line `# Object truly absent — handled by the stuck-timeout in Task 3.` (end of `_reconcile_without_egress`) with:

```python
    if _recording_stuck_expired(sess):
        sess.recording_status = "failed"
        await db.flush()


def _recording_stuck_expired(sess: Session) -> bool:
    """True once the grace window past agent_completed_at has elapsed.

    Returns False when the session never recorded a completion timestamp — we
    don't fail a recording we can't time.
    """
    completed = sess.agent_completed_at
    if completed is None:
        return False
    grace = timedelta(seconds=settings.recording_stuck_timeout_seconds)
    return (datetime.now(UTC) - completed) > grace
```

(`datetime`, `UTC`, `timedelta`, and `settings` are already imported at the top of the file.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/test_session_recording.py -v`
Expected: all PASS (both new tests + the Task 2 test + all pre-existing tests).

- [ ] **Step 5: Lint**

Run: `docker compose run --rm nexus ruff check app/modules/session/recording.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/session/recording.py backend/nexus/tests/test_session_recording.py
git commit -m "feat(recording): fail stuck recordings past the grace window (no egress, no object)"
```

---

## Part 2 — Local LiveKit plane (infra; verified by smoke test)

### Task 4: SFU config file

**Files:**
- Create: `backend/nexus/config/livekit/livekit.yaml`

- [ ] **Step 1: Create the SFU config**

```yaml
# Self-hosted LiveKit SFU — LOCAL DEV config.
# Runs with host networking (see docker-compose.livekit.yml). Not for production;
# the production path uses the livekit/generate tool + Caddy (see
# docs/deployment/2026-06-09-self-hosted-livekit-deployment.md).
port: 7880
log_level: info
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  # Local dev on one machine: no STUN/external-IP discovery needed.
  use_external_ip: false
redis:
  # Shared with the Egress service. Dedicated LiveKit Redis on host port 6380,
  # kept separate from the Dramatiq broker Redis (6379).
  address: 127.0.0.1:6380
keys:
  # Must match LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the backend .env.
  # Dev-only credentials — never reuse in any shared/prod environment.
  devkey: devsecret_change_me_32chars_minimum_ok
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/config/livekit/livekit.yaml
git commit -m "chore(livekit): add local dev SFU config"
```

---

### Task 5: Egress service config file

**Files:**
- Create: `backend/nexus/config/livekit/egress.yaml`

**Context:** No `s3:` block — the backend supplies the R2 `S3Upload` config per-request in `build_room_egress`, which overrides the service default. R2 credentials therefore stay in the backend `.env`, never in this file.

- [ ] **Step 1: Create the egress config**

```yaml
# Self-hosted LiveKit Egress service — LOCAL DEV config.
# Records RoomComposite (Chrome + GStreamer). Runs with host networking +
# --cap-add=SYS_ADMIN (see docker-compose.livekit.yml).
#
# NO s3/azure/gcp block here: the backend's build_room_egress passes the
# Cloudflare R2 S3Upload config per-request, which overrides the service
# default. R2 credentials live in the backend .env only.
log_level: debug
api_key: devkey
api_secret: devsecret_change_me_32chars_minimum_ok
ws_url: ws://127.0.0.1:7880
# Local non-TLS server.
insecure: true
redis:
  # Same Redis as the SFU (required — egress coordinates with the server over it).
  address: 127.0.0.1:6380
# Prometheus metrics endpoint (livekit_egress_available) for prod autoscaling.
prometheus_port: 9090
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/config/livekit/egress.yaml
git commit -m "chore(livekit): add local dev Egress service config"
```

---

### Task 6: LiveKit plane docker-compose file

**Files:**
- Create: `backend/nexus/docker-compose.livekit.yml`

**Context:** This is a *separate* compose file (mirrors the prod boundary — LiveKit deploys to entirely different infra). Bring the LiveKit plane up alongside the app with `-f docker-compose.yml -f docker-compose.livekit.yml`. All three services use host networking on Linux. Egress requires `cap_add: [SYS_ADMIN]` (v1.7.6+) or RoomComposite fails with `chrome failed to start`.

- [ ] **Step 1: Create the compose file**

```yaml
# LiveKit plane — self-hosted SFU + Egress + their coordination Redis.
# LOCAL DEV only. Linux host networking. Bring up with:
#   docker compose -f docker-compose.yml -f docker-compose.livekit.yml up -d
#
# Pin image tags deliberately (never :latest in committed config).
services:
  livekit-redis:
    image: redis:7-alpine
    network_mode: host
    restart: unless-stopped
    # Dedicated LiveKit bus on 6380 so it never collides with the Dramatiq
    # broker Redis (6379). protected-mode off for local host-network access.
    command: redis-server --port 6380 --protected-mode no
    healthcheck:
      test: ["CMD", "redis-cli", "-p", "6380", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  livekit-server:
    image: livekit/livekit-server:v1.9.1
    network_mode: host
    restart: unless-stopped
    command: --config /etc/livekit/livekit.yaml
    volumes:
      - ./config/livekit/livekit.yaml:/etc/livekit/livekit.yaml:ro
    depends_on:
      livekit-redis:
        condition: service_healthy

  livekit-egress:
    image: livekit/egress:v1.9.0
    network_mode: host
    restart: unless-stopped
    # Required since egress v1.7.6 — Chrome sandbox setup. Without it,
    # RoomComposite/web egress fail with "chrome failed to start".
    cap_add:
      - SYS_ADMIN
    environment:
      - EGRESS_CONFIG_FILE=/etc/livekit/egress.yaml
    volumes:
      - ./config/livekit/egress.yaml:/etc/livekit/egress.yaml:ro
    depends_on:
      livekit-redis:
        condition: service_healthy
```

- [ ] **Step 2: Validate the merged compose parses**

Run: `cd backend/nexus && docker compose -f docker-compose.yml -f docker-compose.livekit.yml config >/dev/null && echo OK`
Expected: prints `OK` (no YAML/merge errors). If the pinned image tags don't exist, run `docker pull livekit/livekit-server:v1.9.1` / `docker pull livekit/egress:v1.9.0` to confirm and adjust to the current stable tags (check `mcp__livekit-docs__get_changelog` or Docker Hub).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/docker-compose.livekit.yml
git commit -m "feat(livekit): add self-hosted SFU + Egress + Redis compose plane"
```

---

### Task 7: Backend env guidance for self-hosted LiveKit

**Files:**
- Modify: `backend/nexus/.env.example` (LiveKit block, lines ~63-75)

- [ ] **Step 1: Replace the LiveKit block in `.env.example`**

Replace the existing LiveKit comment+vars block (currently lines 63-75) with:

```bash
# LiveKit — SELF-HOSTED (default). The SFU + Egress run via
# docker-compose.livekit.yml. See docs/.../2026-06-09-self-hosted-livekit-egress-design.md.
#
# LIVEKIT_URL — backend/engine -> SFU (server-to-server: token mint, dispatch,
#   room create/delete). The engine container is bridged, so it reaches the
#   host-networked SFU via host.docker.internal.
# LIVEKIT_PUBLIC_URL — handed to the candidate browser in /start. The browser
#   runs on the host, so it uses localhost.
# LIVEKIT_API_KEY / SECRET — MUST match the `keys:` entry in
#   config/livekit/livekit.yaml (and api_key/api_secret in egress.yaml).
#
# To use LiveKit Cloud instead (fallback): set LIVEKIT_URL + LIVEKIT_PUBLIC_URL
# to wss://<project>.livekit.cloud and the keys to your Cloud project keys, and
# do NOT start the docker-compose.livekit.yml plane.
LIVEKIT_URL=ws://host.docker.internal:7880
LIVEKIT_PUBLIC_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret_change_me_32chars_minimum_ok
```

- [ ] **Step 2: Add the stuck-timeout to the recording block**

After the `RECORDING_EGRESS_PRESET=H264_720P_30` line (currently line 105), add:

```bash
# Fail a stuck "recording" row this many seconds after agent_completed_at when
# LiveKit has no egress record AND no object exists in storage (avoids an
# eternal "processing" spinner). See recording.py::_recording_stuck_expired.
RECORDING_STUCK_TIMEOUT_SECONDS=900
```

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/.env.example
git commit -m "docs(env): document self-hosted LiveKit + recording stuck timeout"
```

---

### Task 8: End-to-end smoke test (manual — the real acceptance gate)

**Files:** none (verification only). Record the outcome in the PR/commit description.

> Infra can't be unit-tested. This checklist is the acceptance gate. The local
> ICE path is the fiddly part — if media (not signaling) fails, see Step 6.

- [ ] **Step 1: Update your local `.env`** to the self-hosted values from Task 7 (`LIVEKIT_URL`, `LIVEKIT_PUBLIC_URL`, `LIVEKIT_API_KEY=devkey`, `LIVEKIT_API_SECRET=...` matching `livekit.yaml`). Keep `RECORDING_STORAGE_*` pointed at R2 as today.

- [ ] **Step 2: Bring up the LiveKit plane**

Run: `cd backend/nexus && docker compose -f docker-compose.yml -f docker-compose.livekit.yml up -d livekit-redis livekit-server livekit-egress`
Then: `docker compose logs livekit-server | tail -20`
Expected: server logs `starting LiveKit server` and binds `:7880`; no Redis connection errors.

- [ ] **Step 3: Confirm the SFU + egress are healthy**

Run: `curl -sf http://localhost:7880 && echo " <- SFU up"`
Expected: a response (LiveKit returns a 200/404 on `/`, not a hang).
Run: `curl -s http://localhost:9090/metrics | grep livekit_egress_available`
Expected: a line like `livekit_egress_available 1` (egress registered and idle-available).
Run: `docker compose logs livekit-egress | grep -i "starting egress\|registered"`
Expected: egress registered with the server (no `chrome failed to start`).

- [ ] **Step 4: Restart the engine so it registers with the self-hosted SFU**

Run: `docker compose up -d --force-recreate nexus nexus-engine`
Then: `docker compose logs nexus-engine | grep -i "registered worker\|livekit"`
Expected: the engine worker registers against `ws://host.docker.internal:7880` (not a `*.livekit.cloud` URL).

- [ ] **Step 5: Run one full interview end-to-end**

Drive a candidate session from `frontend/session` (port 3002) to completion as you normally do for agent talk-tests.
Expected: audio/video both directions; the interview completes normally.

- [ ] **Step 6: If media fails but signaling connects** (candidate joins but no audio/video)

This is local ICE. In `config/livekit/livekit.yaml` set `rtc.use_external_ip: true`, or add `rtc.node_ip: 127.0.0.1` for a single-machine setup; `docker compose -f docker-compose.yml -f docker-compose.livekit.yml up -d --force-recreate livekit-server` and retry Step 5. Record which setting worked.

- [ ] **Step 7: Confirm the recording landed in R2 and plays back**

After the session ends, open the recruiter report page for that session.
Expected: the recording player shows the video (not a stuck spinner). Confirm the egress ran: `docker compose logs livekit-egress | grep -i "egress.*complete\|uploaded"`. Optionally confirm the object exists in R2 at `recordings/<tenant_id>/<session_id>.mp4`.

- [ ] **Step 8: Record the result**

Write the smoke-test outcome (pass + any ICE setting needed) into the implementation PR/commit description. No code commit for this task.

---

## Part 3 — Frontend prod-readiness (env-driven CSP origin)

### Task 9: Parameterize the LiveKit connect-src origin

**Files:**
- Modify: `frontend/session/proxy.ts:50`
- Modify: `frontend/session/lib/env.ts`
- Test: `frontend/session/tests/lib/env.test.ts` (and a small proxy CSP assertion)

**Context:** The **dev** CSP already permits `ws://localhost:*` (`proxy.ts:50`), so local self-hosted LiveKit works today with no change. The remaining work is **prod-readiness**: replace the hardcoded `wss://*.livekit.cloud https://*.livekit.cloud` with an env-driven origin that defaults to those Cloud wildcards (back-compat) and can be set to a self-hosted `wss://livekit.<domain>`. This is a Human-Review-Required file (CSP boundary) — keep the change minimal and additive.

- [ ] **Step 1: Add the optional env var to the schema**

In `lib/env.ts`, add to `envSchema` (after `NEXT_PUBLIC_PROCTORING_DEBUG`):

```typescript
  // WebSocket origin(s) for the LiveKit SFU, used in the CSP connect-src.
  // Defaults to the LiveKit Cloud wildcards for back-compat; set to the
  // self-hosted origin in production, e.g. "wss://livekit.example.com".
  NEXT_PUBLIC_LIVEKIT_WS_URL: z
    .string()
    .optional()
    .transform((v) => v && v.length > 0 ? v : 'wss://*.livekit.cloud https://*.livekit.cloud'),
```

And add to the `envSchema.parse({...})` call:

```typescript
  NEXT_PUBLIC_LIVEKIT_WS_URL: process.env.NEXT_PUBLIC_LIVEKIT_WS_URL,
```

- [ ] **Step 2: Write the failing env test**

In `tests/lib/env.test.ts`, add:

```typescript
it('defaults LIVEKIT_WS_URL to the cloud wildcards when unset', () => {
  const parsed = envSchema.parse({ NEXT_PUBLIC_API_URL: 'https://api.example.com' })
  expect(parsed.NEXT_PUBLIC_LIVEKIT_WS_URL).toBe('wss://*.livekit.cloud https://*.livekit.cloud')
})

it('uses a provided self-hosted LIVEKIT_WS_URL', () => {
  const parsed = envSchema.parse({
    NEXT_PUBLIC_API_URL: 'https://api.example.com',
    NEXT_PUBLIC_LIVEKIT_WS_URL: 'wss://livekit.example.com',
  })
  expect(parsed.NEXT_PUBLIC_LIVEKIT_WS_URL).toBe('wss://livekit.example.com')
})
```

- [ ] **Step 3: Run the env test to verify it fails**

Run: `cd frontend/session && npm run test -- tests/lib/env.test.ts`
Expected: FAIL — `NEXT_PUBLIC_LIVEKIT_WS_URL` is undefined (not yet wired into `parse`).

- [ ] **Step 4: Confirm Step 1 wiring makes it pass**

Run: `cd frontend/session && npm run test -- tests/lib/env.test.ts`
Expected: PASS (Step 1 already added the schema + parse entry; if it still fails, ensure both edits in Step 1 were applied).

- [ ] **Step 5: Use the env origin in the CSP**

In `proxy.ts`, replace the `connect-src` line (line 50):

```typescript
    `connect-src 'self' ${apiUrl}${isDev ? " ws://localhost:*" : ""} wss://*.livekit.cloud https://*.livekit.cloud`,
```

with:

```typescript
    `connect-src 'self' ${apiUrl}${isDev ? " ws://localhost:*" : ""} ${process.env.NEXT_PUBLIC_LIVEKIT_WS_URL ?? "wss://*.livekit.cloud https://*.livekit.cloud"}`,
```

(`proxy.ts` reads `process.env` directly rather than importing `env` — it runs in the middleware/edge runtime where the module-load `env.parse` may not be desired. The `?? default` keeps dev behavior identical when the var is unset.)

- [ ] **Step 6: Type-check + lint + full test**

Run: `cd frontend/session && npm run type-check && npm run lint && npm run test`
Expected: all PASS (including the existing 100%-branch candidate-session gate).

- [ ] **Step 7: Commit**

```bash
git add frontend/session/proxy.ts frontend/session/lib/env.ts frontend/session/tests/lib/env.test.ts
git commit -m "feat(session): make LiveKit CSP connect-src origin env-driven for self-hosting"
```

---

## Part 4 — Documentation

### Task 10: Production deployment doc

**Files:**
- Create: `docs/deployment/2026-06-09-self-hosted-livekit-deployment.md`

- [ ] **Step 1: Write the deployment doc**

Capture, from spec §5.3 and the verification log §9:
- **Plane split:** app planes (API/engine/workers) → ECS Fargate; LiveKit plane (SFU + Egress + Redis) → EC2/EKS with host networking (NOT Fargate — no host networking, no `SYS_ADMIN`); GPU plane → Modal.
- **Tier 1 (first prod cut):** single compute-optimized EC2 VM via `docker pull livekit/generate` → `docker run --rm -it -v$PWD:/output livekit/generate`, producing `docker-compose.yaml` + `caddy.yaml` + `livekit.yaml` + `redis.conf` + cloud-init; Caddy auto-TLS for `wss://livekit.<domain>` + `turn.<domain>`. Firewall: 443 (HTTPS + TURN/TLS), 80 (cert issuance), 7881/TCP, 3478/UDP, 50000-60000/UDP. Set the frontend `NEXT_PUBLIC_LIVEKIT_WS_URL=wss://livekit.<domain>` and the backend `LIVEKIT_URL`/`LIVEKIT_PUBLIC_URL` to the same.
- **Tier 2 (scale-out / Trigger B):** EKS + LiveKit Helm chart; Egress as its own autoscaling deployment (HPA on the `livekit_egress_available` custom metric via the Prometheus adapter, 4-CPU pods, one room each); distributed/multi-region SFU. Prereqs: Redis, SSL certs (primary + TURN/TLS).
- **Recording sink unchanged:** still R2 via per-request `S3Upload`; egress service needs no S3 creds.
- **Reference** the spec + its verification log for doc citations.

- [ ] **Step 2: Commit**

```bash
git add docs/deployment/2026-06-09-self-hosted-livekit-deployment.md
git commit -m "docs(deployment): self-hosted LiveKit production path (Tier 1 VM / Tier 2 EKS)"
```

---

## Part 5 — OPTIONAL fast-follow: compose restructure

> Spec §8 leaves this as "land now OR fast-follow" — it has **no functional
> dependency** on the LiveKit bring-up. Skip it for the recording fix; do it as a
> separate hygiene pass. Listed here so it isn't lost.

### Task 11 (OPTIONAL): Layer the app compose for dev/prod parity

**Files:**
- Modify: `backend/nexus/docker-compose.yml` (reduce to env-neutral base)
- Create: `backend/nexus/docker-compose.override.yml` (dev: source mounts, `--reload`, `host.docker.internal` Supabase wiring, exposed ports)
- Create: `backend/nexus/docker-compose.prod.yml` (pinned tags, no mounts, resource limits, replicas)

- [ ] **Step 1:** Move dev-only concerns (the `.:/app` volume, `--reload`, `host.docker.internal` env, port exposes) from `docker-compose.yml` into `docker-compose.override.yml` (auto-merged by `docker compose up`, so the one-command dev flow is unchanged).
- [ ] **Step 2:** Add `docker-compose.prod.yml` with pinned image tags, no source mounts, and resource limits (egress 4 CPU/4 GB floor; vision keeps `cpus: 4`).
- [ ] **Step 3: Verify** `docker compose config >/dev/null` (dev merge) and `docker compose -f docker-compose.yml -f docker-compose.prod.yml config >/dev/null` (prod merge) both parse.
- [ ] **Step 4:** Verify the dev stack still boots: `docker compose up -d nexus && docker compose ps`.
- [ ] **Step 5: Commit** `chore(compose): layer base/dev-override/prod for clean separation`.

---

## Self-review notes (author)

- **Spec coverage:** §4.1 env → Task 7; §4.2 reconcile hardening → Tasks 1-3; §4.3 frontend CSP → Task 9 (dev already covered; prod parameterized); §3/§5.2 LiveKit plane → Tasks 4-6; §5.3 prod path → Task 10; §6 verification → Task 8; §5.2 compose restructure → Task 11 (optional, per §8). All covered.
- **Type/name consistency:** `_reconcile_without_egress` / `_recording_stuck_expired` / `recording_object_key` / `recording_stuck_timeout_seconds` / `NEXT_PUBLIC_LIVEKIT_WS_URL` used identically across tasks.
- **No placeholders:** every code/config step shows full content; image tags flagged for confirm-against-current in Task 6 Step 2.
- **Known fiddly point** honestly surfaced: local ICE (Task 8 Step 6), image-tag currency (Task 6 Step 2).
