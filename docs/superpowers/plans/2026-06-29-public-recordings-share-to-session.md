# Move Public Recordings Share Page → Candidate Session App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the public, token-gated `/recordings/<token>` playback page from the recruiter app (`frontend/app`) to the candidate session app (`frontend/session`) and base the shared-PDF link on `candidate_session_base_url`, so PDF links work over the session ngrok tunnel (and locally) without the LAN-only workaround.

**Architecture:** Backend repoints the PDF link base (1 setting) and a config setting is deleted. `demo.sh` loses three LAN-only hacks. The full 49-file `PublicRecordingsView` import closure is copied into a **self-contained, namespaced** subtree `frontend/session/components/recordings/**` (session already has its own `components/px`, `lib/utils`, `lib/brand`, `lib/api/client` — so we must NOT mirror app paths). The two supabase-tainted hooks are replaced with public-only variants that read solely from `PublicPlaybackProvider` context, keeping the session app free of `@supabase`. A new env var + CSP edit allow the R2 video origin. The old app page is deleted.

**Tech Stack:** Next.js 16 App Router (React 19), TypeScript, Tailwind v4 (`@theme inline` + `--px-*` CSS vars), `@tanstack/react-query`, `lucide-react`, `sonner`, `clsx`+`tailwind-merge`; backend FastAPI + Dramatiq + pytest.

## Global Constraints

- `frontend/session` MUST NOT depend on `@supabase/*` or read `NEXT_PUBLIC_SUPABASE_*`. (Pre-merge gate: `grep @supabase frontend/session/package.json` returns nothing; additionally `grep -rn "@supabase\|lib/auth/tokens\|lib/supabase" frontend/session/components/recordings frontend/session/lib` must be empty.)
- `frontend/app` MUST NOT depend on `livekit-*` or import from `components/agents-ui/` / `components/ai-elements/`. (This change only *removes* files from `frontend/app`.)
- Do NOT use PostgREST; no data-access changes here.
- All real-time/LLM model IDs, secrets, and URLs come from config/env — never hardcoded.
- `nexus-pdf-worker` has NO hot-reload — after editing `reporting/actors.py` or `reporting/pdf/`, it must be restarted (`docker compose up -d --force-recreate nexus-pdf-worker`) for live verification.
- TDD where unit-testable (backend, env schema); for the frontend port the gates are `npm run build`, `npm run lint`, `npm run type-check`, a render smoke test, and a `curl -I` CSP assertion.
- Commit after every task.

---

## File Structure

**Backend (modify):**
- `backend/nexus/app/modules/reporting/actors.py` — link base → `candidate_session_base_url`.
- `backend/nexus/app/config.py` — delete `recording_share_base_url` + its validator.
- `backend/nexus/tests/reporting/test_share_actor.py` — update the override test.

**Scripts (modify):**
- `scripts/demo.sh` — remove `RECORDING_SHARE_BASE_URL` rewrite, `frontend/app/.env.local` rewrite + backup/restore, LAN-IP CORS entry, and related status/comment lines.

**Session app (create) — namespaced port under `frontend/session/`:**
- `app/recordings/[token]/page.tsx`, `app/recordings/[token]/layout.tsx`, `app/recordings/[token]/loading.tsx`, `app/recordings/[token]/error.tsx`
- `app/recordings/recordings-theme.css` — scoped daylight `--px-*` block
- `components/recordings/PublicRecordingsView.tsx`
- `components/recordings/SessionPlayback.tsx`, `ScoreGauge.tsx`, `StarRating.tsx`, `report-format.ts`, `report.css`
- `components/recordings/theater/` — `ReelTheater.tsx`, `ReviewTheater.tsx`, `GlassBackdrop.tsx`, `TheaterStage.tsx`, `VideoControls.tsx`, `useVideoController.ts`, `QuestionRail.tsx`, `TheaterTopBar.tsx`, `ThisMomentPanel.tsx`, `useTheaterState.ts`, `timeline-model.ts`, `theater.css`
- `components/recordings/px/` — copy of app `components/px/*` (16 files + `index.ts`)
- `components/recordings/api/` — `reports.ts`, `reels.ts`, `client.ts` (copied; client base+ngrok adapted)
- `components/recordings/hooks/` — `public-playback-context.tsx`, `use-session-recording.ts` (public-only), `use-session-proctoring.ts` (public-only)

**Session app (modify):**
- `lib/env.ts` — add `NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN`.
- `proxy.ts` — add media origin to `media-src` / `img-src` / `connect-src`.
- `app/globals.css` — back-fill any missing `@theme inline` `--color-px-*` tokens.
- `.env.local.example` — document the new env var.
- `tests/` — env-schema test, render smoke test.

**App (delete):**
- `frontend/app/app/recordings/` (whole dir), `frontend/app/components/dashboard/reports/PublicRecordingsView.tsx`, the `/recordings/` bypass in `frontend/app/proxy.ts`, and `reportsApi.getPublicRecordings` from `frontend/app/lib/api/reports.ts`.

---

## Task 1: Backend — repoint PDF link base to `candidate_session_base_url`

**Files:**
- Modify: `backend/nexus/app/modules/reporting/actors.py` (in `_share_report_pdf_async`, the `share_base = ...` line, ~line 487)
- Modify: `backend/nexus/app/config.py` (delete `recording_share_base_url` + `_strip_recording_share_trailing_slash`, ~lines 553–565)
- Test: `backend/nexus/tests/reporting/test_share_actor.py`

