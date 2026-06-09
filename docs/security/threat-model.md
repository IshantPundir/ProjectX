# ProjectX Threat Model

This document tracks the STRIDE-per-trust-boundary view of the system.
It is updated whenever a new external service joins the data path, a
new auth surface is added, tenant-isolation boundaries change, or the
candidate-facing surface changes (per the root `CLAUDE.md` enterprise
operating standards).

PRs touching auth, RLS, or session paths must reference the relevant
section below.

---

## Phase 3C.2 — Interview engine integration (2026-04-29; superseded 2026-05-01 by Phase 3 modular-monolith merge)

Trust boundaries originally added by wiring `backend/interview_engine/` into Nexus, and how each one changed when the engine merged into the nexus monolith on 2026-05-01.

| Boundary | Original element (Phase 3C.2) | Post-Phase-3 element | STRIDE | Post-merge mitigation |
|---|---|---|---|---|
| Public → LiveKit room | Candidate access token (`room_join`) | unchanged | T (token leakage) | TTL = `stage_duration + 10min`; minted at `/start`, single-issuance; rejoin endpoint added Phase 3C.2 (mints fresh token without re-dispatching engine). |
| LiveKit → engine worker | Single-use HS256 dispatch JWT in metadata + `engine_dispatch_tokens` row | **Plain dispatch metadata `{session_id, tenant_id, correlation_id}` — no JWT, no DB row.** | T (replay across rooms) | Forging a CreateAgentDispatchRequest into the LiveKit room requires the LiveKit API key/secret. Equivalent to leaking the LiveKit creds — would already give the attacker far broader capabilities. Residual threat documented below. |
| Engine → Nexus internal API | HTTP `/api/internal/sessions/{id}/{config,results}` w/ engine JWT, `engine_token_uses` single-use record | **In-process `build_session_config` / `record_session_result` calls; no HTTP, no JWT.** Tenant scope enforced application-side via explicit `tenant_id` parameter on every query under a bypass-RLS session. | E (privilege escalation), I (information disclosure) | RLS bypass + explicit-tenant filter in `app/modules/interview_runtime/service.py`. Cross-tenant queries return "not found" (same shape as unknown session — no existence leak). `record_session_result` is gated on session.state='active' (atomic UPDATE); a second call after state='completed' is a silent no-op, not a duplicate. |
| Engine → OpenAI / Deepgram / Cartesia | Audio + transcript | unchanged | I (PII via 3rd party) | Consent gate (existing); `CandidateContext` drops email; OTel observability is vendor-neutral with both exporters off by default (Phase 1). |

### Phase 3 trust-boundary change — engine dispatch

The pre-Phase-3 design used a signed HMAC envelope (`engine_dispatch_tokens` JWT) to authenticate the engine's HTTP calls back to nexus. Phase 3 merged the engine into the same Python process and removed both the HTTP boundary and the JWT. The defense layer is now:

1. **Application-layer tenant filter.** Every query in `build_session_config` and `record_session_result` filters by an explicit `tenant_id` parameter sourced from the LiveKit dispatch metadata. The bypass-RLS session is necessary because the engine has no Supabase user context.
2. **State-machine atomicity replaces JWT single-use.** `record_session_result` uses an atomic `UPDATE … WHERE state='active'`; the second call after completion is a silent no-op rather than a duplicate audit row.
3. **LiveKit dispatch is the new attack surface.** A forged `CreateAgentDispatchRequest` would require a valid LiveKit API secret. The threat is equivalent to LiveKit-credential leakage, which would already grant much broader capabilities (room creation, recording, anywhere).

### Residual threat (accepted)

If the LiveKit API secret leaks, an attacker could dispatch arbitrary agents into our rooms with chosen `tenant_id` metadata. The engine would then call `build_session_config` with that tenant_id and return a SessionConfig — but only for a `session_id` that actually exists in that tenant. The attacker would need both the LiveKit secret AND a valid session_id+tenant_id pair to extract data. Mitigation if the threat ever materializes: re-introduce a signed dispatch envelope (HMAC over the metadata) as a defense-in-depth layer.

### When to reintroduce a signed dispatch envelope

- The LiveKit dispatch path becomes reachable from a less-trusted actor (e.g., a third-party can dispatch agents into our project).
- The engine moves out of the same Postgres network (e.g., enterprise client requires the engine in their VPC).
- A real-world incident demonstrates LiveKit-credential leakage as a viable attack path.

