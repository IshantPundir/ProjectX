# Key Rotation Runbook

Cadence: every 90 days, on personnel change, or on incident — whichever
comes first. The rotation runbook is the gate, not the rotation itself:
a key cannot be rotated until its runbook is current. (Per the root
`CLAUDE.md` enterprise operating standards.)

When you rotate a key, append a brief log entry below with date + who
performed it + reason. Do not record the key value itself anywhere in
this file.

---

## INTERVIEW_ENGINE_JWT_SECRET (Phase 3C.2)

Used by Nexus to sign HS256 engine dispatch JWTs. The interview engine
verifies them using the same secret. The signed JWT is what authorizes
the engine's calls to `/api/internal/sessions/{id}/{config,results}`.

**Compromise impact.** An attacker who also has access to LiveKit (so
they can land an agent worker in a candidate room) could mint
dispatches that retrieve full `SessionConfig`s — which include the
company profile and the question rubric. Result post is single-use per
`(jti, endpoint)` so a stolen token cannot post a fabricated result
once the legitimate engine has already posted, but it could overwrite
a session that has not yet completed if it acts first.

**Procedure.**

1. Generate a fresh secret: `openssl rand -base64 32`.
2. Update the Railway / ECS env: `INTERVIEW_ENGINE_JWT_SECRET=<new>`
   on **both** the `nexus` and `interview-engine` containers in
   lockstep — they verify against the same secret.
3. Roll out both containers together. No in-flight dispatches will
   succeed across the cutover; this is acceptable since sessions
   already in the live state hold a fully-authenticated room and do
   not need to re-dispatch.
4. Audit `engine_dispatch_tokens` for the prior 24h:
   ```sql
   SELECT count(*) FROM engine_dispatch_tokens
   WHERE issued_at > NOW() - INTERVAL '24h';
   ```
   Confirm the count is plausible vs. the dispatch volume from the
   logs.

---

## LIVEKIT_API_KEY / LIVEKIT_API_SECRET

Provisioned by LiveKit Cloud (or your self-hosted control plane).
Used by Nexus to mint candidate access tokens at `/start` and by the
engine to authenticate as the agent worker.

**Compromise impact.** An attacker can mint candidate or agent room
tokens for any room on the project, publish/subscribe to any room,
and exfiltrate live audio + transcript. Treat as severity-equivalent
to a DB credential.

**Procedure.**

1. Generate a new key pair in the LiveKit Cloud dashboard (or via
   `livekit-cli` for self-hosted).
2. Update `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` env on `nexus` and
   `interview-engine`.
3. Revoke the old key pair from the LiveKit project after both
   containers have rolled and you have verified a fresh dispatch
   succeeds end-to-end.

---

## CANDIDATE_JWT_SECRET (Phase 3C.1)

Used by Nexus to sign HS256 candidate session tokens (the JWT in
`/interview/{token}` URLs). Rotated separately from the engine secret.

**Compromise impact.** An attacker can mint candidate JWTs for any
session and bypass the OTP gate (since the JWT validates ahead of the
OTP check). The supersession chain on `candidate_session_tokens`
limits replay against the *same* session_id but does not stop a
forged-from-scratch token.

**Procedure.**

1. Generate a fresh secret: `openssl rand -base64 32`.
2. Update `CANDIDATE_JWT_SECRET` on `nexus` only (the engine does not
   verify candidate tokens).
3. Roll the `nexus` container.
4. Existing candidate invite links signed with the old secret will
   stop validating. If invites have been sent within the prior 72
   hours (the candidate JWT TTL), notify recruiters to resend
   affected invites.

---

## OTP HMAC key

Same lifecycle as the candidate JWT secret — rotate together if
either is compromised.

---

## Rotation log

| Date | Key | Operator | Reason |
|---|---|---|---|
| _(none yet)_ | | | |
