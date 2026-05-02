# Nexus — Threat Model

(STRIDE per trust boundary. Extended on every change to auth surfaces,
external services in the data path, or tenant-isolation boundaries.)

## Engine: in-session safety reporting (Phase 2 — added 2026-05-03)

The Phase 2 InterviewController exposes three controller-level
@function_tools that the LLM can invoke during a live interview:

  * end_interview_early(reason="candidate_request") — terminates the call
  * flag_safety_concern(category, note) — records a safety event without ending
  * report_technical_issue(description) — records a tech complaint without ending

### Information assets and trust boundaries

  * `note` (flag_safety_concern) and `description` (report_technical_issue)
    are LLM-authored summaries of candidate speech. They may contain quoted
    snippets of what the candidate said.
  * Stored in the per-session JSON envelope written by the EventCollector +
    sink. Default redaction mode is `metadata`, which strips the content
    field but keeps the category / character count metadata. `full` mode
    is consent-gated audit replay only.
  * Replay log retention follows the existing recording bucket policy:
    versioning ON, MFA-delete ON, S3 server-side encryption.

### STRIDE per relevant boundary

  * **Spoofing** — N/A. Tools are LLM-callable only; no external trigger.
  * **Tampering** — Audit envelope is append-only; sink writes are
    one-shot at session close. No mid-session mutation surface.
  * **Repudiation** — Each event carries `t_ms`, `wall_ms`, `correlation_id`.
    The session_id + tenant_id + JTI prefix are sufficient to reconstruct
    chain of custody.
  * **Information disclosure** — `note` / `description` content gated by
    redaction mode. Default production setting is `metadata`. `full` mode
    requires per-tenant consent + use-case documentation under
    `docs/security/audit_replay/`.
  * **DoS** — A flood of `flag_safety_concern` calls would inflate the
    envelope but cannot prevent session completion. The 1Hz idle-nudge
    tick is the only continuous resource consumer; it is bounded to ~15
    iterations per minute per session.
  * **Elevation of privilege** — N/A. No tool grants persistence or DB
    access beyond what's already in the engine's bypass-RLS session.

### Escalation procedure

  * `category="threats_to_self"` — recruiter on the assigned hiring team
    is notified within 1 hour of session close. SEV2 if unactioned within
    24 hours.
  * `category="threats_to_others"` — same 1-hour SLA; security ops
    notified in parallel.
  * `category="harassment"` / `category="inappropriate_request"` — recruiter
    notified at next business day; no SEV.
  * `category="other"` — appears on the recruiter's session report; no
    proactive notification.

### Pending follow-ups

  * Recruiter dashboard surface for safety events — separate post-arc ticket.
  * Per-tenant escalation routing config — Phase 5+.
