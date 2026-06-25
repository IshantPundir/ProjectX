# LAN-mode Recordings Share Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `scripts/demo.sh` LAN mode, make the shared report PDF's "See full session recording" link reachable from any same-WiFi device by pointing it at the host's LAN IP instead of `localhost`.

**Architecture:** Keep the public `/recordings/<token>` page where it lives (`frontend/app`). Add an optional backend setting `RECORDING_SHARE_BASE_URL` that defaults to `frontend_base_url` (zero production change). The PDF actor builds the link from it. `demo.sh` detects the host LAN IP and, in LAN mode only, sets that var to `http://<LAN-IP>:3000`, repoints `frontend/app`'s `NEXT_PUBLIC_API_URL` to `http://<LAN-IP>:8000`, and adds `http://<LAN-IP>:3000` to backend CORS. `local` mode restores everything.

**Tech Stack:** FastAPI / pydantic-settings (backend config), Dramatiq actor (PDF render), Bash (demo.sh).

## Global Constraints

- **No production behavior change:** `RECORDING_SHARE_BASE_URL` defaults to `None`; the actor falls back to `settings.frontend_base_url`. Production leaves the env var unset. (Copied from spec §"Approach".)
- **Minimal blast radius:** do NOT repoint `frontend_base_url` (it is also used for recruiter invite emails). (Spec §"Why a dedicated base-URL setting".)
- **`nexus-pdf-worker` has no hot-reload** and reads `RECORDING_SHARE_BASE_URL` at process start — it must be recreated on a LAN/local toggle so it picks up the new/removed env var. (Spec §Changes.2, backend CLAUDE.md.)
- **Config tests must be hermetic:** build `Settings` with `_env_file=None` (or `monkeypatch.setenv` + fresh `Settings()`), mirroring `tests/test_config_validators.py`.
- **Backend Python 3.13, async, type hints required.** (backend CLAUDE.md → Code Standards.)
- **Secrets/config via `app/config.py` only**, env-driven, never hardcoded.

---

### Task 1: Add `recording_share_base_url` setting to backend config

**Files:**
- Modify: `backend/nexus/app/config.py` (insert after the `frontend_base_url` validator, around line 551)
- Test: `backend/nexus/tests/test_config_validators.py` (append)

**Interfaces:**
- Produces: `Settings.recording_share_base_url: str | None` (default `None`; trailing slash stripped when set). Consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Append to `backend/nexus/tests/test_config_validators.py`:

```python
def test_recording_share_base_url_defaults_to_none():
    """Unset → None, so the actor falls back to frontend_base_url (no prod change)."""
    from app.config import Settings

    s = Settings(_env_file=None, candidate_jwt_secret="test-secret-32-chars-long-0000000")
    assert s.recording_share_base_url is None


def test_recording_share_base_url_strips_trailing_slash():
    """Mirrors frontend_base_url's normalizer — trailing slash is removed when set."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        candidate_jwt_secret="test-secret-32-chars-long-0000000",
        recording_share_base_url="http://192.168.1.50:3000/",
    )
    assert s.recording_share_base_url == "http://192.168.1.50:3000"


def test_recording_share_base_url_none_is_not_stripped():
    """None passes through the validator untouched (no AttributeError)."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        candidate_jwt_secret="test-secret-32-chars-long-0000000",
        recording_share_base_url=None,
    )
    assert s.recording_share_base_url is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm nexus pytest tests/test_config_validators.py -k recording_share_base_url -v`
Expected: FAIL — `Settings` has no field `recording_share_base_url` (or the strip test fails because the value is returned verbatim).

- [ ] **Step 3: Add the setting + validator**

In `backend/nexus/app/config.py`, immediately after the `frontend_base_url` validator (the `_strip_trailing_slash` method ending at line 551), insert:

```python
    # Base URL for the public recordings share page (the "See full session
    # recording" button in the shared report PDF). Defaults to frontend_base_url
    # (the recruiter app hosts /recordings/) when unset — production leaves this
    # unset, so there is no behavior change. Overridden ONLY for the LAN demo,
    # where the recruiter app is reached at the host's LAN IP instead of
    # localhost (scripts/demo.sh sets it). Kept separate from frontend_base_url
    # so the LAN override does not disturb recruiter invite-email links.
    recording_share_base_url: str | None = None

    @field_validator("recording_share_base_url")
    @classmethod
    def _strip_recording_share_trailing_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/test_config_validators.py -k recording_share_base_url -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/config.py backend/nexus/tests/test_config_validators.py
git commit -m "feat(config): add optional RECORDING_SHARE_BASE_URL (defaults to frontend_base_url)"
```

