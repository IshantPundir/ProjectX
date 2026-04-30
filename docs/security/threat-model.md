# ProjectX Threat Model

This document tracks the STRIDE-per-trust-boundary view of the system.
It is updated whenever a new external service joins the data path, a
new auth surface is added, tenant-isolation boundaries change, or the
candidate-facing surface changes (per the root `CLAUDE.md` enterprise
operating standards).

PRs touching auth, RLS, or session paths must reference the relevant
section below.

---

## Phase 3C.2 — Interview engine integration (2026-04-29)

Trust boundaries added by wiring `backend/interview_engine/` into Nexus.

| Boundary | New element | STRIDE | Mitigation |
|---|---|---|---|
| Public → LiveKit room | Candidate access token (`room_join`) | T (token leakage) | TTL = `stage_duration + 10min`; minted at `/start`, single-issuance; no rejoin flow this round. |
| LiveKit → engine worker | Dispatch JWT in metadata | T (replay across rooms) | Single-use per `(jti, endpoint)` via `engine_token_uses`; `jti` bound to `session_id`; max lifetime 90 min. |
| Engine → Nexus internal API | HTTP w/ engine JWT | E (privilege escalation), I (information disclosure) | HS256 pinning; `purpose` claim; `tenant_id` claim explicit-filter on every query under bypass session; audit log on every call. |
| Engine → OpenAI / Deepgram / Cartesia | Audio + transcript | I (PII via 3rd party) | Consent gate (existing); `CandidateContext` drops email; self-hosted Langfuse only. |

### Notes on each boundary

**Public → LiveKit room.** The candidate's LiveKit access token is the
sole credential they hold for the room. It is minted once at `/start`
with `room_join` and a TTL of `stage_duration + 10min`. There is no
rejoin flow — a refresh after start sees the existing
`AlreadyStartedPanel` fallback in the wizard, not a new token. This
trades user recovery for a tight blast radius on token theft.

**LiveKit → engine worker.** When Nexus dispatches the agent into the
candidate's room it embeds a single-use HS256 JWT in the dispatch
metadata. The engine reads the `jti` from the JWT, exchanges it for a
`SessionConfig` via the internal API, and the API atomically marks
`(jti, endpoint='config')` as consumed in `engine_token_uses` —
preventing replay against the same endpoint. A second exchange
against `(jti, endpoint='results')` is permitted on result post.

**Engine → Nexus internal API.** The engine never holds Supabase auth
context; it authenticates via the same engine JWT used at dispatch.
The `verify_engine_token` path in `app/modules/interview_runtime/`
enforces algorithm pinning (HS256-only), the `purpose` claim
(`engine_dispatch`), and `tenant_id` consistency between the JWT and
the session row. The internal API runs under a bypass-RLS session but
every query is filtered by the tenant claim on the JWT, so a forged
token bound to tenant A cannot read session data for tenant B.

**Engine → external AI providers.** Audio + transcript flow through
Deepgram (STT), OpenAI (LLM), and Cartesia (TTS) during the live
session. Candidate consent is captured via the existing AIVIA-aligned
consent flow before the session enters the active state. The
`CandidateContext` Pydantic model deliberately omits email and any
non-essential PII so the engine has no path by which to forward those
to providers. LLM tracing routes to a self-hosted Langfuse instance —
managed Langfuse cloud is forbidden by the root `CLAUDE.md`.

### Out of scope this round

- A LiveKit recording (Egress) pipeline. When added, it will publish
  to a private S3 bucket with versioning + MFA-delete; that boundary
  needs its own STRIDE row.
- A rejoin flow for candidates who were disconnected mid-session.
- Cross-region tenant data residency.