### Out of scope this round

- A LiveKit recording (Egress) pipeline. When added, it will publish
  to a private S3 bucket with versioning + MFA-delete; that boundary
  needs its own STRIDE row.
- A rejoin flow for candidates who were disconnected mid-session.
- Cross-region tenant data residency.

---

## Audio pipeline (2026-05-06)

> **Status: locked to LK Cloud (2026-05-06).** The architecture committed
> to LK Cloud as the day-1 deployment target — the prior self-hosted
> fallback was removed once the Cloud path was empirically validated
> across three smoke sessions. See the audio-pipeline spec at
> `docs/superpowers/specs/2026-05-06-audio-pipeline-design.md` for the
> full implementation detail.

### New sub-processors (as of 2026-05-06 audio-pipeline cutover)

- **LiveKit Cloud SFU** — candidate audio routes through Cloud's SFU
  (encrypted in transit, transient, no persistence at the SFU layer).
  New as of 2026-05-06 audio-pipeline cutover.

### Current audio path (self-hosted, updated 2026-06-04)

Audio data-flow: candidate mic → browser WebRTC (EC + AGC on,
`noiseSuppression` ON — browser does light NS) → LiveKit Cloud SFU →
Deepgram STT → engine LLM. No server-side NC stage.

- `noiseSuppression` is **true** on the browser side (browser-side
  light NS; no server NC, so no double-denoising concern).
- `echoCancellation` and `autoGainControl` stay true.
- Server is source of truth: frontend reads constraints from
  `audio_processing_hints` on the `/start` response.
- The engine uses **Silero VAD** (`livekit-plugins-silero`; prewarm-loaded
  in agent.py). `app/ai/realtime.py` exposes `build_interruption_options()`
  (`mode="vad"` with `min_words=2`) and `build_vad()` (returns Silero VAD).
  `build_noise_cancellation()` no longer exists (ai-coustics removed).
- Per-session tuning is snapshotted in `sessions.audio_tuning_summary`
  (migration `0028_audio_tuning_summary`) for audit and analytics.

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| Browser mic → LiveKit Cloud SFU | Audio carries whatever the browser's WebRTC EC/AGC + browser NS pass through (noiseSuppression is ON). | I (info disclosure of bystander speech) | No server-side NS; bystander mitigation rests on the quiet-environment mandate in the pre-session guidance + pre-session consent (consent text must cover third-party voices for the candidate's locale) + event-log redaction policy (`metadata` mode) for any STT transcripts of bystander speech. |
| Engine → recording (LiveKit Egress, if ever wired) | LiveKit Egress is not wired today. When wired, the recording captures browser-NS-processed audio. | I (information disclosure via recording) | Future Egress wiring will need its own threat-model row. S3 recording-bucket policy in root CLAUDE.md (versioning + MFA-delete) is already in place. |

### When this section needs updating

- A server-side NC plugin is re-introduced (changes bystander-disclosure posture).
- VAD provider changes (Silero → different provider).
- Egress (recording) wired in.
- Deployment target switches to a different SFU for a specific
  tenant (per-tenant audio path becomes heterogeneous).

## Phase ATS — 2026-05-12

A new tenant-scoped credential surface is introduced via the ATS adapter system
(`app/modules/ats/`). Per-tenant Ceipal email, password, API key, and OAuth-style
access/refresh tokens are stored encrypted at rest via Fernet (MultiFernet
keyring; key in env at MVP → AWS Secrets Manager at enterprise).

Rotation runbook: `docs/security/ats-credentials-rotation.md`.