**Interfaces:**
- Consumes: `settings.candidate_session_base_url` (already exists; `config.py:573`, trailing-slash-stripped at `config.py:575`).
- Produces: PDF links of the form `{candidate_session_base_url}/recordings/{token}?view=full|reel`.

- [ ] **Step 1: Update the share-actor test to assert the new base**

In `backend/nexus/tests/reporting/test_share_actor.py`, find `test_share_actor_uses_recording_share_base_url_override`. Rename it to `test_share_actor_uses_candidate_session_base_url` and rewrite its body so it sets `settings.candidate_session_base_url` and asserts the link uses it. Keep all other setup identical to the existing test. The assertion block becomes:

```python
    # The recordings link is based on candidate_session_base_url (the session
    # app), so a shared PDF opened over the session ngrok tunnel reaches a real
    # page. (Was recording_share_base_url || frontend_base_url before 2026-06-29.)
    monkeypatch.setattr(settings, "candidate_session_base_url", "https://sess.example.ngrok.app")
    # ... run the actor as the existing test does ...
    ctx = build_pdf_context_mock.call_args.kwargs  # however the existing test captures it
    assert ctx["full_session_url"] == "https://sess.example.ngrok.app/recordings/" + token + "?view=full"
    assert ctx["reel_url"] == "https://sess.example.ngrok.app/recordings/" + token + "?view=reel"
```

Match the existing test's mechanism for capturing the URLs (it already asserts on `?view=full`); only the base setting and expected prefix change. Remove every remaining reference to `recording_share_base_url` in this test file.

- [ ] **Step 2: Run the test, verify it FAILS**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/reporting/test_share_actor.py -v`
Expected: FAIL — actor still builds the URL from `recording_share_base_url or frontend_base_url`, so the asserted prefix won't match (or `settings.recording_share_base_url` AttributeError once config is changed — order doesn't matter, it must be red before Step 3).

- [ ] **Step 3: Repoint the actor**

In `backend/nexus/app/modules/reporting/actors.py`, replace the base-resolution line (and its now-stale comment) inside `_share_report_pdf_async`:

```python
        # was:
        #   share_base = settings.recording_share_base_url or settings.frontend_base_url
        # The recordings page lives in the candidate session app (frontend/session),
        # which is the surface already tunneled in LAN/demo mode and set as
        # CANDIDATE_SESSION_BASE_URL. Local default is http://localhost:3002.
        # See docs/superpowers/specs/2026-06-29-public-recordings-share-to-session-design.md
        share_base = settings.candidate_session_base_url
```

Leave the `full_session_url` / `reel_url` f-strings and the `?view=` params exactly as they are.

- [ ] **Step 4: Delete the dead setting**

In `backend/nexus/app/config.py`, delete the `recording_share_base_url: str | None = None` field AND the `_strip_recording_share_trailing_slash` validator (the whole `@field_validator("recording_share_base_url")` block), plus the comment paragraph above the field.

- [ ] **Step 5: Verify no dangling references**

Run: `cd backend/nexus && grep -rn "recording_share_base_url\|RECORDING_SHARE_BASE_URL" app/ tests/`
Expected: no matches (the `.env.example`/`.env` entries are handled in Task 2's demo.sh; if `.env.example` documents `RECORDING_SHARE_BASE_URL`, remove that line here too and note it).

Also check `.env.example`:
Run: `cd backend/nexus && grep -n "RECORDING_SHARE_BASE_URL" .env.example` — if present, delete that line and its comment.

- [ ] **Step 6: Run the test, verify it PASSES**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/reporting/test_share_actor.py tests/test_public_recordings.py tests/test_share_tokens.py -v`
Expected: PASS (the public endpoint + token tests are unaffected and must stay green).

- [ ] **Step 7: Commit**

```bash
git add backend/nexus/app/modules/reporting/actors.py backend/nexus/app/config.py backend/nexus/tests/reporting/test_share_actor.py backend/nexus/.env.example
git commit -m "feat(reporting): base recordings-share link on candidate_session_base_url

The public /recordings page is moving to the candidate session app, which is
the surface already tunneled in LAN/demo mode. Drop the recording_share_base_url
override (was a LAN recruiter-app workaround).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `scripts/demo.sh` — remove the LAN-only recruiter-app hacks

**Files:**
- Modify: `scripts/demo.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: a `demo.sh lan` that no longer rewrites `RECORDING_SHARE_BASE_URL` or `frontend/app/.env.local`, and no longer adds `http://<LAN-IP>:3000` to CORS. `CANDIDATE_SESSION_BASE_URL` rewrite (already present) is what now drives the link.

- [ ] **Step 1: Remove the `RECORDING_SHARE_BASE_URL` rewrite**

In `cmd_lan`, delete these two lines (~335–336):
```bash
  info "Pointing the recordings share link at the recruiter app on the LAN…"
  set_env_var "$BACKEND_ENV" "RECORDING_SHARE_BASE_URL" "http://${lan_ip}:3000"
```

- [ ] **Step 2: Remove the LAN-IP CORS entry**

Change the `set_cors_origins` call (~337) from:
```bash
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "http://${lan_ip}:3000" "${LOCAL_ORIGINS[@]}"
```
to:
```bash
  set_cors_origins "$BACKEND_ENV" "$session_url" "$backend_url" "${LOCAL_ORIGINS[@]}"
```

