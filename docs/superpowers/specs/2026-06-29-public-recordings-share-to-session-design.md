# Move the public recordings share page to the candidate session app

**Date:** 2026-06-29
**Status:** Approved (design) — pending implementation plan
**Related:**
- `docs/superpowers/specs/2026-06-16-public-recordings-share-design.md` (original public recordings page, in `frontend/app`)
- `docs/superpowers/specs/2026-06-18-lan-recordings-share-link-design.md` (the LAN `RECORDING_SHARE_BASE_URL` workaround this supersedes)
- `docs/superpowers/specs/2026-06-16-report-sharing-pdf-design.md` (the shared PDF that carries the link)

---

## Problem

A shared report PDF contains "Candidate highlight" (reel) and "Full session" buttons that link to a
public, token-gated recordings page at `/recordings/<token>?view=reel|full`. Today that page lives in
the **recruiter dashboard** (`frontend/app`), and in LAN demo mode the link points at
`http://<LAN-IP>:3000` (the recruiter app on the host's LAN IP).

That only works for a recipient **on the same WiFi**: the recruiter app is not exposed through ngrok
(the free tier already runs three tunnels — backend, session, LiveKit signaling — and a fourth would
exceed the cap), and the LAN IP is unreachable from the internet.

The **candidate session app** (`frontend/session`) **is** already tunneled (`session-frontend` ngrok
endpoint) and its base URL is already rewritten to the public ngrok URL by `scripts/demo.sh`. Hosting
the public recordings page there makes the PDF's links reachable by anyone with the PDF — not just
same-WiFi recipients — with the link base auto-tracking both local and ngrok addresses. The R2
presigned video URLs and the public backend endpoint are already internet-reachable, so the move
delivers true remote playback, not just LAN.

## Goal

Move the public recordings share page from `frontend/app` to `frontend/session`, repoint the PDF link
at the session app's base URL, and remove the now-dead LAN-only workarounds — so that **every** shared
PDF link works in both local and LAN/ngrok mode without manual wiring.

## Non-goals

- No change to token minting, hashing, resolution, validation, the `PublicRecordingsEnvelope` shape,
  the public endpoint, or the middleware allowlist.
- No change to what the viewer sees: **full parity** with today's page (reel + full session via
  `ReviewTheater` — scores, proctoring flags, transcript, question rail).
- No shared-component-package extraction between the two Next apps (out of scope; see Accepted drift).
- No change to the authenticated recruiter report viewer (which also uses the theater components).

---

## Decisions (locked during brainstorming)

1. **Full parity port** — port the entire current experience (reel + full `ReviewTheater`), not a
   leaner videos-only page.
2. **Remove the old `frontend/app` public page** — single source of truth in `frontend/session`. Only
   the *public* route + `PublicRecordingsView` + the `proxy.ts` `/recordings` bypass are deleted; the
   theater components stay in `frontend/app` because the **authenticated** report page still uses them.
3. **Base the link on `candidate_session_base_url`** and delete the `recording_share_base_url` override
   + the `demo.sh` LAN hacks.

---

## Design

### 1. Backend (surgical)

**`app/modules/reporting/actors.py`** (`_share_report_pdf_async`):
- Change the link base:
  ```python
  # was: share_base = settings.recording_share_base_url or settings.frontend_base_url
  share_base = settings.candidate_session_base_url
  ```
- `full_session_url` / `reel_url` construction and the `?view=full|reel` deep-links are unchanged.
- `candidate_session_base_url` already has a `field_validator` that strips trailing slashes
  (`config.py:575`), so `f"{base}/recordings/..."` is safe (no `//recordings`).

**`app/config.py`:**
- Delete the `recording_share_base_url` setting and its `_strip_recording_share_trailing_slash`
  validator (it existed only for the LAN recruiter-app workaround being removed).

**Unchanged:** `reporting/public_router.py`, `reporting/public_share.py`, `reporting/share_tokens.py`,
`reporting/schemas.py` (envelope), `middleware/auth.py` `_PUBLIC_PREFIXES`,
`reporting/pdf/{context.py,render.py,templates/report.html.j2}` (they receive the two URLs as before).

**Tests:**
- `tests/reporting/test_share_actor.py`: the existing
  `test_share_actor_uses_recording_share_base_url_override` is repurposed to assert the link is built
  from `candidate_session_base_url` (set `settings.candidate_session_base_url`, assert the URL prefix).
  The `test_share_actor_mints_token_and_sets_recordings_url` token/`?view=` assertions stay.
- `tests/test_public_recordings.py`, `tests/test_share_tokens.py`: unchanged.

### 2. `scripts/demo.sh` (simplification)

Remove the three pieces added solely to serve the page off the recruiter app at the LAN IP:
- The `set_env_var "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL" "http://${lan_ip}:3000"` line + its log.
- The `frontend/app/.env.local` `NEXT_PUBLIC_API_URL` rewrite, plus `APP_ENV` /
  `APP_ENV_BACKUP` backup + restore handling and the recruiter-app "Next steps" guidance that exists
  only for the recordings page. (Re-evaluate whether any *other* reason to rewrite `app/.env.local`
  remains; per the inline comment dated 2026-06-18 it was added exclusively for the recordings page, so
  it should be fully removable.)
- The `http://${lan_ip}:3000` entry from the `set_cors_origins` call (the session ngrok URL is already
  in CORS).
- Update `cmd_status` to drop the `RECORDING_SHARE_BASE_URL` / `frontend/app NEXT_PUBLIC_API_URL` lines
  it prints, and update the header comment block describing what the script touches.

