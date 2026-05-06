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
- **ai-coustics** — ML noise-cancellation provider running inside
  the LK Cloud audio path. Server-side QUAIL_L model processes
  candidate audio before it reaches Deepgram STT.

### Current audio path (LK Cloud mode)

Audio data-flow: candidate mic → browser WebRTC (EC + AGC on,
`noiseSuppression` OFF) → LiveKit Cloud SFU → ai-coustics QUAIL_L
(server-side denoising, enhancement_level=0.5) → Deepgram STT →
engine LLM.

- `noiseSuppression` is **false** on the browser side when server NC
  is on — avoids double-denoising the ML model's input.
- `echoCancellation` and `autoGainControl` stay true in both modes
  (Cloud and self-hosted).
- Server is source of truth: frontend reads constraints from
  `audio_processing_hints` on the `/start` response.
- The engine installs `livekit-plugins-ai-coustics` only (Krisp +
  Silero plugins were removed in Patch 5 once the architecture
  locked to ai-coustics). `app/ai/realtime.py` exposes
  `build_noise_cancellation()` (ai_coustics QUAIL_L or QUAIL_VF,
  env-selected), `build_interruption_options()` (locked to
  `mode="adaptive"` with `min_words=2`), and `build_vad()`
  (always returns `ai_coustics.VAD()` — uses ai-coustics' built-in
  VAD adapter, which reads VAD signals from the same inference that
  runs for NC).
- Per-session tuning is snapshotted in `sessions.audio_tuning_summary`
  (migration `0028_audio_tuning_summary`) for audit and analytics.

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| Browser mic → LiveKit Cloud SFU | Audio carries whatever the browser's WebRTC EC/AGC pass through (noiseSuppression is OFF in Cloud mode). | I (info disclosure of bystander speech) | ai-coustics QUAIL_L provides server-side NS before STT. Pre-session consent covers audio recording; consent text must cover third-party voices for the candidate's locale. STT transcripts of bystander speech fall under the existing event-log redaction policy (`metadata` mode). |
| LiveKit Cloud SFU → ai-coustics | Candidate audio transits ai-coustics infrastructure before STT. | I (PII via 3rd party) | ai-coustics is a sub-processor (add to DPA); audio is transient (not stored by the provider). Consent gate already in place. |
| Engine → recording (LiveKit Egress, if ever wired) | LiveKit Egress is not wired today. When wired, the recording captures post-denoising audio. | I (information disclosure via recording) | Future Egress wiring will need its own threat-model row. S3 recording-bucket policy in root CLAUDE.md (versioning + MFA-delete) is already in place. |

### When this section needs updating

- NC model changes (QUAIL_L → QUAIL_VF_L or different provider).
- Enhancement level tuned above 0.8 (voice-isolation range; changes
  consent-gate posture for bystander speech).
- Egress (recording) wired in.
- Deployment target switches from Cloud to self-hosted for a specific
  tenant (per-tenant audio path becomes heterogeneous).
