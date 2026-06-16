# Public Recordings Share — Design

**Date:** 2026-06-16
**Status:** Approved (design), pending implementation
**Author:** Engine/platform work, brainstormed with Ishant

---

## Problem

The shared report PDF (Phase 3D report-sharing-pdf, merged 2026-06-16) contains a
"▶ See full session recording" button that currently points at a dead stub URL
(`{frontend_base_url}/shared-session/coming-soon`, `reporting/actors.py:450`).

We want an external recruiter — someone who received the PDF by email and does **not**
have a BinQle account — to click that button and watch the candidate's **session
recording** (with proctoring/scores/transcript/decision, i.e. the full ReviewTheater
experience) **and** the **highlight reel**, on a single public page.

Hard constraints:

- The page must **not** be guessable, indexable, or enumerable. Anyone without the link
  must not be able to reach any candidate's videos.
- The recruiter **report page itself** stays private (auth-gated). Only a separate
  `/recordings/...` playback surface is public.
- The videos themselves (R2 objects) stay private — presigned access only.

## Decisions (locked with the user)

1. **Page content:** full **ReviewTheater** (recording + proctoring + scores + transcript
   + AI decision) **plus** the highlight reel. Rationale: the external recruiter already
   received the complete evaluation in the PDF, so the video page mirrors the internal
   playback experience. *(Privacy note: this is maximal data exposure on a capability URL.
   It is acceptable because it is gated behind a 256-bit unguessable, revocable token and
   the recipient already holds the full PDF report.)*
2. **Link lifetime:** long-lived (default **1 year**) **and revocable**. The PDF can sit
   in an inbox indefinitely, so a short expiry would make the button rot; revocation is the
   kill switch.
3. **Link creation:** minted automatically as part of the **existing email-share flow**.
   No new recruiter "create link" UI.
4. **Revocation UI:** **backend-only for now** (API endpoint + DB). No recruiter-facing
   revoke control in this iteration.