`CANDIDATE_SESSION_BASE_URL` is already rewritten to the session ngrok URL in `cmd_lan`, so no new env
wiring is needed for the link.

### 3. `frontend/session` — the port

**Route files** under `frontend/session/app/recordings/`:
- `[token]/page.tsx` — extract `token` from params, render `PublicRecordingsView` (client).
- `[token]/layout.tsx` (or a shared `recordings/layout.tsx`) — mount the `@tanstack/react-query`
  provider (already a session dependency) and wrap children in a container with
  `data-px-theme="daylight"` so the ported components render with their original tokens.
- `[token]/loading.tsx`, `[token]/error.tsx` — ported skeleton + expired/revoked fallback.

**Component closure** copied into `frontend/session/components/recordings/`:
- `PublicRecordingsView`, `theater/{ReelTheater, ReviewTheater, SessionPlayback, GlassProvider,
  VideoControls, ...}`, `PublicPlaybackProvider` + the `public-playback-context`, and the
  `use-session-recording` / `use-session-proctoring` hooks (their public-context branches).
- The **full transitive closure** of `@/components/px` primitives (`BrandLogo`, `Skeleton`, `Dialog`,
  `DialogContent`, …) and report scoring sub-components that `ReviewTheater` imports.
- **First implementation task** = mechanically compute that exact closure (the main unknown). All `@/`
  imports are rewritten to session paths. If the closure drags in a disproportionately large slice of
  the report component library, surface it before committing to the copy.

**CSS / theme parity:**
- Copy `theater.css` into the session components tree.
- Scope `frontend/app`'s `theme.css` `[data-px-theme="daylight"]` token block + the `@theme inline`
  Tailwind token mapping into session, scoped to the `/recordings` subtree, so ported components keep
  their original colors without disturbing the candidate-interview `cool-light` theme.
- Load the Urbanist font scoped to the `/recordings` route for visual parity (session's default is
  Inter/Fraunces).

**API client:** add `frontend/session/lib/api/recordings.ts`:
- `getPublicRecordings(token)` → `GET ${NEXT_PUBLIC_API_URL}/api/public/recordings/{token}`, no
  `Authorization` header, with the `ngrok-skip-browser-warning: '1'` header the session client already
  uses, `cache: 'no-store'`.
- Port the response types: `PublicRecordingsEnvelope`, `ReportRead`, `RecordingPlayback`,
  `ProctoringAnalysis`, `ReelPlayback`, `ReelChapter`.

### 4. CSP (load-bearing)

`frontend/app` sets **no** CSP, which is why R2 presigned `<video src>` works there. `frontend/session`
enforces a strict per-request CSP in `proxy.ts`. The video is a plain `<video src={signedUrl}>` where
`signedUrl` is a presigned URL on `recording_storage_endpoint_url`
(`https://<account>.r2.cloudflarestorage.com` for R2; a regional AWS host for S3). Without a CSP change
the page renders but the video is blocked.

- New env `NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN`, validated in `frontend/session/lib/env.ts`
  (optional; default `https://*.r2.cloudflarestorage.com` so it works out-of-box; provider-agnostic, no
  host hardcoded in code). Documented in `frontend/session/.env.local.example`.
- `frontend/session/proxy.ts`: add the value to `media-src` and `img-src` (and `connect-src` as a
  safety net in case a player variant fetches the media).
- This origin is the **same in local and LAN mode** (R2 is a fixed cloud host regardless of how the
  candidate reaches the app), so `demo.sh` does not touch it.

### 5. Constraint checks

- `frontend/session` must not depend on `@supabase/*` — the ported tree uses none. ✔
- `frontend/app` must not depend on `livekit-*` — this change only *removes* files there. ✔
- No new route allowlisting in session `proxy.ts`; the candidate-token CSP/headers and the
  `/interview/[token]` flow are unaffected.

### 6. Removal in `frontend/app`

Delete:
- `frontend/app/app/recordings/` (route dir: `layout.tsx`, `[token]/{page,loading,error}.tsx`).
- `frontend/app/components/dashboard/reports/PublicRecordingsView.tsx`.
- The `/recordings/` auth bypass branch in `frontend/app/proxy.ts`.
- `reportsApi.getPublicRecordings` from `frontend/app/lib/api/reports.ts` (now unused in app).

Keep (still used by the authenticated report viewer):
- `theater/*`, `use-session-recording`, `use-session-proctoring`, and `public-playback-context` (the
  hooks reference `usePublicPlayback()`; the context file stays even though no provider wraps it on the
  authenticated path — the hooks' public branch is simply never taken there).

---

## Testing (end-to-end)

- **Backend:** updated `test_share_actor.py` (link base → `candidate_session_base_url`); existing
  public-endpoint + token tests stay green.
- **Session:** `npm run build`, `npm run lint`, `npm run type-check`; a render/smoke test for the
  `/recordings/[token]` route; manual `curl -I` of the route asserting the storage origin appears in CSP
  `media-src`.
- **App:** `npm run build` + `npm run lint` after the deletions (no dangling imports); existing tests
  green.
- **Live (LAN):** `./scripts/demo.sh lan` → run an interview → share the report PDF → from a second
  device (off the LAN, over ngrok) open both the reel and full-session links and confirm playback.

## Accepted drift / residual risk

- **Two copies of the theater tree** (authenticated in `frontend/app`, public in `frontend/session`).
  This is the price of two separate Next apps with no shared workspace package; a shared package is a
  larger refactor and out of scope.
- **Closure size unknown** until the first implementation task maps it; flagged there before the copy.
- The `NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN` default is an R2 wildcard. An S3-hosted deployment must set
  it explicitly; the `.env.local.example` note calls this out.
