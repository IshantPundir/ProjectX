# LAN-mode recordings share link — design

**Date:** 2026-06-18
**Status:** Proposed
**Author:** Ishant + Claude
**Related:** `scripts/demo.sh`, public recordings share (`2026-06-16-public-recordings-share-design.md`)

## Problem

The shared report PDF contains a "See full session recording" button that links
to the public `/recordings/<token>` page. That page lives in **`frontend/app`**
(the recruiter dashboard, port 3000) and is already public + token-gated + has
zero Supabase auth (`proxy.ts` whitelists `/recordings/`).

The link is built in `app/modules/reporting/actors.py:459`:

```python
full_session_url = f"{settings.frontend_base_url}/recordings/{share_token}"
```

`FRONTEND_BASE_URL` defaults to `http://localhost:3000`. In the `scripts/demo.sh`
**LAN** demo, only `frontend/session` + the backend + LiveKit signaling get ngrok
tunnels (the ngrok free tier caps at 3 simultaneous tunnels — all used). The
recruiter app is **not** tunneled, so a PDF recipient on another device cannot
open a `localhost:3000` link.

## Goal

In LAN demo mode, anyone **on the same WiFi** who receives the PDF can open the
recordings page. This matches the demo's existing constraint exactly: WebRTC
media is already LAN-direct, so the demo is same-WiFi-only regardless. We are
not reducing reach; we are making the share link reachable for the audience the
demo already supports.

**Non-goal:** internet-wide recipients (would need a 4th tunnel or a public VM —
out of scope, blocked by the ngrok tunnel cap). No code move/duplication of the
recordings page into `frontend/session`. No bending the session app's
single-purpose rule.

## Approach: point the PDF link at the host's LAN address

The recordings page stays in `frontend/app`. We make `frontend/app` reachable on
the LAN and have the PDF embed `http://<LAN-IP>:3000/recordings/<token>`.

Three things must line up for that page to work from another device:

1. **The page must load** — `frontend/app` dev server (`npm run dev`) binds
   `0.0.0.0` by default and prints its Network URL, so `http://<LAN-IP>:3000`
   already serves to same-WiFi devices. No change; the user already runs the
   dashboard.
2. **Its API calls must reach the backend** — the page calls
   `GET /api/public/recordings/{token}` via `NEXT_PUBLIC_API_URL`, which today is
   `http://127.0.0.1:8000` (unreachable from another device). In LAN mode this
   must become `http://<LAN-IP>:8000` (Docker already publishes 8000 on the host).
   `.env.local.example` already anticipates this exact value.
3. **CORS must allow the page's origin** — the backend `CORS_ORIGINS` must include
   `http://<LAN-IP>:3000`.

No mixed-content issues: `http` page → `http` API → `https` R2 presigned video
are all permitted from an `http` origin. The recruiter app sets **no CSP /
connect-src restriction** (the strict CSP is only in `frontend/session`), so there
is no client-side gate to adjust. R2 presigned URLs are reachable from anywhere.

### Why a dedicated base-URL setting (not repoint `frontend_base_url`)

`frontend_base_url` is also used for recruiter invite/confirmation emails. To keep
blast radius minimal we add a dedicated, optional `RECORDING_SHARE_BASE_URL` that
**defaults to `frontend_base_url`** when unset — production behavior is unchanged.
`demo.sh` sets it only in LAN mode.

## Changes

### 1. Backend config — `app/config.py`

Add an optional setting next to `frontend_base_url`:

```python
# Base URL for the public recordings share page (the "See full session
# recording" button in the shared report PDF). Defaults to frontend_base_url
# (the recruiter app hosts /recordings/). Overridden only for the LAN demo,
# where the recruiter app is reached at the host's LAN IP instead of localhost
# (scripts/demo.sh sets it). Kept separate from frontend_base_url so the LAN
# override does not disturb recruiter invite-email links.
recording_share_base_url: str | None = None

@field_validator("recording_share_base_url")
@classmethod
def _strip_recording_share_trailing_slash(cls, v: str | None) -> str | None:
    return v.rstrip("/") if v else v
```

### 2. Backend actor — `app/modules/reporting/actors.py:459`

```python
share_base = settings.recording_share_base_url or settings.frontend_base_url
full_session_url = f"{share_base}/recordings/{share_token}"
```

