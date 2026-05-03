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

### Original Phase 3C.2 notes (superseded)

The original Phase 3C.2 trust boundaries described a `verify_engine_token` path in `app/modules/interview_runtime/` enforcing HS256 algorithm pinning, a `purpose='engine_dispatch'` claim, and tenant-id consistency. All of that — `verify_engine_token`, the `engine_dispatch_tokens` table, the `engine_token_uses` table, and the `/api/internal/*` HTTP endpoints — was retired in Phase 3 of the modular-monolith spec (see `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` and `docs/superpowers/plans/2026-05-01-phase-3-engine-merge.md`). The original notes are preserved in git history for reference.

### Out of scope this round

- A LiveKit recording (Egress) pipeline. When added, it will publish
  to a private S3 bucket with versioning + MFA-delete; that boundary
  needs its own STRIDE row.
- A rejoin flow for candidates who were disconnected mid-session.
- Cross-region tenant data residency.

---

## Engine: in-session safety reporting (Phase 2 — controller cutover; 2026-05-03)

Closes Phase 2 acceptance gate §9.5. Covers the trust surface added
when `InterviewController` exposes meta tools the LLM can call
in-session (`flag_safety_concern`, `report_technical_issue`,
`disqualify_knockout`). These are not new external services — they
are new in-process capabilities the LLM can invoke through
`@function_tool` calls. The audit + escalation surface they create
is the new threat-model concern.

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| LLM tool-call → audit log | Every meta tool fires a structured audit event into the event-log envelope (`controller.intent.flag_safety_concern`, `controller.intent.report_technical_issue`, `disqualify.knockout`). | I (information disclosure), R (repudiation) | Tool-call retention follows the event-log envelope: same path, same redaction, same TTL. The `category` enum on `flag_safety_concern` is bounded; the `note` field is free-form but redacted to "(content redacted)" in `metadata` mode (production default). `full` mode preserves the verbatim note for audit replay only. |
| Reviewer → metadata-mode envelope | Default reviewer access is `metadata` mode: tool name + category enum + redaction marker, no note bodies, no transcript content. | I | Standard structlog redactor + envelope-level redaction. Recruiter-dashboard access is the only routine path. No additional gating beyond existing recruiter RBAC. |
| Reviewer → full-mode envelope (audit replay) | `full` mode preserves verbatim note + transcript content. Required for genuine investigation of a flagged session. | I (information disclosure of candidate PII / safety-sensitive disclosures) | Privileged audit-replay path. Requires (a) candidate consent on the original session (already captured pre-recording per AIVIA), (b) documented use case under `docs/security/`, (c) Super Admin role assertion at access time. The path is not exposed in the recruiter dashboard today; access is operator-only via direct envelope file read. |

### Escalation procedure for `flag_safety_concern`

The LLM may call `flag_safety_concern(category, note)` mid-session
when a candidate disclosure raises a safety concern. Categories are
a bounded enum; `threats_to_self` is the highest-severity case.

| Category | Severity | Notification | SLA |
|---|---|---|---|
| `threats_to_self` | SEV2 | Recruiter notified within **1 hour** (in-app notification + email via the existing notifications module). Super Admin notified at the same time. SEV2 incident opened if not actioned within **24 hours**. | 1h notify / 24h actioned |
| `disclosure_inappropriate` | SEV3 | Recruiter notified within 4 hours. No SEV escalation unless repeated across sessions for the same candidate. | 4h notify |
| Other categories | SEV3 | Routine recruiter dashboard surface; no time-bound SLA. | n/a |

The 1h notification for `threats_to_self` is the binding SLA — an
operational alert if the notifications path is degraded would itself
be a SEV2 incident (notify-the-notifier failure).

### When this section needs updating

- A new meta tool is added to the controller (e.g.,
  `request_break`, `report_consent_withdrawal`).
- A new category is added to the `flag_safety_concern` enum.
- A new redaction mode is added (e.g., `aggregate` for cross-session
  analytics).
- The recruiter dashboard exposes the `full` mode replay path (today
  it is operator-only; surfacing it to recruiters changes the
  consent-gate posture).
- A real-world incident demonstrates a new attack path on the meta
  tools (e.g., LLM emitting a flag with a free-form note that
  contains untrusted user input from an attacker probing the system).

---

## Phase 6 — Server-authoritative audio (2026-05-03)