- [ ] **Step 3: Remove the `frontend/app/.env.local` rewrite**

Delete these two lines (~343–344) in `cmd_lan`:
```bash
  info "Rewriting frontend/app/.env.local (NEXT_PUBLIC_API_URL → LAN backend)…"
  set_env_var "$APP_ENV" "NEXT_PUBLIC_API_URL" "http://${lan_ip}:8000"
```

- [ ] **Step 4: Remove APP_ENV backup/restore + path constants**

- Remove `APP_ENV="$REPO_ROOT/frontend/app/.env.local"` (~47) and `APP_ENV_BACKUP="$STATE_DIR/app.env.local.backup"` (~55).
- In `require_files`, remove the `[[ -f "$APP_ENV" ]] || die ...` line (~99).
- In `backup_envs_once`, remove the `[[ -f "$APP_ENV_BACKUP" ]] || cp "$APP_ENV" "$APP_ENV_BACKUP"` line (~179).
- In `restore_envs`, remove the whole `if [[ -f "$APP_ENV_BACKUP" ]]; then ... fi` block (~194–198).

- [ ] **Step 5: Clean status output + Next-steps guidance + header comment**

- In `cmd_status`, remove the `backend_rec_share` and `app_api` reads and the two `echo` lines printing `RECORDING_SHARE_BASE_URL` and `frontend/app NEXT_PUBLIC_API_URL` (~435–437, 445–446).
- In the `cmd_lan` heredoc "Next steps", delete step 2 (the "(Re)start the recruiter app … reach it via the LAN IP" paragraph, ~370–376) since the recordings page no longer lives there; renumber following steps. Keep the recruiter-dashboard-on-localhost guidance only if still accurate.
- Update the top-of-file header comment block (the "This script ALSO touches (added 2026-06-18): frontend/app/.env.local …" paragraph, ~20–24) to state the recordings link now rides `CANDIDATE_SESSION_BASE_URL` (the session ngrok URL) and the script no longer touches `frontend/app/.env.local` or `RECORDING_SHARE_BASE_URL`.

- [ ] **Step 6: Syntax-check the script**

Run: `bash -n scripts/demo.sh`
Expected: no output (valid syntax).

Run: `cd /home/ishant/Projects/ProjectX && ./scripts/demo.sh status`
Expected: prints mode + the remaining env lines without `RECORDING_SHARE_BASE_URL` / `frontend/app NEXT_PUBLIC_API_URL`, no errors.

- [ ] **Step 7: Commit**

```bash
git add scripts/demo.sh
git commit -m "chore(demo): drop LAN recruiter-app recordings hacks

The recordings page now rides CANDIDATE_SESSION_BASE_URL (already rewritten to
the session ngrok URL). Remove the RECORDING_SHARE_BASE_URL rewrite, the
frontend/app/.env.local rewrite + backup/restore, and the LAN-IP CORS entry.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Session — env var + CSP for the R2 media origin

**Files:**
- Modify: `frontend/session/lib/env.ts`
- Modify: `frontend/session/proxy.ts` (CSP block, ~lines 40–57)
- Modify: `frontend/session/.env.local.example`
- Test: `frontend/session/tests/env.test.ts` (existing env-schema test file; create if absent)

**Interfaces:**
- Produces: `env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN: string` (default `https://*.r2.cloudflarestorage.com`), present in CSP `media-src` / `img-src` / `connect-src`.

- [ ] **Step 1: Write the failing env-schema test**

In `frontend/session/tests/env.test.ts` add:

```ts
import { describe, it, expect } from 'vitest'
import { envSchema } from '@/lib/env'

describe('NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN', () => {
  const base = { NEXT_PUBLIC_API_URL: 'https://api.example.com' }

  it('defaults to the R2 wildcard when unset', () => {
    const env = envSchema.parse({ ...base })
    expect(env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN).toBe('https://*.r2.cloudflarestorage.com')
  })

  it('passes through an explicit origin', () => {
    const env = envSchema.parse({ ...base, NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN: 'https://x.s3.us-east-1.amazonaws.com' })
    expect(env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN).toBe('https://x.s3.us-east-1.amazonaws.com')
  })
})
```

- [ ] **Step 2: Run it, verify FAIL**

Run: `cd frontend/session && npm run test -- env`
Expected: FAIL — `NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN` is undefined on the parsed env.

- [ ] **Step 3: Add the field to the schema**

In `frontend/session/lib/env.ts`, add to `envSchema` (after `NEXT_PUBLIC_LIVEKIT_WS_URL`):

```ts
  // CSP origin(s) for presigned session-recording / reel video + thumbnails.
  // The <video src> and reference-photo <img> load from the object store
  // (Cloudflare R2: https://<account>.r2.cloudflarestorage.com, or an AWS S3
  // regional host). Space-separated list allowed. Defaults to the R2 wildcard
  // so it works out-of-box; an S3-hosted deploy MUST set this explicitly.
  NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN: z
    .string()
    .optional()
    .transform((v) => (v && v.length > 0 ? v : 'https://*.r2.cloudflarestorage.com')),
```

And add it to the `envSchema.parse({...})` call object:
```ts
  NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN: process.env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN,
```

- [ ] **Step 4: Run it, verify PASS**

Run: `cd frontend/session && npm run test -- env`
Expected: PASS.