---

### Task 2: Build the PDF recordings link from the new setting

**Files:**
- Modify: `backend/nexus/app/modules/reporting/actors.py:459`
- Test: `backend/nexus/tests/reporting/test_share_actor.py` (append one test)

**Interfaces:**
- Consumes: `settings.recording_share_base_url` (Task 1), `settings.frontend_base_url`.
- Produces: PDF `full_session_url` = `"{recording_share_base_url or frontend_base_url}/recordings/{token}"`.

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/reporting/test_share_actor.py` (it already imports `actors` and defines `_patch_bypass_session`):

```python
@pytest.mark.asyncio
async def test_share_actor_uses_recording_share_base_url_override(
    db_session, monkeypatch, seeded_share
):
    """When RECORDING_SHARE_BASE_URL is set (LAN demo), the PDF link uses it
    instead of frontend_base_url."""
    captured = {}
    real_build = actors.build_pdf_context

    def _capture_build(*args, **kwargs):
        captured["full_session_url"] = kwargs.get("full_session_url")
        return real_build(*args, **kwargs)

    async def fake_render(ctx):
        return b"%PDF-1.4 fake"

    class FakeStorage:
        async def upload_bytes(self, key, data, *, content_type):
            pass
        async def presign_get_url(self, key, *, ttl_seconds):
            return "https://r2/photo.jpg"

    async def fake_send_email(*, to, subject, html, attachments=None):
        pass

    monkeypatch.setattr(actors.settings, "recording_share_base_url", "http://192.168.1.50:3000")
    _patch_bypass_session(monkeypatch, db_session)
    monkeypatch.setattr(actors, "build_pdf_context", _capture_build)
    monkeypatch.setattr(actors, "render_report_pdf", fake_render)
    monkeypatch.setattr(actors, "get_object_storage", lambda: FakeStorage())
    monkeypatch.setattr(actors, "send_email", fake_send_email)

    await actors._share_report_pdf_async(
        share_id=seeded_share.id, tenant_id=seeded_share.tenant_id, correlation_id="c",
    )

    url = captured["full_session_url"]
    assert url.startswith("http://192.168.1.50:3000/recordings/")
```

> Note: the existing `test_share_actor_mints_token_and_sets_recordings_url` test does NOT set the override, so it implicitly verifies the fallback to `frontend_base_url` still works — no separate fallback test needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_actor.py -k recording_share_base_url_override -v`
Expected: FAIL — `url` starts with `frontend_base_url` (e.g. `http://localhost:3000/...`), not the override.

- [ ] **Step 3: Apply the change**

In `backend/nexus/app/modules/reporting/actors.py`, replace line 459:

```python
            full_session_url = f"{settings.frontend_base_url}/recordings/{share_token}"
```

with:

```python
            # LAN demo: recording_share_base_url points at the host LAN IP so a
            # PDF recipient on the same WiFi can open the page; unset in prod →
            # falls back to frontend_base_url (the recruiter app). See
            # docs/superpowers/specs/2026-06-18-lan-recordings-share-link-design.md
            share_base = settings.recording_share_base_url or settings.frontend_base_url
            full_session_url = f"{share_base}/recordings/{share_token}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm nexus pytest tests/reporting/test_share_actor.py -v`