5. **Security model:** **opaque capability link.** A 256-bit random token in the URL,
   stored only as a keyed HMAC hash at rest. Possession of the link = access. Chosen over
   a signed JWT (can't be revoked without a denylist — and we need revocable) and over a
   link-plus-OTP second factor (burdens the external recruiter, contradicts "anyone with
   the PDF can view").

## Architecture

### The durable secret vs. the ephemeral URLs

Two layers, deliberately separated:

- **Share token** — long-lived (1 year), revocable, lives in the PDF link. This is the
  *capability*. It is never a direct media URL.
- **R2 presigned URLs** — short-lived (`recording_signed_url_ttl_seconds`, ~1h), minted
  **fresh on every page load** by the public endpoint. The long-lived secret therefore
  never exposes a directly-fetchable video URL; a leaked presigned URL expires within the
  hour.

### Mint point — the share actor

In `reporting/actors.py::_share_report_pdf_async` (currently sets the stub URL at line
450), before building the PDF context:

1. Generate `token = secrets.token_urlsafe(32)` (256 bits).
2. Compute `token_hash = hmac_sha256(token, settings.recording_share_hmac_secret)`.
3. Persist on the existing `report_shares` row: `share_token_hash`, `share_expires_at`
   (`now + recording_share_ttl_days`, default 365).
4. Build `full_session_url = f"{settings.frontend_base_url}/recordings/{token}"` and pass
   it to `build_pdf_context` (replacing the stub).

Plaintext token lives **only** in the rendered PDF link — never stored, never logged.
Re-share / regenerate runs the actor again → a fresh row → a fresh token (the old row's
token keeps working until it expires or is revoked; per-recipient tokens give granular
revocation + audit). The HMAC keying means a DB-only leak yields no working links.

### DB change — migration `0062`

Add to `report_shares` (the table from `0061`):

| Column | Type | Notes |
|---|---|---|
| `share_token_hash` | `TEXT NULL` | HMAC-SHA256 hex of the plaintext token. **Unique index** (partial, `WHERE share_token_hash IS NOT NULL`) for O(1) lookup. |
| `share_expires_at` | `TIMESTAMPTZ NULL` | Capability expiry (mint + 1 year). |
| `revoked_at` | `TIMESTAMPTZ NULL` | Set by the revoke endpoint. |
| `last_viewed_at` | `TIMESTAMPTZ NULL` | Updated on each successful public fetch. |
| `view_count` | `INTEGER NOT NULL DEFAULT 0` | Incremented on each successful public fetch. |

`report_shares` already carries the canonical RLS pair (`0061`); these columns inherit it.
Downgrade drops the five columns + the index. No new table, so no `_TENANT_SCOPED_TABLES`
change.

### Backend — one public, token-gated endpoint

`GET /api/public/recordings/{token}` — **no Supabase auth**, registered on a router whose
prefix is allowlisted by `proxy.ts`.

Flow:

1. `token_hash = hmac_sha256(token, secret)`.
2. Look up the `report_shares` row by `share_token_hash` using a **bypass-RLS DB session**
   (tenant unknown until the row is read — mirrors how candidate-session resolves tenant
   from its token).
3. Validate: row exists **and** `revoked_at IS NULL` **and** `share_expires_at > now()`.
   Any failure → **uniform `404`** (no oracle distinguishing unknown / revoked / expired).
4. Resolve `tenant_id` from the row. Re-fetch through the **tenant-scoped** read path
   (`SET LOCAL app.current_tenant`), reusing the existing services:
   - report (`ReportRead`, with presigned reference photo + question thumbnails),
   - recording playback (presigned R2 URL + transcript + offset; **read-only** — no egress
     reconciliation, since sharing is always post-session),
   - proctoring analysis,
   - reel playback envelope (or an "ineligible/absent" marker).
5. Increment `view_count`, set `last_viewed_at` (best-effort; never blocks the response).
6. Return one envelope:
   `{ report, recording, proctoring, reel, candidate_name, job_title, stage_label }`.

`POST /api/public/recordings/{share_id}/revoke` is **rejected** as a design choice — revoke
is an authenticated recruiter action:

`POST /api/reports/session/{session_id}/shares/{share_id}/revoke` — RBAC `reports.view`
(or `super_admin`), tenant-scoped, sets `revoked_at = now()`, writes audit event
`session_report.share_revoked`. (No recruiter UI this iteration; callable via API.)

**Rate limits** (new "public share" class; add the row to root `CLAUDE.md` table):

| Endpoint | Per-IP | Per-token |
|---|---|---|
| `GET /api/public/recordings/{token}` | 30/min | 60/hour |

### Frontend — new public route

`frontend/app/app/recordings/[token]/page.tsx` (a top-level route segment, **outside**
`(dashboard)` so it does not inherit the auth-guard layout).

- **`proxy.ts`:** allowlist the page prefix `/recordings/` and the API prefix
  `/api/public/recordings/` so both bypass the Supabase session gate. `proxy.ts` is on the
  100%-branch coverage gate — add tests for the new allow paths.
- **Robots:** `noindex, nofollow` via Next route metadata + an `X-Robots-Tag: noindex`
  response header (set in `next.config.ts` for the `/recordings/*` path) — un-indexable.
- The page fetches `GET /api/public/recordings/{token}` **without** an `Authorization`
  header, then renders the existing **`ReviewTheater`** (recording + proctoring + scores +
  transcript + decision) and the **reel player**, fed from the envelope.
- Error states: invalid/expired/revoked → a clean "This link is no longer available" page
  (the API returns 404). Recording not yet ready → "not available yet." Reel absent /
  ineligible → hide the reel tab; recording still plays.

### Component reuse without duplication

`ReviewTheater` / `ReelCard` today self-fetch via authenticated hooks
(`useSessionRecording`, `useSessionProctoring`, `useReel`) that attach a Supabase token and
hit the private `/api/reports/...` endpoints. To reuse the *presentation* on the public
page without forking it:

- Add an optional **`shareToken`** parameter to those hooks (and their `lib/api`
  fetchers). When present, the hook targets the public endpoint and **omits** the
  `Authorization` header; when absent, behavior is unchanged for the recruiter app.
- The report data that `ReviewTheater` needs as a prop (scores/decision/questions) comes
  from the **same public envelope** on the public page (no recruiter report-page wrapper).

This is parameterization, not a parallel component tree — `ReviewTheater`'s rendering code
is untouched.

### Data flow

```
PDF "See full session recording" button
  → /recordings/<token>                         (public Next page, noindex)
    → GET /api/public/recordings/<token>         (no Supabase auth)
       → hmac(token) → report_shares row (bypass RLS)
       → validate (exists / not revoked / not expired) else 404
       → tenant-scope → re-fetch report + recording + proctoring + reel, presign R2 fresh
       → bump view_count / last_viewed_at
       → envelope
    → ReviewTheater (recording + proctoring + scores + transcript + decision) + Reel player
```

## Config / secrets

Add to `app/config.py` (pydantic-settings), following the `candidate_jwt_secret` pattern:

- `recording_share_hmac_secret: str = ""` — keys the token hash. Required in non-test envs
  via a `field_validator` (error message documents `openssl rand -hex 32`). *(Alternative:
  reuse the existing OTP HMAC key. A dedicated secret is preferred for blast-radius
  isolation and independent rotation.)*
- `recording_share_ttl_days: int = 365` — capability lifetime.

Document both in `.env.example`. Rotation: rotating the HMAC secret invalidates all
outstanding share links (acceptable — they can be re-shared); note this in the rotation
runbook.

## Security properties summary

- **Unguessable:** 256-bit token → 2^256 space; not brute-forceable or enumerable.
- **Un-indexable:** `noindex, nofollow` meta + `X-Robots-Tag` header on `/recordings/*`.
- **No DB-leak exposure:** only the keyed HMAC hash is stored; plaintext lives only in the PDF.
- **No oracle:** unknown / expired / revoked all return an identical `404`.
- **Revocable + bounded:** `revoked_at` kill switch + 1-year `share_expires_at`.
- **Media stays private:** R2 presigned URLs minted fresh per load, ~1h TTL.
- **Rate-limited:** per-IP + per-token caps on the public endpoint.
- **No PII in logs:** log `share_id` + `session_id`; never the token, never recipient email
  (mask per existing share-actor convention); access tracked via row counters, not full
  audit (mint + revoke *are* audited).
- **Tenant isolation intact:** after token→row resolution, all reads go through the
  tenant-scoped path; a token can only ever reach its own tenant's session.

## Testing

**Backend:**
- valid token → full envelope (report + recording + proctoring + reel).
- revoked / expired / unknown / malformed token → uniform `404` (no distinguishing detail).
- token resolves to exactly one tenant; reads cannot cross tenants.
- `share_token_hash` is a hash, never the plaintext (assert plaintext absent from row + logs).
- `view_count` / `last_viewed_at` advance on success; failure path doesn't block response.
- revoke endpoint: RBAC-gated, sets `revoked_at`, writes audit; subsequent public fetch → 404.
- rate-limit trip (per-IP and per-token).
- reel-absent / recording-not-ready envelope shapes.

**Frontend:**
- `proxy.ts` lets `/recordings/*` and `/api/public/recordings/*` through unauthenticated
  (this file is on the 100%-branch gate).
- public page renders ReviewTheater + reel from a mocked envelope.
- reel-absent path hides the reel tab; recording still renders.
- invalid-link (404) → "no longer available" page, no crash.
- `shareToken`-mode hooks omit the `Authorization` header and target the public endpoint.

## Out of scope (this iteration)

- Recruiter-facing revoke/list UI (backend-only revoke for now).
- Per-recipient analytics dashboards (we store counters; no UI).
- Watermarking / download protection on the videos (capability URL + presigned R2 only).
- Second-factor (email/OTP) gating of the public link.

## Files touched (anticipated)

**Backend**
- `migrations/versions/0062_report_share_tokens.py` — new columns + partial unique index.
- `app/modules/reporting/models.py` — `ReportShare` new columns.
- `app/modules/reporting/actors.py` — mint token, set real `full_session_url`.
- `app/modules/reporting/` — new public router + share-token service helpers (hash/verify),
  revoke endpoint on the existing reports router.
- `app/config.py` — `recording_share_hmac_secret`, `recording_share_ttl_days` + validators.
- `app/main.py` — register the public router (no auth middleware on its prefix).
- `.env.example` — document the new secrets.
- `tests/` — endpoint + security + rate-limit tests.
- root `CLAUDE.md` — rate-limit table row.

**Frontend**
- `app/recordings/[token]/page.tsx` — new public page (+ `loading.tsx`, `error.tsx`).
- `proxy.ts` — allowlist `/recordings/` + `/api/public/recordings/`.
- `next.config.ts` — `X-Robots-Tag: noindex` for `/recordings/*`.
- `lib/api/reports.ts`, `lib/api/reels.ts` — `shareToken` mode + public envelope fetcher.
- `lib/hooks/use-session-recording.ts`, `use-session-proctoring.ts`, `use-reel.ts` —
  optional `shareToken`.
- `tests/` — proxy allowlist, public-page render, hook share-token mode.
```