- [ ] **Step 5: Wire the origin into CSP**

In `frontend/session/proxy.ts`, near the top of the request handler where `apiUrl` is resolved, add:
```ts
  const mediaOrigin = process.env.NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN || "https://*.r2.cloudflarestorage.com";
```
Then update three CSP directives in the `cspHeader` array:
```ts
    "img-src 'self' data: blob: " + mediaOrigin,
    "media-src 'self' blob: mediastream: " + mediaOrigin,
```
and append `mediaOrigin` to the `connect-src` line:
```ts
    `connect-src 'self' ${apiUrl}${isDev ? " ws://localhost:* http://localhost:*" : ""} ${process.env.NEXT_PUBLIC_LIVEKIT_WS_URL ?? "wss://*.livekit.cloud https://*.livekit.cloud"} ${mediaOrigin}`,
```

- [ ] **Step 6: Document the env var**

In `frontend/session/.env.local.example`, add:
```bash
# CSP origin for presigned recording/reel video + thumbnails (the public
# /recordings page). Default works for Cloudflare R2; set explicitly for AWS S3.
# Example (S3): https://<bucket>.s3.<region>.amazonaws.com
NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN=https://*.r2.cloudflarestorage.com
```

- [ ] **Step 7: Commit**

```bash
git add frontend/session/lib/env.ts frontend/session/proxy.ts frontend/session/.env.local.example frontend/session/tests/env.test.ts
git commit -m "feat(session): allow recording media origin in CSP

Add NEXT_PUBLIC_RECORDING_MEDIA_ORIGIN (default R2 wildcard) to media-src,
img-src, connect-src so the upcoming public /recordings page can play
presigned R2/S3 video.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Session — copy the closure into a namespaced subtree + rewrite imports

This task copies the 43 portable closure files (everything EXCEPT the route files, the 2 tainted hooks, and the supabase/auth files) into `frontend/session/components/recordings/` and rewrites cross-references. The route files (Task 7), public-only hooks (Task 5), and API client adaptation (Task 6) follow.

**Files:**
- Create (copied): `frontend/session/components/recordings/{PublicRecordingsView.tsx,SessionPlayback.tsx,ScoreGauge.tsx,StarRating.tsx,report-format.ts,report.css}`
- Create (copied): `frontend/session/components/recordings/theater/*` (all 12 files)
- Create (copied): `frontend/session/components/recordings/px/*` (all 17 files incl. `index.ts`)
- Create (copied): `frontend/session/components/recordings/api/{reports.ts,reels.ts,client.ts}`
- Create (copied): `frontend/session/components/recordings/hooks/public-playback-context.tsx`

**Interfaces:**
- Consumes: session `@/lib/utils` (`cn`) and `@/lib/brand` (`brand` — shape-compatible: `{name, logo:{wordmark,mark}}`).
- Produces: a namespaced component tree where all intra-closure imports resolve under `@/components/recordings/...`, with NO import of `@/lib/auth/tokens`, `@/lib/supabase/*`, or `@supabase/*`.

- [ ] **Step 1: Copy the portable files into the namespace**

```bash
cd /home/ishant/Projects/ProjectX/frontend
mkdir -p session/components/recordings/theater session/components/recordings/px session/components/recordings/api session/components/recordings/hooks

# reports tree (non-theater)
cp app/components/dashboard/reports/PublicRecordingsView.tsx session/components/recordings/
cp app/components/dashboard/reports/SessionPlayback.tsx session/components/recordings/
cp app/components/dashboard/reports/ScoreGauge.tsx session/components/recordings/
cp app/components/dashboard/reports/StarRating.tsx session/components/recordings/
cp app/components/dashboard/reports/report-format.ts session/components/recordings/
cp app/components/dashboard/reports/report.css session/components/recordings/

# theater
cp app/components/dashboard/reports/theater/*.{tsx,ts,css} session/components/recordings/theater/

# px design system (whole dir — self-contained, supabase-free)
cp app/components/px/*.{tsx,ts} session/components/recordings/px/

# api type+client modules
cp app/lib/api/reports.ts app/lib/api/reels.ts app/lib/api/client.ts session/components/recordings/api/

# public playback context (the supabase-free hook file)
cp app/lib/hooks/public-playback-context.tsx session/components/recordings/hooks/
```

- [ ] **Step 2: Rewrite intra-closure import specifiers**

Run these `sed` rewrites over the copied tree so all `@/...` closure imports point at the namespace. (macOS: use `sed -i ''`; Linux: `sed -i`.)

```bash
cd /home/ishant/Projects/ProjectX/frontend/session/components/recordings
FILES=$(grep -rl '@/' . || true)

# reports tree → namespace root
sed -i 's#@/components/dashboard/reports/theater/#@/components/recordings/theater/#g' $FILES
sed -i 's#@/components/dashboard/reports/#@/components/recordings/#g' $FILES
# px → namespaced px
sed -i 's#@/components/px#@/components/recordings/px#g' $FILES
# api type/client modules → namespaced api
sed -i 's#@/lib/api/reports#@/components/recordings/api/reports#g' $FILES
sed -i 's#@/lib/api/reels#@/components/recordings/api/reels#g' $FILES
sed -i 's#@/lib/api/client#@/components/recordings/api/client#g' $FILES
# hooks → namespaced hooks
sed -i 's#@/lib/hooks/public-playback-context#@/components/recordings/hooks/public-playback-context#g' $FILES
sed -i 's#@/lib/hooks/use-session-recording#@/components/recordings/hooks/use-session-recording#g' $FILES
sed -i 's#@/lib/hooks/use-session-proctoring#@/components/recordings/hooks/use-session-proctoring#g' $FILES
# NOTE: @/lib/utils and @/lib/brand are intentionally LEFT pointing at session's
# own shared modules (cn is identical clsx+twMerge; brand shape is compatible).
```

- [ ] **Step 3: Confirm no supabase/auth specifiers survive in the copied tree**

Run: `cd /home/ishant/Projects/ProjectX && grep -rn "@supabase\|@/lib/auth/tokens\|@/lib/supabase\|getFreshSupabaseToken" frontend/session/components/recordings`
Expected: matches ONLY inside `hooks/use-session-recording.ts` / `use-session-proctoring.ts` — and those two files are NOT copied in this task (they're rewritten clean in Task 5). So with only `public-playback-context.tsx` present, expected output is **empty**. If anything else matches, stop and inspect — a closure file imports auth that the earlier analysis missed.

- [ ] **Step 4: Confirm leftover `@/` imports are only session-shared modules**

Run: `cd /home/ishant/Projects/ProjectX/frontend/session/components/recordings && grep -rhoE "@/[a-zA-Z0-9_/-]+" . | sort -u`
Expected: every line is one of `@/components/recordings/...`, `@/lib/utils`, or `@/lib/brand`. Any other `@/lib/...` or `@/components/...` means a closure edge was missed — add it to the copy list and re-run Steps 1–2.

- [ ] **Step 5: Commit (intermediate — does not type-check yet; hooks + routes follow)**

```bash
cd /home/ishant/Projects/ProjectX
git add frontend/session/components/recordings
git commit -m "feat(session): vendor public-recordings component closure (namespaced)

Copy ReviewTheater/ReelTheater/SessionPlayback + px primitives + report
helpers + api type modules into components/recordings/, imports rewritten to
the namespace. Public-only hooks + route + client adaptation follow.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Session — public-only recording/proctoring hooks (de-taint)

**Files:**
- Create: `frontend/session/components/recordings/hooks/use-session-recording.ts`
- Create: `frontend/session/components/recordings/hooks/use-session-proctoring.ts`

**Interfaces:**
- Consumes: `usePublicPlayback()` from `@/components/recordings/hooks/public-playback-context`; types `RecordingPlayback` / `ProctoringAnalysis` from `@/components/recordings/api/reports`.
- Produces: `useSessionRecording(sessionId): UseQueryResult<RecordingPlayback>` and `useSessionProctoring(sessionId): UseQueryResult<ProctoringAnalysis>` — context-only, no auth, no `@supabase`. Same names/signatures the theater components already import.

- [ ] **Step 1: Write the public-only recording hook**

Create `frontend/session/components/recordings/hooks/use-session-recording.ts`:

```ts
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { type RecordingPlayback } from '@/components/recordings/api/reports'
import { usePublicPlayback } from '@/components/recordings/hooks/public-playback-context'

/**
 * Public /recordings/<token> variant: the recording is supplied, already
 * fetched, by PublicPlaybackProvider. No authenticated fetch, no polling, no
 * Supabase token (the session app must not depend on @supabase). Mirrors the
 * recruiter app's hook signature so the theater components are drop-in.
 */