This actor runs in `nexus-pdf-worker` (no hot-reload) — `demo.sh`'s
`recreate_backend_containers` does not touch it, but `ensure_nexus_workers` runs
`up -d nexus-pdf-worker`. Because the change is **env-driven** (the worker reads
`RECORDING_SHARE_BASE_URL` at process start), the pdf-worker must be **recreated**
(`--force-recreate`) in LAN/local toggles so it picks up the new/removed env var,
OR the new var must be present before it starts. See demo.sh change below.

### 3. `scripts/demo.sh`

Scope expansion: the script now also manages `frontend/app/.env.local`. Update the
header "Scope" comment to reflect this (currently it says it does NOT touch
`frontend/app`).

- **New constant + backup:** `APP_ENV="$REPO_ROOT/frontend/app/.env.local"` and
  back it up/restore it alongside backend + session (extend `backup_envs_once` /
  `restore_envs`).
- **LAN IP detection helper:**
  ```bash
  detect_lan_ip() {
    ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1 \
      || hostname -I 2>/dev/null | awk '{print $1}'
  }
  ```
  Fail loudly if empty.
- **`cmd_lan`:**
  - `lan_ip="$(detect_lan_ip)"`
  - `set_env_var "$APP_ENV" "NEXT_PUBLIC_API_URL" "http://${lan_ip}:8000"`
  - `set_env_var "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL" "http://${lan_ip}:3000"`
  - add `http://${lan_ip}:3000` to the `set_cors_origins` argument list
  - recreate the pdf-worker with `--force-recreate` so it reads
    `RECORDING_SHARE_BASE_URL` (or set the env before `ensure_nexus_workers` and
    add `--force-recreate nexus-pdf-worker`).
  - extend the "Next steps" output: the user must (re)start `frontend/app`
    (`cd frontend/app && npm run dev`) so it reads the rewritten
    `NEXT_PUBLIC_API_URL`, and access the dashboard via `http://<LAN-IP>:3000`
    when testing the shared link from another device.
- **`cmd_local`:** `restore_envs` already restores all backed-up env files
  (now including `APP_ENV`), which removes the LAN `NEXT_PUBLIC_API_URL` and
  `RECORDING_SHARE_BASE_URL`. Recreate the pdf-worker so it drops the env var.
- **`cmd_status`:** print `RECORDING_SHARE_BASE_URL` (backend) and the app's
  `NEXT_PUBLIC_API_URL` for visibility.

## Data flow (LAN mode)

```
Recruiter clicks Share  →  share_report_pdf actor (pdf-worker)
   builds link: http://<LAN-IP>:3000/recordings/<token>
   → PDF emailed to recipient

Recipient (same WiFi) opens the link on their device
   → frontend/app at <LAN-IP>:3000 serves /recordings/<token>  (public, no Supabase)
   → page fetches <LAN-IP>:8000 GET /api/public/recordings/<token>  (CORS allows <LAN-IP>:3000)
   → backend resolves token (bypass-RLS) → presigned R2 URLs
   → recording + reel play from R2 (https, reachable anywhere)
```

## Security / privacy

- The capability token (256-bit, HMAC-hashed at rest, 1-year TTL, revocable,
  uniform-404) is unchanged. LAN exposure does not weaken it.
- `RECORDING_SHARE_BASE_URL` is local-dev/demo only; production leaves it unset →
  falls back to `frontend_base_url`. No production behavior change.
- `frontend/app` already has no CSP/connect-src gate, so no security header is
  loosened.
- The link is `http://` on the LAN — acceptable for a same-WiFi demo; the token
  is in the URL path (same exposure profile as today's `localhost` link).

## Testing

- **Backend unit:** `recording_share_base_url` falls back to `frontend_base_url`
  when unset; trailing slash stripped; the actor builds the URL from the override
  when set. (Add to existing `reporting` actor tests.)
- **demo.sh:** manual — run `demo.sh lan`, confirm `status` shows the LAN IP in
  both `RECORDING_SHARE_BASE_URL` and the app's `NEXT_PUBLIC_API_URL`; share a
  report, confirm the PDF link is `http://<LAN-IP>:3000/recordings/...`; open it
  from a second same-WiFi device and confirm the recording + reel play. Then
  `demo.sh local` and confirm both env vars are restored/removed.

## Rollback

Revert the config + actor change (link falls back to `frontend_base_url`) and the
demo.sh changes. No migration, no data change.