Expected: PASS (all share-actor tests, including the override and the existing token/fallback test).

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/reporting/actors.py backend/nexus/tests/reporting/test_share_actor.py
git commit -m "feat(reporting): build recordings share link from RECORDING_SHARE_BASE_URL when set"
```

---

### Task 3: demo.sh — manage frontend/app env + LAN IP detection

**Files:**
- Modify: `scripts/demo.sh` (header comment, constants, `backup_envs_once`, `restore_envs`, new `detect_lan_ip` helper)

**Interfaces:**
- Produces: `APP_ENV` constant, `APP_ENV_BACKUP` constant, `detect_lan_ip()` → echoes the host LAN IPv4. Consumed by Task 4.
- Note: `demo.sh` is Bash, not unit-tested in this repo — verification is by `bash -n` syntax check + manual run.

- [ ] **Step 1: Update the header "Scope" comment**

In `scripts/demo.sh`, in the Scope block (lines ~12–21), change the "does NOT touch the recruiter app (frontend/app)" wording. Replace the two bullet lines:

```bash
#   - This script does NOT:    start `npm run dev` for any frontend, touch the
#     recruiter app (frontend/app), or change anything Supabase-related.
```

with:

```bash
#   - This script ALSO touches (added 2026-06-18): frontend/app/.env.local
#     (NEXT_PUBLIC_API_URL → http://<LAN-IP>:8000) so the recruiter app's public
#     /recordings/<token> page, opened from another same-WiFi device, can reach
#     the backend. The PDF's recordings link is pointed at http://<LAN-IP>:3000
#     via the backend's RECORDING_SHARE_BASE_URL.
#   - This script does NOT:    start `npm run dev` for any frontend, or change
#     anything Supabase-related.
```

- [ ] **Step 2: Add the `APP_ENV` constants**

After the `SESSION_ENV` constant (line 41), add:

```bash
APP_ENV="$REPO_ROOT/frontend/app/.env.local"
```

After the `SESSION_ENV_BACKUP` constant (line 48), add:

```bash
APP_ENV_BACKUP="$STATE_DIR/app.env.local.backup"
```

- [ ] **Step 3: Extend `backup_envs_once` and `restore_envs`**

Replace `backup_envs_once` (lines 156–160) with:

```bash
backup_envs_once() {
  mkdir -p "$STATE_DIR"
  [[ -f "$BACKEND_ENV_BACKUP" ]] || cp "$BACKEND_ENV" "$BACKEND_ENV_BACKUP"
  [[ -f "$SESSION_ENV_BACKUP" ]] || cp "$SESSION_ENV" "$SESSION_ENV_BACKUP"
  [[ -f "$APP_ENV_BACKUP" ]]     || cp "$APP_ENV" "$APP_ENV_BACKUP"
}
```

Replace `restore_envs` (lines 162–175) with:

```bash
restore_envs() {
  local restored=0
  if [[ -f "$BACKEND_ENV_BACKUP" ]]; then
    cp "$BACKEND_ENV_BACKUP" "$BACKEND_ENV"
    rm "$BACKEND_ENV_BACKUP"
    restored=1
  fi
  if [[ -f "$SESSION_ENV_BACKUP" ]]; then
    cp "$SESSION_ENV_BACKUP" "$SESSION_ENV"
    rm "$SESSION_ENV_BACKUP"
    restored=1
  fi
  if [[ -f "$APP_ENV_BACKUP" ]]; then
    cp "$APP_ENV_BACKUP" "$APP_ENV"
    rm "$APP_ENV_BACKUP"
    restored=1
  fi
  return $((1 - restored))
}
```

- [ ] **Step 4: Add `require_files` coverage + the LAN IP helper**

In `require_files` (after the `SESSION_ENV` check, line ~91), add:

```bash
  [[ -f "$APP_ENV" ]]            || die "Recruiter app .env.local not found at $APP_ENV"
```

After the `require_files` function (around line 94), add the helper:

```bash
# Echo the host's primary LAN IPv4 (the source address used to reach the
# internet). Falls back to the first `hostname -I` address. Used so the shared
# report PDF + the recruiter app's API base point at a same-WiFi-reachable host
# instead of localhost.
detect_lan_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1 || true)"
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  echo "$ip"
}
```

- [ ] **Step 5: Syntax check**

Run: `bash -n scripts/demo.sh`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/demo.sh
git commit -m "chore(demo): manage frontend/app env + add LAN IP detection helper"
```

---

### Task 4: demo.sh — wire the LAN IP into cmd_lan, cmd_local, cmd_status

**Files:**
- Modify: `scripts/demo.sh` (`cmd_lan`, `cmd_local`, `cmd_status`, and a pdf-worker recreate)

**Interfaces:**
- Consumes: `detect_lan_ip`, `APP_ENV`, `set_env_var`, `set_cors_origins`, `read_env_value` (existing).