export function useSessionRecording(sessionId: string): UseQueryResult<RecordingPlayback> {
  const pub = usePublicPlayback()
  return useQuery<RecordingPlayback>({
    queryKey: ['public-recording', sessionId],
    queryFn: async () => {
      if (!pub) {
        throw new Error('useSessionRecording requires PublicPlaybackProvider on the public recordings page')
      }
      return pub.recording
    },
    enabled: !!sessionId,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  })
}
```

- [ ] **Step 2: Write the public-only proctoring hook**

Create `frontend/session/components/recordings/hooks/use-session-proctoring.ts`:

```ts
'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { type ProctoringAnalysis } from '@/components/recordings/api/reports'
import { usePublicPlayback } from '@/components/recordings/hooks/public-playback-context'

/**
 * Public /recordings/<token> variant: analysis is supplied by
 * PublicPlaybackProvider. No authenticated fetch, no polling, no Supabase.
 */
export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  const pub = usePublicPlayback()
  return useQuery<ProctoringAnalysis>({
    queryKey: ['public-proctoring', sessionId],
    queryFn: async () => {
      if (!pub) {
        throw new Error('useSessionProctoring requires PublicPlaybackProvider on the public recordings page')
      }
      return pub.proctoring
    },
    enabled: !!sessionId,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  })
}
```

- [ ] **Step 3: Verify the de-taint grep is clean across the whole subtree**

Run: `cd /home/ishant/Projects/ProjectX && grep -rn "@supabase\|getFreshSupabaseToken\|@/lib/auth/tokens\|@/lib/supabase" frontend/session/components/recordings`
Expected: **empty**.

- [ ] **Step 4: Commit**

```bash
git add frontend/session/components/recordings/hooks
git commit -m "feat(session): public-only recording/proctoring hooks (no Supabase)

Context-only variants that read PublicPlaybackProvider; drop the authenticated
fetch + getFreshSupabaseToken branch so the session app stays @supabase-free.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Session — adapt the namespaced API client (base URL + ngrok header)