Trust boundaries that change when browser-side EC/NS/AGC switch from
ON to OFF and ai_coustics becomes the sole noise filter for candidate
audio. Configuration tuning: `INTERVIEW_NOISE_CANCELLATION_MODEL=QUAIL_S`,
`INTERVIEW_NOISE_CANCELLATION_LEVEL=0.4`. Affected surfaces:

- `frontend/session/app/interview/[token]/CameraMicStep.tsx` —
  `getUserMedia` constraint object disables EC/NS/AGC.
- `frontend/session/components/interview/app/app.tsx` —
  pre-constructed LiveKit `Room` carries matching `audioCaptureDefaults`.
- `backend/nexus/app/config.py` + `.env.example` — engine ai_coustics
  defaults.

### Trust boundaries

| Boundary | Element | STRIDE | Mitigation |
|---|---|---|---|
| Browser mic → LiveKit room | Raw audio (no browser-side EC/NS/AGC) carries any sound the mic captures, including ambient conversations near the candidate (open-plan offices, family members in the next room). | I (info disclosure of bystander PII) | Pre-session consent text already states audio is recorded; reviewers SHOULD verify the consent copy reasonably covers third-party voices for the candidate's locale. ai_coustics QUAIL_S at level 0.4 suppresses non-target voices but is gentler than QUAIL_VF_L; the e2e checklist's noisy-environment scenario (9b) verifies the suppression holds in practice. STT transcripts of bystander speech, if produced, fall under the existing event-log redaction policy (`metadata` mode strips transcript content). |
| ai_coustics plugin → audio path | Single source of truth for noise reduction. **No application-level runtime fallback exists.** Boot-time misconfigured model name → `ValueError` at `app/ai/realtime.py::build_noise_cancellation` → worker exits → container restarts → LiveKit `AGENT_DISPATCH_FAILED`. Mid-session plugin failure → undefined application behavior (depends on plugin internals). | A (availability dependency) | Mitigation is LiveKit Cloud-managed plugin reliability. Documented as a known gap; future phase can wrap the audio input pipeline with a fallback if a real-world failure pattern emerges. The audit envelope's `model_versions` dict (`agent.py:191-202`) captures the model+level on every session, providing forensic trace if a session reports degraded quality. |
| Engine → recording (LiveKit Cloud Insights, if enabled) | Recording captures **post-ai_coustics audio** per `https://docs.livekit.io/deploy/observability/insights/` ("If noise cancellation is enabled, user audio recording is collected after noise cancellation is applied. The recording reflects what the STT or realtime model receives."). | I (information disclosure via recording) | Insights recording is OFF by default; enabling it is a deliberate operator choice already covered by existing consent gating + S3 recording-bucket policy in root CLAUDE.md ("S3: versioning ON for the recording bucket. MFA-delete ON for the recording bucket."). Future LiveKit Egress wiring will need its own threat-model row when added. |

### Browser-divergence decision (residual risk accepted)

Browsers (notably mobile Safari on iOS, sometimes mobile Chrome on
Android) silently ignore `MediaTrackConstraints` flags such as
`echoCancellation: false`. After `getUserMedia` resolves, `CameraMicStep`
reads `track.getSettings()` and emits `cammic.constraints.diverged` if
any flag was silently re-enabled. **The session continues regardless.**
Refusing to start would be worse UX than partial mitigation via
ai_coustics. Residual risk: a candidate on an ignoring-browser has
stacked browser-NC + ai_coustics-NC, which may over-suppress soft
speech. Operators monitor the divergence log; if a high false-rate is
observed, a future phase can add per-browser handling.

### Operational performance note

Raw audio uplink may be marginally larger (more entropy in the Opus
payload); browser CPU may be marginally lower (no DSP). Net effect on
low-bandwidth or low-CPU candidate devices is expected to be invisible;
measurement is deferred to a future analytics phase.

### When this section needs updating

- LiveKit Egress is wired into the engine (would require its own
  capture-point row — see `docs/superpowers/specs/2026-05-02-…` Phase 3C.2
  "out of scope" notes, which are still authoritative for Egress).
- The ai_coustics model is changed in production (the threat surface
  changes per-model; e.g., a switch to `QUAIL_BV` would change the
  broadband-voice-suppression characteristic).
- A real-world incident demonstrates a mid-session plugin failure path
  needing application-level handling.
- The candidate surface adopts a structured logger that ships
  `cammic.constraints.diverged` to a third-party sink (would change the
  PII posture of the divergence record).