- [ ] **Step 1: Set LAN IP–based env in `cmd_lan`**

In `cmd_lan`, after the block that rewrites `frontend/session/.env.local` (the `set_env_var "$SESSION_ENV" "NEXT_PUBLIC_LIVEKIT_WS_URL" ...` line, ~line 310), add:

```bash
  local lan_ip; lan_ip="$(detect_lan_ip)"
  [[ -n "$lan_ip" ]] || die "Could not detect this host's LAN IP (needed for the recordings share link)."
  info "Detected LAN IP: $lan_ip"

  info "Pointing the recordings share link at the recruiter app on the LAN…"
  set_env_var "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL" "http://${lan_ip}:3000"

  info "Rewriting frontend/app/.env.local (NEXT_PUBLIC_API_URL → LAN backend)…"
  set_env_var "$APP_ENV" "NEXT_PUBLIC_API_URL" "http://${lan_ip}:8000"
```

- [ ] **Step 2: Add the app LAN origin to CORS in `cmd_lan`**

Replace the existing CORS line in `cmd_lan` (line ~306):

```bash
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "${LOCAL_ORIGINS[@]}"
```

with (note: `lan_ip` is set in Step 1, which runs *after* this line in source order — move the `lan_ip` detection block from Step 1 to *above* this CORS line, i.e. right after the `info "Rewriting backend/nexus/.env ..."` section. Final order in `cmd_lan`: detect `lan_ip` → rewrite backend env incl. `RECORDING_SHARE_BASE_URL` → CORS with the app origin → rewrite session env → rewrite app env):

```bash
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "http://${lan_ip}:3000" "${LOCAL_ORIGINS[@]}"
```

> Implementer note: the safe final layout for `cmd_lan` is — (1) `lan_ip="$(detect_lan_ip)"` + guard right after `fetch_tunnel_urls`/`livekit_wss` are known; (2) backend `.env` rewrites: `CANDIDATE_SESSION_BASE_URL`, `LIVEKIT_PUBLIC_URL`, `RECORDING_SHARE_BASE_URL`, then `set_cors_origins` including `http://${lan_ip}:3000`; (3) session `.env.local` rewrites; (4) app `.env.local` rewrite. Detect `lan_ip` once, before any use.

- [ ] **Step 3: Recreate the pdf-worker so it reads the new env**