**Files:**
- Modify: `frontend/session/components/recordings/api/client.ts`
- Modify (only if needed): `frontend/session/components/recordings/api/reports.ts` (the `getPublicRecordings` call path)

**Interfaces:**
- Consumes: `process.env.NEXT_PUBLIC_API_URL`.
- Produces: `reportsApi.getPublicRecordings(token): Promise<PublicRecordingsEnvelope>` issuing `GET ${NEXT_PUBLIC_API_URL}/api/public/recordings/{token}` with `ngrok-skip-browser-warning: '1'`, no auth header.

- [ ] **Step 1: Inspect the copied client**

Read `frontend/session/components/recordings/api/client.ts`. Confirm it resolves its base from `process.env.NEXT_PUBLIC_API_URL` (it does in app: `const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"`). Confirm it does NOT import `@supabase` / `@/lib/auth/tokens` (verified absent from the closure taint list; re-confirm by reading the imports).

- [ ] **Step 2: Add the ngrok-skip header to the fetch wrapper**

In `frontend/session/components/recordings/api/client.ts`, locate the `fetch(...)` call (the `apiFetch` helper) and ensure every request sends `'ngrok-skip-browser-warning': '1'` (so the backend ngrok tunnel's browser interstitial is skipped in LAN/demo mode). Merge it into the existing headers object, e.g.:

```ts
  const res = await fetch(url, {
    ...init,
    headers: {
      'ngrok-skip-browser-warning': '1',
      ...(init?.headers ?? {}),
    },
  })
```

(Adapt to the file's actual shape — the requirement is: the header is present on the public GET, and existing headers are preserved.)

- [ ] **Step 3: Type-check just the recordings subtree imports compile**

Run: `cd frontend/session && npx tsc --noEmit -p tsconfig.json 2>&1 | grep -i "components/recordings" | head -40`
Expected: no errors referencing `components/recordings`. (Full `type-check` runs in Task 9 once routes exist; this is an early signal that the copied tree + hooks + client resolve. If `getPublicRecordings` or any type is missing, fix the copy now.)

- [ ] **Step 4: Commit**

```bash
git add frontend/session/components/recordings/api
git commit -m "feat(session): adapt namespaced recordings API client (ngrok header)

Send ngrok-skip-browser-warning on the public recordings fetch so the link
works through the backend ngrok tunnel; base stays NEXT_PUBLIC_API_URL.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Session — route files, react-query provider, daylight theme scope

**Files:**
- Create: `frontend/session/app/recordings/[token]/page.tsx`
- Create: `frontend/session/app/recordings/[token]/layout.tsx`
- Create: `frontend/session/app/recordings/[token]/loading.tsx`
- Create: `frontend/session/app/recordings/[token]/error.tsx`
- Create: `frontend/session/app/recordings/recordings-theme.css`
- Modify: `frontend/session/app/globals.css` (back-fill missing `@theme inline` tokens)

**Interfaces:**
- Consumes: `PublicRecordingsView` from `@/components/recordings/PublicRecordingsView`; the daylight `--px-*` token block.
- Produces: the `/recordings/[token]` route rendering the full-parity page under `data-px-theme="daylight"`.

- [ ] **Step 1: Port the route files**

Read the four originals (`frontend/app/app/recordings/layout.tsx`, `[token]/page.tsx`, `[token]/loading.tsx`, `[token]/error.tsx`) and recreate them under `frontend/session/app/recordings/`, with these changes:
- `page.tsx`: import `PublicRecordingsView` from `@/components/recordings/PublicRecordingsView`. Keep the `useParams()` token extraction.
- `loading.tsx`: import `Skeleton` from `@/components/recordings/px` (namespaced).
- `error.tsx`: unchanged logic.
- `layout.tsx`: keep the `QueryClientProvider` setup (app's recordings layout already isolates a `QueryClient`); import `./recordings-theme.css`; wrap children in a container that sets the daylight theme and pins the Urbanist font, e.g.:

```tsx
'use client'
import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './recordings-theme.css'

export default function RecordingsLayout({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient())
  return (
    <QueryClientProvider client={qc}>
      <div data-px-theme="daylight" className="recordings-root min-h-dvh">
        {children}
      </div>
    </QueryClientProvider>
  )
}
```

(Font parity: the session root uses Inter/Fraunces. If exact Urbanist parity is wanted, load it via `next/font/google` in this layout and set it on `.recordings-root`; otherwise the daylight tokens alone give close parity. Keep it simple — daylight token scope is the load-bearing part.)

- [ ] **Step 2: Create the scoped daylight token stylesheet**

Copy the entire `[data-px-theme="daylight"] { ... }` block from `frontend/app/app/theme.css` (~lines 12–onward, the full block incl. the `--px-*` values and the `--background`/`--card`/etc. shadcn aliases) into `frontend/session/app/recordings/recordings-theme.css`. Then also import `report.css` + `theater.css` are already imported by their components; this file only carries the daylight token block. Verify the selector remains `[data-px-theme="daylight"]` so it activates only inside the wrapper.

- [ ] **Step 3: Back-fill missing `@theme inline` color tokens in session**

Tailwind v4 `@theme` cannot be scoped, so the `--color-px-*` utility set is global. Compare app vs session token lists:

Run: `diff <(grep -oE "color-px-[a-z0-9-]+" frontend/app/app/globals.css | sort -u) <(grep -oE "color-px-[a-z0-9-]+" frontend/session/app/globals.css | sort -u)`

For every token present in app but missing in session (known: at least `--color-px-accent-ink`), add the matching line to session's `@theme inline` block in `frontend/session/app/globals.css`, mapping to the `var(--px-*)` indirection, e.g.:
```css
  --color-px-accent-ink: var(--px-accent-ink);
```
This is additive (new utilities) and does not change session's existing candidate-interview styling, because the underlying `--px-accent-ink` is only defined inside the daylight scope.

Also confirm every `--px-*` raw var the daylight block omits but a component uses is covered — run after Step 2:
`grep -oE "var\(--px-[a-z0-9-]+\)" frontend/session/components/recordings -r | grep -oE "--px-[a-z0-9-]+" | sort -u` and confirm each appears in `recordings-theme.css`. Add any missing ones from app's daylight block.

- [ ] **Step 4: Commit**

```bash
git add frontend/session/app/recordings frontend/session/app/globals.css
git commit -m "feat(session): /recordings route + scoped daylight theme

Port the route files (react-query provider, loading/error) and scope the app's
daylight --px-* token block to the recordings subtree for visual parity; back-
fill missing @theme color tokens.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Session — build, lint, type-check, render smoke test, CSP assertion

**Files:**
- Create: `frontend/session/tests/recordings-route.test.tsx` (render smoke test)

**Interfaces:**
- Consumes: everything from Tasks 3–7.
- Produces: a green session build + a smoke test proving the page renders from a mocked envelope, and a verified CSP `media-src`.

- [ ] **Step 1: Type-check + lint the whole session app**

Run: `cd frontend/session && npm run type-check && npm run lint`
Expected: PASS. Fix any unresolved import / type error in `components/recordings/**` (most likely a missed closure edge or a token type mismatch). Do NOT silence with `any`; resolve the real import.

- [ ] **Step 2: Production build**

Run: `cd frontend/session && npm run build`
Expected: build succeeds and lists the `/recordings/[token]` route.

- [ ] **Step 3: Write a render smoke test**

Create `frontend/session/tests/recordings-route.test.tsx` that mounts `PublicRecordingsView` (or the page) wrapped in `PublicPlaybackProvider` + a `QueryClientProvider`, with a minimal mocked `PublicRecordingsEnvelope` (candidate name, a `report` stub, a `recording` with a fake `signed_url`, a `proctoring` stub, no reel), mock `getPublicRecordings` to resolve that envelope, and assert the candidate name + a "Full session" affordance render without throwing. Use the existing session vitest setup/patterns (`@testing-library/react`).

- [ ] **Step 4: Run the smoke test**

Run: `cd frontend/session && npm run test -- recordings-route`
Expected: PASS.

- [ ] **Step 5: Assert the CSP carries the media origin (dev server curl)**

```bash
cd frontend/session && (npm run dev >/tmp/sess-dev.log 2>&1 &) ; sleep 6
curl -sI http://localhost:3002/recordings/smoke-token | grep -i "content-security-policy"
```
Expected: the `Content-Security-Policy` header includes `media-src` with `https://*.r2.cloudflarestorage.com` (or your configured origin). Then stop the dev server (`pkill -f "next dev" || true`).

- [ ] **Step 6: Commit**

```bash
git add frontend/session/tests/recordings-route.test.tsx
git commit -m "test(session): smoke-test public recordings route + verify CSP

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: App — remove the now-dead public recordings page

**Files:**
- Delete: `frontend/app/app/recordings/` (whole dir)
- Delete: `frontend/app/components/dashboard/reports/PublicRecordingsView.tsx`
- Modify: `frontend/app/proxy.ts` (remove the `/recordings/` bypass)
- Modify: `frontend/app/lib/api/reports.ts` (remove `getPublicRecordings`)

**Interfaces:**
- Consumes: nothing.
- Produces: a recruiter app with no public recordings surface; the shared theater components + hooks remain for the authenticated report viewer.

- [ ] **Step 1: Delete the route dir + the public view component**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
git rm -r app/recordings
git rm components/dashboard/reports/PublicRecordingsView.tsx
```

- [ ] **Step 2: Remove the proxy bypass**

In `frontend/app/proxy.ts`, delete the public-recordings bypass block (~lines 69–73):
```ts
  // Public recordings share page — token-gated by the backend, no Supabase
  // session needed.
  if (path.startsWith("/recordings/")) {
    return supabaseResponse;
  }
```

- [ ] **Step 3: Remove the unused public API method**

In `frontend/app/lib/api/reports.ts`, delete the `getPublicRecordings` method (~lines 348–355) and the `PublicRecordingsEnvelope` type **only if** it is now unreferenced in `frontend/app`. Verify:
Run: `cd frontend/app && grep -rn "getPublicRecordings\|PublicRecordingsEnvelope\|PublicPlaybackProvider" --include=*.ts --include=*.tsx . | grep -v node_modules`
Delete `getPublicRecordings`. Keep `public-playback-context.tsx` + `usePublicPlayback` (still imported by the shared hooks) — confirm the grep shows those hooks still reference it; if `PublicPlaybackProvider` has zero remaining references that's expected (only the deleted public page used it) and the provider export can stay harmlessly, or be removed if you prefer — the hook `usePublicPlayback` MUST remain.

- [ ] **Step 4: Verify app still builds + lints (no dangling imports)**

Run: `cd frontend/app && npm run lint && npm run build`
Expected: PASS. If the build complains about a missing `getPublicRecordings`/`PublicRecordingsView` import, that import was a dangling reference — find and remove it.

- [ ] **Step 5: Confirm the proxy auth-gating test still passes**

Run: `cd frontend/app && npm run test -- proxy`
Expected: PASS. If a test asserted the `/recordings/` bypass, update it to reflect that `/recordings/` is no longer a special public path in the recruiter app (it should now be treated like any other gated route).

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add -A frontend/app
git commit -m "refactor(app): remove public /recordings page (moved to session app)

The token-gated recordings page now lives in frontend/session. Drop the route,
PublicRecordingsView, the proxy bypass, and the unused getPublicRecordings.
Shared theater components + hooks stay for the authenticated report viewer.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: End-to-end live verification (LAN/ngrok)

**Files:** none (manual verification + a final guard sweep).

- [ ] **Step 1: Global constraint guard sweep**

```bash
cd /home/ishant/Projects/ProjectX
grep @supabase frontend/session/package.json || echo "OK: session has no @supabase dep"
grep -rn "@supabase\|@/lib/auth/tokens\|@/lib/supabase" frontend/session/components/recordings frontend/session/lib && echo "FAIL: supabase taint" || echo "OK: no supabase taint"
grep -in livekit frontend/app/package.json && echo "FAIL: livekit in app" || echo "OK: app has no livekit dep"
```
Expected: the OK lines; no FAIL.

- [ ] **Step 2: Restart backend + workers so they read the new code/config**

```bash
cd /home/ishant/Projects/ProjectX && ./scripts/demo.sh lan
```
This recreates `nexus`/`nexus-worker`/`nexus-engine` and force-recreates `nexus-pdf-worker` (which renders the PDF link). Note the printed session ngrok URL.

- [ ] **Step 3: Start the session app**

```bash
cd frontend/session && npm run dev
```
Confirm it picks up the rewritten `NEXT_PUBLIC_API_URL` (backend ngrok) and `NEXT_PUBLIC_LIVEKIT_WS_URL`.

- [ ] **Step 4: Drive the flow**

Run an interview to completion → from the recruiter dashboard, generate the report → share the report PDF to an email (dry-run log or real). Inspect the PDF: the "Candidate highlight" + "Full session" buttons must point at `https://<session-ngrok>/recordings/<token>?view=reel|full`.

Run: `docker compose -f backend/nexus/docker-compose.yml logs --tail=200 nexus-pdf-worker | grep -iE "recordings|share"` to confirm the rendered link base is the session ngrok URL.

- [ ] **Step 5: Open the link from a different device (off the LAN, over the internet)**

On a phone on cellular (NOT the host WiFi), open the reel link from the PDF. Confirm: the page loads (session app over ngrok), the reel video plays (presigned R2 over the internet), and the "Full session" toggle plays the full recording with scores/proctoring/transcript. This is the core acceptance: a PDF recipient anywhere can watch the videos.

- [ ] **Step 6: Local-mode sanity**

```bash
cd /home/ishant/Projects/ProjectX && ./scripts/demo.sh local
```
Confirm `demo.sh status` shows no `RECORDING_SHARE_BASE_URL`. With the session app on `http://localhost:3002`, a freshly shared PDF's links point at `http://localhost:3002/recordings/<token>...` and open locally.

- [ ] **Step 7: Final commit (if any verification fixes were made)**

Commit any small fixes discovered during live verification with a clear message.

---

## Self-Review

**Spec coverage:**
- Backend repoint + delete setting → Task 1. ✔
- demo.sh simplification (3 hacks) → Task 2. ✔
- Full-parity port (49-file closure) → Tasks 4–7. ✔
- Supabase de-taint (public-only hooks) → Task 5. ✔
- CSS/theme parity (daylight scope + `@theme` back-fill) → Task 7. ✔
- API client + getPublicRecordings → Tasks 4 (copy) + 6 (adapt). ✔
- CSP + media origin env → Task 3. ✔
- Remove from app (route, view, proxy bypass, getPublicRecordings) → Task 9. ✔
- Constraint checks (no @supabase in session, no livekit in app) → Tasks 5/10. ✔
- Testing (backend unit, session build/lint/type-check/smoke/curl, live LAN) → Tasks 1, 8, 10. ✔

**Placeholder scan:** No "TBD"/"handle edge cases" left; copy/sed/grep commands and rewritten files are shown in full. The one judgment call (Urbanist font parity) is explicitly optional with a stated default.

**Type consistency:** Public-only hooks keep the exact names/signatures (`useSessionRecording`/`useSessionProctoring` → `UseQueryResult<RecordingPlayback|ProctoringAnalysis>`) the theater components import. `usePublicPlayback`/`PublicPlaybackData` names preserved. `getPublicRecordings(token) → PublicRecordingsEnvelope` unchanged. `share_base` produces `{base}/recordings/{token}?view=full|reel` matching the public endpoint + the `?view=` reader.

**Residual risk (carried from spec):** exact closure size confirmed at 49 files (Task 4 copies 43 + routes/hooks elsewhere); two copies of the theater tree is accepted drift; the R2 wildcard default needs an explicit value on S3 deploys (documented in Task 3 + `.env.local.example`).