Trust boundaries touched:
- Backend ↔ Ceipal API (HTTPS-only per Ceipal docs).
- Recruiter ↔ /api/ats/* (super_admin-gated; rate-limited per the table in this doc).

No PII enters logs (per the redactor table in the spec). Adapter `httpx` client
strips `Authorization` and request/response bodies from logs.

---

## Candidate proctoring (2026-05-21)

> **Spec reference:** `docs/superpowers/specs/2026-05-21-candidate-session-proctoring-design.md`

Proctoring introduces a new candidate-facing write endpoint and a client-side
detection layer. Both add trust-boundary surface that must be understood
accurately — the feature is a deterrent and evidence-recorder, not a
detection guarantee.

### New surface: `POST /api/candidate-session/{token}/proctoring/event`

**Auth.** The candidate JWT is carried in the URL path token segment and
verified by the existing `AuthMiddleware`. The token's `used_at` flag is
**not** checked here — the same property that `/state` and `/rejoin` rely on:
only unknown or superseded JTIs are rejected. An active-session token already
consumed by `/start` is therefore valid for proctoring calls, as intended.

**Tenant scope.** The session is loaded filtered by both `id` and `tenant_id`
from the verified token claims. A cross-tenant token produces a 404 — the
same opacity as `/state` (no existence leak).

**Data recorded.** Only the violation `kind`, `severity`, timestamps,
`session_id`, and a `correlation_id` are stored — in
`sessions.proctoring_violations` (JSONB append) and in the audit log. The
candidate JWT bearer value never appears in any stored field. No PII fields
are accepted or persisted by this endpoint.

**Rate limits.** Declared as 60 requests/min per token and 120 requests/min
per IP. Not yet enforced by middleware (consistent with other candidate-session
endpoints; enforcement lands when the rate-limit middleware ships).

**Backend-authoritative termination.** The backend — not the client — decides
when a session is terminated. On receiving a hard violation event
(`tab_switch`, `focus_loss`, `fullscreen_abandoned`, `devtools`) or when a
soft-violation threshold is exceeded (`keyboard` / `fullscreen_exit`; the
cumulative soft count must *exceed* the limit, so at the default limit of 3 the
session ends on the 4th soft violation), the backend transitions the session
`active → terminated`,
stamps `proctoring_outcome`, and issues a best-effort LiveKit room cancellation.
The candidate cannot prevent termination by suppressing subsequent calls after
the first hard-violation POST is received.

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| Candidate browser → `/proctoring/event` | Client-side listeners emit violation events; the POST payload includes `kind`, `severity`, and timestamps. | T (spoofed or suppressed events — client controls the browser) | Backend is authoritative: it evaluates and terminates. Suppressed events cannot prevent already-received hard violations from acting. False negatives (suppression) are an accepted residual (see limitations below). |
| Proctoring JSONB → recruiter review | `sessions.proctoring_violations` surfaces in the evaluation report. | I (false positive may mislead recruiter) | Every termination is recorded with its specific reason + timestamp. Recruiters are expected to treat a `devtools` flag as a signal to review, not as a conviction. Borderline-candidate invariant (human-review required) applies regardless of proctoring outcome. |
| `proctoring_outcome` → session state machine | A proctoring termination sets `state = 'terminated'` atomically in the same UPDATE that stamps `proctoring_outcome`. | T (replay / double-termination) | The UPDATE filters on `state = 'active'`; a second call after state transitions is a silent no-op, not a duplicate audit row — same atomicity pattern as `record_session_result`. |
| Devtools detection (client-side timing trap + size-delta) | `debugger` statement timing and `window.outerWidth - window.innerWidth` delta. | S (detection evasion) | Opening DevTools before navigation, disabling breakpoints in one click, or attaching a remote debugger evades both checks. Size-delta false positives (OS zoom, browser translate/password panels) are mitigated by capturing a baseline delta at arm time and preferring the `debugger` trap result where available. |

### What proctoring deters (common cases)

- Reflexive tab-switching or window-switching during the session.
- Pressing F12 or right-clicking while the session is active.
- A DevTools panel that is docked or already open at session start (caught by
  the size-delta baseline arm and the `debugger` timing trap).
- Minimising or leaving fullscreen without recovering.

### Honest limitations — what proctoring cannot stop

These must not be overstated in recruiter-facing UX copy:

1. **Tampered client.** The candidate controls their browser. They can patch
   out the event listeners, intercept and drop the `proctoringEvent` POST, or
   never invoke the client-side code at all. The system optimises for the
   un-tampered common case and records only what the backend actually receives.

2. **DevTools evasion.** Opening DevTools before navigating to the session URL,
   disabling all breakpoints (defeats the `debugger` timing trap in one click),
   or attaching a remote debugger over Chrome DevTools Protocol bypasses
   browser-side detection entirely. The `debugger` trap is a soft signal, not
   a hard guarantee.

3. **Second device / off-screen resources.** No browser API can observe a
   second monitor, a phone, or printed notes placed beside the screen. These
   are fully out of band.

4. **Size-delta false positives.** OS-level accessibility zoom, a pinned
   browser translate panel, a password-manager overlay, or a snap-docked
   window can produce a size delta that resembles a docked DevTools pane. The
   baseline-delta approach and preference for the `debugger` trap result reduce
   this; they do not eliminate it. Every `devtools` termination carries its
   triggering reason and timestamp so a recruiter can assess whether the flag
   was plausible.

### Conclusion

Candidate proctoring is a deterrent layer and an evidence-recorder. It raises
the cost of casual cheating, documents what the backend observed, and feeds
into the recruiter review workflow. It is not a guarantee of integrity.
Recruiter-facing UI copy must reflect this: proctoring flags are inputs to a
human decision, not final verdicts. The borderline-candidate invariant (no
auto-advancement or auto-rejection without human sign-off) remains in force
regardless of proctoring outcome.

---

## Session recording (LiveKit Egress → object storage) — 2026-05-28

Wires the previously-anticipated Egress recording pipeline (see the "Audio
pipeline" section's deferred Egress row). The whole interview is captured as
one MP4 — candidate camera full-frame + mixed candidate/agent audio — via a
RoomComposite egress, uploaded by LiveKit Cloud Egress directly to a private
S3-compatible bucket (Cloudflare R2 at MVP), and streamed back to the report
page via a short-lived presigned GET URL minted by Nexus.

### New external service in the data path

- **Cloudflare R2** (object storage) becomes a sub-processor holding candidate
  PII (interview video + audio). The S3-protocol abstraction (`app/storage/`)
  means R2 is swappable for AWS S3 / Supabase Storage by config; any swap
  changes the sub-processor and requires a DPA + sub-processor-list update.
- **LiveKit Cloud Egress** composites + transcodes the recording in LiveKit's
  cloud (already a sub-processor for the media plane; egress extends what it
  processes to include the candidate's video frames).

### Trust boundaries

| Boundary | Risk | STRIDE | Mitigation |
|---|---|---|---|
| Room media → LiveKit Egress | Egress captures candidate video + both audio tracks (post-denoising). | I (disclosure via recording) | Consent is timestamped before `/start`, so recording begins post-consent (AIVIA). Egress starts only for sessions that reach `active`. |
| Egress → R2 bucket | Recording written to object storage with long-lived S3 credentials. | I, T | Bucket is **private** (no public read). S3 API token is server-only (`RECORDING_STORAGE_*`), treated with DB-credential sensitivity, and scoped to the recordings bucket. Object keys are tenant-prefixed (`recordings/{tenant_id}/{session_id}.mp4`). |
| R2 → recruiter browser | A recording URL could leak candidate PII if long-lived or shared. | I | Access is **signed-URL-only**, minted per-request by Nexus under the caller's tenant scope (RLS-bound session + explicit `tenant_id` filter + `reports.view` RBAC). TTL is short (default 1h). Signed URLs are never logged. |
| Cross-tenant read | Tenant A requesting tenant B's recording. | E (elevation) | The read service filters `sessions` by `(id, tenant_id)`; a mismatch returns 0 rows → 404 before any URL is minted. Covered by `test_session_recording.py::test_cross_tenant_access_raises_not_found`. |

### Residual threats (accepted)

- A recruiter with `reports.view` can download the signed URL and redistribute
  the file out of band. This is inherent to any human-review workflow and is
  governed by the same access policy as the report itself.
- R2 has no S3 object versioning / MFA-delete (the root CLAUDE.md DR standard
  for the recording bucket). At enterprise the recording bucket moves to AWS S3
  (regaining versioning + MFA-delete); the S3-protocol code ports as config.
  Tracked as a pre-enterprise gap.

### Logging / PII discipline

- No signed URLs, object keys with tenant context, or egress internals in
  structured logs. Recording-start failures log `session_id` only (the bucket/
  endpoint could appear in raw exception text, so `exc_info` is kept out of the
  message fields).

### Availability posture

- Recording is **best-effort**: a failed egress start is caught and the
  interview proceeds (candidate-session availability target is 99.95%). The
  failure is recorded as `recording_status='failed'` and surfaced honestly on
  the report page ("recording unavailable"); transcript + scores are
  unaffected.

### When this section needs updating

- Storage provider swap (R2 → AWS S3 / Supabase Storage / other).
- Adding an inbound LiveKit Egress webhook (currently pull-based reconcile, so
  no public webhook surface exists yet).
- Any change to who can access a recording (RBAC gate beyond `reports.view`).

---

## Vision proctoring — MediaPipe WASM (2026-05-29)

The candidate session surface now compiles same-origin WASM (`/mediapipe/wasm/`) for face-landmark proctoring via MediaPipe tasks-vision; `'wasm-unsafe-eval'` has been added to the `script-src` CSP directive in `proxy.ts` (narrower than `'unsafe-eval'`; does not re-enable JS eval; assets are self-hosted under `public/mediapipe/` — no CDN). See `docs/superpowers/specs/2026-05-29-vision-proctoring-design.md §5`.

---

## Vision proctoring — server-plane analysis (2026-05-30)

> **Spec reference:** `docs/superpowers/specs/2026-05-29-vision-proctoring-design.md` §16
> **DPIA:** `docs/security/2026-05-30-vision-proctoring-dpia.md`

Post-session offline analysis: after the R2 recording reaches `recording_status='ready'`, a Dramatiq actor on the dedicated `vision` queue (`nexus-vision-worker` image) downloads the recording, samples frames via ffmpeg, runs the MobileGaze ONNX gaze estimator + uniface RetinaFace face detector, derives gaze features and a 3-tier integrity band, and persists the features-only result to `session_proctoring_analysis`. The recruiter reads it via `GET /api/reports/session/{id}/proctoring` as evidence for human review.

### Data flow

```
R2 recording
    │  private bucket; S3-protocol presigned-GET minted by nexus-vision-worker
    ▼
nexus-vision-worker (in-VPC, no new external sub-processor)
    │  ffmpeg frame sample → MobileGaze ONNX gaze + uniface RetinaFace
    │  pure-function detectors → risk band + heatmap + flagged intervals
    │  discard all frames/crops immediately after per-frame inference
    ▼
session_proctoring_analysis (Postgres, tenant-scoped)
    │  canonical tenant_isolation + service_bypass RLS pair
    │  registered in _TENANT_SCOPED_TABLES (boot assertion)
    ▼
GET /api/reports/session/{id}/proctoring
    │  reports.view RBAC gate + session load filtered by (id, tenant_id)
    ▼
ProctoringIntegrityPanel (frontend/app report page)
    │  "for review, not a decision" label — never auto-rejects
```

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| R2 → vision-worker download | Worker presigns and downloads the recording. Recording bucket credentials (`RECORDING_STORAGE_*`) are server-only secrets. | I (credential leakage → recording exfil) | Same credential-sensitivity policy as DB creds (root CLAUDE.md). Bucket is private. Object keys are tenant-prefixed. |
| Worker → Postgres (bypass-RLS) | Actor uses `get_bypass_db` (bypass-RLS session) because the worker has no Supabase user context. | E (cross-tenant read) | Every query in the actor **also** filters by the explicit `tenant_id` from the Dramatiq message (belt-and-suspenders: bypass-RLS + explicit-tenant filter, same pattern as `interview_runtime.service`). Cross-tenant query returns 0 rows before any write. |
| Postgres `session_proctoring_analysis` → recruiter | The `ProctoringAnalysis` response includes `risk_band` + flagged intervals. | I (false positive misleads recruiter) | Band is labelled "for review, not a decision" in the UI (spec §16.5). `insufficient_data` band suppresses confident flags when `unscorable_pct` is high. Human sign-off required on all hiring decisions. |
| Dramatiq `vision` queue → worker | The queue message carries `session_id` + `tenant_id`. | T (forged message triggers spurious analysis) | A forged message can only produce an analysis row for a session_id+tenant_id pair that exists (queries return 0 rows otherwise). The actor is idempotent: if a row already exists in a terminal/active state, it exits without side effects. |
| Model weights file (`resnet34_gaze.onnx`) | Loaded by the worker at startup from a mounted path. | T (supply-chain / tampered weights) | `model_versions.weights_hash` is persisted per result row for audit. The hash should be pinned in the deployment manifest and checked at worker startup. |

### Tenant isolation specifics

- The actor reads under bypass-RLS to avoid requiring a user context, but
  **every query is also filtered by the explicit `tenant_id`** passed in the
  Dramatiq message. This is the same dual-layer pattern used by
  `interview_runtime.service`.
- The result table carries the canonical `tenant_isolation` (USING + WITH
  CHECK, `NULLIF(…)::uuid`) + `service_bypass` RLS pair from migration 0051,
  and is registered in `_TENANT_SCOPED_TABLES` so the boot-time
  `_assert_rls_completeness` check covers it.
- A cross-tenant `GET /api/reports/session/{id}/proctoring` request returns
  404 (session not found) before any analysis row is read.

### No new external sub-processors

All vision computation is in-house (the `nexus-vision-worker` container in
the same infra as the Nexus API). The only external storage service in the
data path is Cloudflare R2, which is an already-registered sub-processor
(existing recording pipeline; see §Session recording above).

### Stored data classification

`session_proctoring_analysis` stores **derived features only** — no raw
frames, no face crops, no biometric templates. Fields: `risk_band`,
`detector_summary`, `gaze_heatmap`, `flagged_intervals`,
`gaze_signal_quality`, `unscorable_pct`, `model_versions`. The link to the
candidate is the `session_id` FK only.

### Residual threats (accepted)

- **NC model weights in dev/POC.** The `resnet34_gaze.onnx` weights are
  Gaze360-trained and non-commercial. An analysis result produced with these
  weights is not legally cleared for use with paying tenants. The
  `proctoring_vision_enabled` flag defaults to OFF to prevent accidental
  production use before the weights are replaced. Tracked as an open GA
  blocker in the DPIA.
- **Demographic performance gaps.** Gaze/face models have documented accuracy
  disparities across skin tones and eyewear. `insufficient_data` band +
  `gaze_signal_quality` field mitigate confident flags on degraded frames;
  human-review-only (never auto-reject) is the primary safeguard. Bias-review
  obligation documented in the DPIA.

### When this section needs updating

- Gaze-model weights replaced (new weights hash must be pinned in the
  deployment manifest and the GA-blocker item in the DPIA closed).
- Analysis extended to liveness / synthetic-feed detection (pass 2 — adds new
  processing purposes requiring DPIA revision).
- Worker moved outside the current infra boundary (e.g. tenant-VPC enterprise
  deployment), which would add a new network trust boundary.
- Any change to the `vision` queue access model (currently internal only).

---

## Self-hosted LiveKit SFU + Egress — transport + recording relocation (2026-06-09)

> **Spec reference:** `docs/superpowers/specs/2026-06-09-self-hosted-livekit-egress-design.md`

This branch moves the LiveKit SFU and Egress from LiveKit Cloud to an
operator-run deployment. It also parameterises the candidate app's CSP
`connect-src` LiveKit origin via `NEXT_PUBLIC_LIVEKIT_WS_URL`.

### Sub-processor change — net reduction in third-party exposure

**LiveKit Cloud is removed as a media/transport sub-processor** for candidate
A/V and recordings. All SFU forwarding and Egress transcoding now run inside
the operator's own infrastructure (same trust boundary as Nexus). This is a
net reduction in third-party data exposure: candidate audio, video, and the
recording upload path no longer transit LiveKit's cloud.

The recording upload path is unchanged in kind: LiveKit Egress (now
operator-hosted) writes one MP4 per session directly to the private R2 bucket
via per-request S3Upload — private bucket, tenant-prefixed keys, presigned-GET
access only (see §Session recording above). No new storage sub-processor is
introduced.

### New operator trust surface — SFU + Egress + coordination Redis

The operator now runs the SFU process, the Egress worker, and the Redis
instance they coordinate over. These components handle candidate A/V in transit.
The operator is already a trusted party (they run Nexus + the DB); this
extends that boundary to the media plane rather than adding a new external
actor. The mitigations below apply:

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| Candidate browser → self-hosted SFU | Audio/video transits via WebRTC through the operator SFU (encrypted in transit, DTLS-SRTP). | I (in-transit disclosure) | DTLS-SRTP in WebRTC is mandatory. SFU forwards encrypted media — it does not persist frames. The operator is an already-trusted infrastructure actor. |
| Egress worker → R2 recording bucket | Egress writes the composite MP4 via S3Upload credentials (`RECORDING_STORAGE_*`). | I (credential leakage → exfil) | Same credential-sensitivity policy as DB creds. Bucket is private; object keys are tenant-prefixed; presigned-GET TTL is short. Unchanged from the Cloud Egress path. |
| Coordination Redis (SFU ↔ Egress) | Room metadata and egress start/stop signals pass through Redis. | T (forged egress command) | Redis is internal-only (not externally reachable). Egress commands require the LiveKit API key/secret (same as before). No new external access surface. |

### CSP `connect-src` — LiveKit origin is now env-driven

`proxy.ts` now reads `NEXT_PUBLIC_LIVEKIT_WS_URL` to populate the `connect-src`
LiveKit origin. When unset it falls back to the prior LiveKit Cloud wildcards
(`wss://*.livekit.cloud https://*.livekit.cloud`) for back-compat.
In dev mode `ws://localhost:*` is also included (unchanged from before).
**No new third-party origin is introduced** — the change narrows an existing
allowance to a specific operator-controlled host, or retains the prior Cloud
wildcards if self-hosting is not yet configured.

Adding a genuinely new third-party origin to `connect-src` would still require
a threat-model update per the candidate-session `CLAUDE.md` security rule.

### When this section needs updating

- Self-hosted SFU is scaled horizontally or moved to a new network boundary
  (e.g. tenant-VPC enterprise deployment adds a new ingress surface).
- Coordination Redis becomes accessible from outside the infra boundary.
- The recording storage destination changes (new bucket or sub-processor).
- The `connect-src` fallback default is updated (keep in sync with
  `lib/env.ts` `NEXT_PUBLIC_LIVEKIT_WS_URL` default and `proxy.ts` comment).

---

## Candidate Reel — AI highlight video (2026-06-03)

The Candidate Reel (`app/modules/reel/`) produces a ~45–60s recruiter-facing
highlight video derived from an existing session recording + the post-session
report. It is a new **derived candidate-video artifact** and so is assessed
here per the "candidate-facing surface changes" update rule.

### No new external sub-processors

The reel data path reuses only **already-registered** services: the OpenAI
API (Director EDL selection — same provider as the rest of the platform),
Cloudflare R2 (reads the source recording, writes the reel MP4 — same bucket
family as §Session recording), and Sarvam TTS (narration — same voice as the
live interview). No new third party joins the data path. Rendering runs
in-house in the existing `nexus-vision-worker` container (ffmpeg + Pillow).

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| R2 → reel actor download | Actor presigns + downloads the source recording. | I (recording exfil) | Recording-bucket credentials are server-only secrets (same policy as DB creds). Private bucket, tenant-prefixed keys. |
| Reel actor → Postgres (bypass-RLS) | `generate_session_reel` runs with no Supabase user context. | E (cross-tenant read/write) | Every query filters by the explicit `tenant_id` from the Dramatiq message (bypass-RLS + explicit-tenant filter — same dual layer as `interview_runtime`). `session_reels` carries the canonical RLS pair (migration `0053`) + is in `_TENANT_SCOPED_TABLES`. |
| Reel MP4 → R2 → recruiter | Rendered reel stored at `reels/{tenant}/{session}.mp4`; served via presigned URL. | I (unauthorized playback) | Tenant-prefixed key; presigned, short-lived URL minted only on an RBAC-checked (`reports.view`) `GET .../reel`. Recruiter-only — the reel is never exposed to the candidate. |
| Director LLM (OpenAI) | Sees report ground-truth + word-indexed transcript. | I (PII via 3rd party) | Same consent + existing-sub-processor posture as the rest of the OpenAI path. No raw video frames are sent to the LLM — only text (transcript + scorecards); clip selection is by word index, rendered locally. |
| Eligibility gate | A reel can be requested for a session. | — (product invariant) | Eligibility requires report `status='ready'` AND verdict ∈ {advance, borderline} AND a recording exists. A **rejected** candidate's reel is disallowed by design, limiting blast radius + avoiding adverse-action artifacts. |

### Stored data classification

`session_reels` stores reel **state + metadata** only (EDL, chapters,
`r2_key`, duration, model versions, status). The rendered video itself lives
on R2 (private). The reel is a recruiter aid, not an evaluation input — it
does not feed scoring or the verdict.

### Residual threats (accepted)

- **Narrative framing risk.** The reel is "grounded-positive" — it
  foregrounds strengths. It is a presentation artifact, not a decision input,
  and is gated to non-rejected candidates; human sign-off on the underlying
  report remains the control.
- **R2 lacks versioning / MFA-delete** (same residual as §Session recording).
  The reel is fully reproducible from the recording + report, so loss is
  recoverable by regeneration.

### When this section needs updating

- The reel is ever surfaced to candidates (new external-facing surface).
- A new external service joins the render/selection path (e.g. a hosted video
  pipeline or a different TTS/LLM vendor).
- Reel storage moves to a new bucket / sub-processor, or eligibility is
  widened to rejected candidates (re-examine adverse-action exposure).