The PDF actor reads `RECORDING_SHARE_BASE_URL` at process start and `nexus-pdf-worker` has no hot-reload. In `cmd_lan`, the existing `ensure_nexus_workers` call runs `up -d nexus-pdf-worker` (idempotent, won't recreate). Add an explicit force-recreate right after `recreate_backend_containers` (line ~313):

```bash
  info "Recreating the report-share PDF worker so it reads RECORDING_SHARE_BASE_URL…"
  (cd "$BACKEND_DIR" && docker compose up -d --force-recreate nexus-pdf-worker)
```

Do the same in `cmd_local` after `recreate_backend_containers` (so the worker drops the removed env var):

```bash
    info "Recreating the report-share PDF worker so it drops RECORDING_SHARE_BASE_URL…"
    (cd "$BACKEND_DIR" && docker compose up -d --force-recreate nexus-pdf-worker)
```

(Place the `cmd_local` one inside the `if restore_envs; then` branch, after `recreate_backend_containers`.)

- [ ] **Step 4: Extend the `cmd_lan` "Next steps" output**

In the `cat <<EOF` block of `cmd_lan`, add a step about the recruiter app. After the existing step about the session app (step 1) and before/around the invite steps, add a line set:

```bash
  4. To let a PDF recipient watch the recording from another device, (re)start
     the recruiter app so it reads the rewritten NEXT_PUBLIC_API_URL, and reach
     it via the LAN IP (NOT localhost):
       (cd frontend/app && npm run dev)
       → recruiter dashboard on this machine: http://${lan_ip}:3000
     The shared report PDF's "See full session recording" link will point at
     http://${lan_ip}:3000/recordings/<token>, reachable by any same-WiFi device.
```

(Renumber the following existing steps accordingly, or append as the last numbered step — keep it inside the same heredoc so `${lan_ip}` interpolates.)

- [ ] **Step 5: Print the new values in `cmd_status`**

In `cmd_status`, after the existing `read_env_value` calls (lines ~384–388), add:

```bash
  local backend_rec_share app_api
  backend_rec_share="$(read_env_value "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL")"
  app_api="$(read_env_value "$APP_ENV" "NEXT_PUBLIC_API_URL")"
```

And in the echo block (after the `LIVEKIT_PUBLIC_URL` line, ~line 395), add:

```bash
  echo "  backend          RECORDING_SHARE_BASE_URL   : ${backend_rec_share:-<unset>}"
  echo "  frontend/app     NEXT_PUBLIC_API_URL        : ${app_api:-<unset>}"
```

- [ ] **Step 6: Syntax check**

Run: `bash -n scripts/demo.sh`
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git add scripts/demo.sh
git commit -m "feat(demo): point recordings share link + app API at host LAN IP in LAN mode"
```

---

### Task 5: Manual end-to-end verification

**Files:** none (manual run; the LiveKit/ngrok stack and a real session are required, so this is operator-run, not automated).

- [ ] **Step 1: Switch to LAN mode and check status**

```bash
./scripts/demo.sh lan
./scripts/demo.sh status
```

Expected `status` output includes a non-empty LAN IP in both:
- `backend RECORDING_SHARE_BASE_URL : http://<LAN-IP>:3000`
- `frontend/app NEXT_PUBLIC_API_URL : http://<LAN-IP>:8000`

- [ ] **Step 2: Start the recruiter app and reach it via LAN IP**

```bash
(cd frontend/app && npm run dev)
```

From a second same-WiFi device, open `http://<LAN-IP>:3000` and confirm the dashboard loads.

- [ ] **Step 3: Share a report and inspect the PDF link**

From the dashboard, open a completed session report → Share → enter an email. In the dry-run email log, confirm the "See full session recording" link is `http://<LAN-IP>:3000/recordings/<token>` (not `localhost`):

```bash
docker compose -f backend/nexus/docker-compose.yml logs --tail=200 nexus-pdf-worker | grep -iE 'recordings|share'
```

- [ ] **Step 4: Open the link from the second device**

On the second same-WiFi device, open `http://<LAN-IP>:3000/recordings/<token>`. Confirm: the page loads, the report renders, and the recording + reel play (R2 presigned video over https).

- [ ] **Step 5: Return to local mode and confirm restore**

```bash
./scripts/demo.sh local
./scripts/demo.sh status
```

Expected: `RECORDING_SHARE_BASE_URL` is `<unset>` and `frontend/app NEXT_PUBLIC_API_URL` is back to `http://127.0.0.1:8000`. Confirm `frontend/app/.env.local`, `backend/nexus/.env`, and `frontend/session/.env.local` match their pre-LAN contents (no stray LAN entries).

- [ ] **Step 6: No commit** (verification only).

---

## Self-Review

**1. Spec coverage:**
- Spec §Changes.1 (config setting) → Task 1. ✅
- Spec §Changes.2 (actor builds from override w/ fallback) → Task 2. ✅
- Spec §Changes.3 (demo.sh: APP_ENV backup/restore, LAN IP detect, RECORDING_SHARE_BASE_URL, app NEXT_PUBLIC_API_URL, CORS origin, pdf-worker recreate, status output, header comment, next-steps output) → Tasks 3 + 4. ✅
- Spec §Testing (backend unit fallback/override/strip; demo.sh manual) → Tasks 1, 2 (unit) + Task 5 (manual). ✅
- Spec §"Data flow" / §Security (no header loosened, token unchanged) → no code; verified by Task 5 Step 4. ✅
- Spec §Rollback → covered by the per-task commits being independently revertible (config+actor fall back; demo.sh revert). ✅

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". All code shown verbatim; all commands have expected output. ✅

**3. Type consistency:** `recording_share_base_url: str | None` defined in Task 1, consumed identically in Task 2 (`settings.recording_share_base_url or settings.frontend_base_url`). `detect_lan_ip` / `APP_ENV` / `APP_ENV_BACKUP` defined in Task 3, consumed in Task 4. `full_session_url` kwarg name matches the existing actor + the existing `test_share_actor_mints_token_and_sets_recordings_url` capture pattern. ✅
