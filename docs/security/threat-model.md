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
