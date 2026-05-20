# Candidate Session Proctoring — Fullscreen Lock, Focus/Tab Detection, DevTools Enforcement

**Date:** 2026-05-21
**Status:** Design — pending implementation plan
**Owner:** ProjectX team
**Surfaces:** `frontend/session` (candidate interview surface, port 3002), `backend/nexus` (FastAPI: `session` + `tenant_settings` modules, one Alembic migration)
**Related:**
- `2026-05-20-candidate-session-redesign-design.md` (the live surface this instruments)
- `2026-05-16-engine-failure-handling-design.md` (the `state='error'` durable-trace pattern this mirrors for `state='terminated'`)
- `2026-05-17-interview-engine-v2-design.md` (the agent that runs in the room we cancel on termination)

---

## Problem

The candidate interview surface (`frontend/session`) currently has **no proctoring**. A candidate in a live AI interview can switch tabs, leave the window, open browser developer tools to inspect the page or the LiveKit traffic, and use the keyboard freely — none of it is detected, prevented, or recorded. For an enterprise screening product whose entire value is a *trustworthy* signal about a candidate, that is a credibility gap.

A second, structural gap surfaced during analysis: **today the candidate frontend can only disconnect from the LiveKit room — there is no backend path to end a session *because of a violation*, nor to record *why* it ended.** A client-side `room.disconnect()` is indistinguishable from a normal "candidate ended the interview" on the backend (`session_outcome` resolves to `candidate_ended` / a CLIENT_INITIATED disconnect → the frontend shows the *success* completion screen). So even if the browser detected cheating, the recruiter would never learn it happened.

## Goal

Add a proctoring layer to the live interview that:

1. **Locks the candidate into fullscreen** for the duration of the live session.
2. **Detects** fullscreen exit, tab switches, window focus loss, keyboard use, and developer-tools opening.
3. **Warns** on low-severity events with a visible red/amber border and a toast, escalating to termination after a configurable threshold.
4. **Ends the session** immediately on high-severity events.
5. **Records every violation on the backend** (audit trail + a structured list on the session row) and ends the session with a **distinct, recruiter-visible outcome** (`state='terminated'` + a `proctoring_outcome` reason) — never masquerading as a normal completion.
6. Is **per-tenant configurable** (enable flag + thresholds) via `tenant_settings`, delivered to the frontend in the `/start` (and `/rejoin`) response.
7. **Discloses** the monitoring to the candidate before the session begins.

### Non-goals

- **Bulletproof anti-cheat.** Browser-side detection is a deterrent that raises the bar, not a guarantee (see Threat Model). A determined candidate with a tampered client, a second device, or remote debugging can still evade it. We optimise for the casual-to-moderate case and record everything we *can* observe.
- **Recruiter-side configuration UI.** Thresholds live in `tenant_settings` columns (DB-editable); no `frontend/app` dashboard UI in this arc. (Mirrors the existing `engine_knockout_policy` precedent — column exists, no UI yet.)
- **Per-job / per-stage proctoring config.** Tenant-level only for this MVP. A clean upgrade path is noted.
- **Mobile proctoring parity.** The Fullscreen API and these heuristics are desktop-browser oriented. On mobile (where the Fullscreen API is partially unsupported), proctoring **degrades gracefully**: detection that the platform supports still runs, but unsupported guards are skipped rather than falsely terminating. See "Mobile & capability degradation."

---

## Decisions (validated with the user)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | How much does the backend record? | **Full backend recording** — new endpoint, structured violation list, distinct terminal state, audit log | EEOC/audit posture + recruiters must know a candidate was flagged. A client-only disconnect carries no signal. |
| 2 | Do soft violations escalate? | **Escalate after a threshold** | Warnings need teeth, but a single accidental slip shouldn't fail a real candidate. |
| 3 | Fullscreen exit handling | **Grace overlay → end** — cover + "Return to fullscreen" + countdown; return in time = recorded soft violation, countdown expiry = hard end | Browsers can't silently re-enter fullscreen (re-entry needs a user gesture) and Escape always drops it, so an instant end would mis-fire. |
| 4 | Tab switch & window focus loss | **HARD violations → immediate end** (user overrode the initial "soft" classification) | Treated as deliberate context-switching during a monitored exam. |
| 5 | DevTools | **Layered detection** (shortcut block + right-click disable + viewport size-delta + `debugger` timing trap) → **immediate end** | "DevTools open = high-risk candidate." The `debugger` trap's main-thread hitch only occurs when devtools is open — i.e. at the instant we terminate anyway — so its cost is moot here. |
| 6 | Disclosure | **Informational notice on the pre-join screen**, no separate checkbox | Candidate is informed (fairness/AIVIA posture) without adding a consent gate. |
| 7 | Configurability | **Tenant-level toggle + thresholds** in `tenant_settings`, read at `/start` | Per-tenant control now; recruiter UI later. |

### Severity taxonomy (backend-authoritative)

| Violation kind | Severity | Trigger | Effect |
|---|---|---|---|
| `tab_switch` | **hard** | `document.visibilitychange` → `hidden` | Immediate end |
| `focus_loss` | **hard** | `window` `blur` (deduped against `tab_switch`) | Immediate end |
| `fullscreen_abandoned` | **hard** | grace countdown expired without re-entering fullscreen | Immediate end |
| `devtools` | **hard** | size-delta heuristic OR `debugger` trap fires | Immediate end |
| `fullscreen_exit` | soft | fullscreen dropped but re-entered within grace | Warn + count |
| `keyboard` | soft | meaningful keypress (debounced per burst); includes blocked devtools-open shortcuts | Warn + count |

**Soft escalation rule:** the backend terminates with `proctoring_outcome = 'soft_threshold_exceeded'` when the cumulative count of **soft** violations on the session exceeds `proctoring_soft_violation_limit` (default 3 → the 1st–3rd warn, the 4th ends). Hard violations bypass the counter entirely.

---

## Architecture overview

```
┌─────────────────────── frontend/session (live session only) ───────────────────────┐
│  <ProctoringGuard config={proctoring} onTerminated={...}>   ← wraps <LiveInterview> │
│    useProctoringController  ── owns soft-count, posts events, drives overlays       │
│      ├─ useFullscreenGuard   (request FS on arm; fullscreenchange; grace overlay)   │
│      ├─ useVisibilityGuard   (visibilitychange → hidden  → hard tab_switch)         │
│      ├─ useFocusGuard         (window blur → hard focus_loss, deduped)              │
│      ├─ useKeyboardGuard      (keydown → soft keyboard; block devtools combos)      │
│      └─ useDevtoolsGuard      (size-delta + debugger trap → hard devtools)          │
│    presentational:  <ViolationBorder>   <FullscreenGraceOverlay>                    │
│  candidateSessionApi.proctoringEvent(token, {kind, occurred_at})                    │
└───────────────────────────────────────────┬─────────────────────────────────────────┘
                                             │ POST /api/candidate-session/{token}/proctoring/event
                                             ▼
┌──────────────────────────── backend/nexus (session module) ─────────────────────────┐
│  router: post_proctoring_event_endpoint  (candidate JWT, tenant-scoped, get_tenant_db)│
│  service: record_proctoring_event(...)                                               │
│     1. load active session (id + tenant_id)                                          │
│     2. append {kind, occurred_at, severity} → sessions.proctoring_violations         │
│     3. terminal? hard kind  OR  soft_count > limit                                   │
│     4. if terminal: set proctoring_outcome, transition active→terminated,            │
│        best-effort cancel_room(), audit 'session.proctoring_terminated'              │
│        else: audit 'session.proctoring_violation'                                    │
│     5. return { terminated, violation_count, soft_violation_count }                  │
│  config delivered earlier:  start_session()/rejoin_session() embed `proctoring` obj  │
│     read from get_tenant_settings(...)                                               │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Backend is **authoritative** on the threshold and termination (the candidate's browser is the adversarial environment). The frontend reports each observed violation and obeys `{ terminated: true }`; for **hard** violations it *also* tears down locally regardless of the response (fail-safe — see "Fail-safe").

---

## Backend design

### Migration `0042_session_proctoring` (single migration, two tables + one CHECK)

**`sessions`** — three new columns:

| Column | Type | Notes |
|---|---|---|
| `proctoring_violations` | `JSONB NOT NULL DEFAULT '[]'` | Append-only list of `{kind, severity, occurred_at}` objects. |
| `proctoring_outcome` | `TEXT NULL` | The terminating reason (`tab_switch` / `focus_loss` / `fullscreen_abandoned` / `devtools` / `soft_threshold_exceeded`). Null unless proctoring ended the session. |
| `proctoring_violation_count` | `INTEGER NOT NULL DEFAULT 0` | Denormalised total; cheap recruiter-list rendering without parsing JSONB. |

**`sessions.state` CHECK** — alter `sessions_state_check` (defined in migration 0014) to add `'terminated'`:
`CHECK (state IN ('created','pre_check','consented','active','completed','cancelled','error','terminated'))`.
Rollback restores the prior 7-value constraint (the down migration must first map any `terminated` rows → `cancelled` so the tighter CHECK re-applies).

**`tenant_settings`** — three new columns (lazy-default pattern, mirroring `engine_knockout_policy`):

| Column | Type | Default | Notes |
|---|---|---|---|
| `proctoring_enabled` | `BOOLEAN NOT NULL` | `true` | Enterprise default ON, consistent with the product's bot-screening purpose. Tenants opt out by flipping the column. |
| `proctoring_soft_violation_limit` | `INTEGER NOT NULL` | `3` | Warnings allowed before a soft escalation ends the session. |
| `proctoring_fullscreen_grace_seconds` | `INTEGER NOT NULL` | `10` | Countdown length on the fullscreen grace overlay. |

No new tables → **no new RLS policy pair** and **no change to `_assert_rls_completeness`'s table list**. Both `sessions` and `tenant_settings` already carry the canonical `tenant_isolation` + `service_bypass` pair; new columns inherit it. The CHECK alter and column adds are reversible; a `down_revision` script is included per the migration-rollback rule.

### State machine change (`session/state_machine.py`)

Add `TERMINATED` to `SessionState` (schemas) and to the legal-transition graph:

```
active → completed, error, terminated     # + terminated
terminated → (terminal)
```

`cancelled` stays reserved for the pre-start scheduler-revoke path; `terminated` is exclusively "ended mid-session by policy enforcement." This is a **session-state-machine change → Human Review Required** per root CLAUDE.md (the user is the reviewer).

### `tenant_settings` extension

- `TenantSettings` (Pydantic) gains `proctoring_enabled: bool = True`, `proctoring_soft_violation_limit: int = 3` (`ge=1, le=20`), `proctoring_fullscreen_grace_seconds: int = 10` (`ge=3, le=60`). Validators reject out-of-range values; defaults mirror the migration's server defaults exactly.
- `TenantSettingsModel` gains the three columns with matching server defaults + a CHECK mirroring the bounds (consistent with the module's "mirror DB CHECK in ORM" convention).
- `get_tenant_settings` already returns schema defaults when no row exists — the new fields ride that path for free.

### `/start` & `/rejoin` response — new `proctoring` block

`StartSessionResponse` gains a nested object so the frontend can arm + configure the guard, and skip proctoring entirely when disabled:

```python
class ProctoringConfig(BaseModel):
    enabled: bool
    soft_violation_limit: int
    fullscreen_grace_seconds: int

class StartSessionResponse(BaseModel):
    livekit_url: str
    livekit_token: str
    room_name: str
    session_id: UUID
    audio_processing_hints: AudioProcessingHints
    proctoring: ProctoringConfig          # NEW
```

`start_session()` and `rejoin_session()` both call `get_tenant_settings(db, tenant_id)` and populate the block. Rejoin must carry it too, so a reconnecting candidate stays proctored.

### `PreCheckResponse` — disclosure flag

Add `proctoring_enabled: bool` so the pre-join screen renders the monitoring notice only when proctoring is on. (The notice *copy* is static frontend text; the backend only signals whether to show it.)

### New endpoint

```
POST /api/candidate-session/{token}/proctoring/event
```

- **Auth:** candidate JWT in path, verified by `AuthMiddleware` (already-`used_at` tokens still authenticate — only unknown/superseded JTIs are rejected; this is the same property `/rejoin` and `/state` rely on). Tenant-scoped via `get_tenant_db`, session loaded filtered by `id + tenant_id` (mirrors `/state`'s cross-tenant opacity).
- **Body:** `{ "kind": "<violation_kind>", "occurred_at": "<ISO-8601>" }` (`model_config = ConfigDict(extra="forbid")`; `kind` validated against the enum; `occurred_at` clamped server-side — client clocks are untrusted, used only for ordering display).
- **Service:** `record_proctoring_event(db, session_id, tenant_id, kind, occurred_at, correlation_id)`:
  1. Load the session for `id + tenant_id`. If not `active` → return `{ terminated: true, already_terminal: true }` (idempotent: a violation arriving after the session already ended is a no-op success, not an error — avoids racing the engine's natural close).
  2. Compute `severity` from the kind (server-side map — not trusted from the client).
  3. Append `{kind, severity, occurred_at}` to `proctoring_violations`; bump `proctoring_violation_count`.
  4. Terminal if `severity == 'hard'` OR (`severity == 'soft'` AND soft-count `>` `proctoring_soft_violation_limit`).
  5. **If terminal:** set `proctoring_outcome` (= `kind` for hard, `'soft_threshold_exceeded'` for soft escalation); `transition(active → terminated)`; best-effort `await cancel_room(livekit_room_name)` (wrapped in `contextlib.suppress`, same as the token-race path); write audit `session.proctoring_terminated` with `actor=candidate`, `proctoring_outcome`, `violation_count`. **Else:** write audit `session.proctoring_violation`.
  6. Return `{ terminated, violation_count, soft_violation_count }`.
- **Errors:** 404 (unknown/cross-tenant session, same opacity as `/state`); validation 422 on bad `kind`. No 409 — step 1 makes post-termination calls idempotent.
- **Rate limit (declared, per the enterprise rule):** `60/min per token`, `120/min per IP`. *Not yet enforced* — consistent with `/rejoin`'s documented-but-unenforced limits until the rate-limit middleware lands. Documented in the router docstring.
- **PII discipline:** the violation list and audit rows carry **no PII** — only `kind`, `severity`, timestamps, `session_id`, `jti_prefix`. The candidate JWT never appears in any field. (Root CLAUDE.md logging rules.)

### Termination mechanics & transcript preservation

When a hard violation (or soft escalation) terminates:

1. `record_proctoring_event` flips `state` `active → terminated` and sets `proctoring_outcome` **immediately** — these are durable the instant the endpoint returns, independent of LiveKit.
2. It best-effort `cancel_room`s. The engine's LiveKit `CloseEvent` then fires `_handle_close`, which (a) **writes the audit envelope to S3/local** — so the full forensic transcript up to the termination point survives there — and (b) calls `record_session_result(...)`, which is **gated on `state='active''`** and therefore **no-ops** (the session is already `terminated`).

**Tradeoff (explicit):** the `sessions.transcript` / `questions_asked` / `probes_fired` columns will be **empty** on a proctoring-terminated row, because the engine's column-writing path is gated on `active`. The transcript is **not lost** — it lives in the engine audit envelope (`engine-events/<session_id>.json` / S3). The recruiter report (a future, separate module) reads the envelope for terminated sessions. This keeps the spec simple and avoids a race between the endpoint and the engine over who owns the row.

*Rejected alternative:* extending `record_session_result` to backfill result columns when `state='terminated'`. It preserves the column copy of the transcript but introduces a write-ordering race (endpoint vs. engine close) and complicates an audited helper. Deferred as a clean follow-up if/when the reporting module needs the columns rather than the envelope. Noted as the upgrade path, not built now.

### Audit events

Two new action constants in the session/audit vocabulary:
- `session.proctoring_violation` — every recorded non-terminal violation (`actor_id=candidate`, `tenant_id`, `session_id`, `kind`, `severity`, `correlation_id`).
- `session.proctoring_terminated` — the terminating event (adds `proctoring_outcome`, `violation_count`).

---

## Frontend design (`frontend/session`)

All proctoring code lives under a new `components/interview/proctoring/` directory and is only mounted inside the **live** session (never the pre-join wizard). It is lazy-loaded with the live `App` chunk, so the pre-join bundle budget (< 180 KB) is unaffected.

### `<ProctoringGuard>` (provider)

Wraps `<LiveInterview>` at the point it's rendered (in `view-controller.tsx`). Props: `config: ProctoringConfig`, `onTerminated: (reason) => void`, and the LiveKit session/room (for the disconnect on termination). If `config.enabled === false`, it renders children with **no** listeners attached (zero behavioural change). It composes the detector hooks, owns the `<ViolationBorder>` + `<FullscreenGraceOverlay>` render, and threads everything through `useProctoringController`.

### `useProctoringController`

The single policy/state owner:
- Tracks the local soft-violation count (display only; backend is authoritative on termination).
- `report(kind)` → POSTs `candidateSessionApi.proctoringEvent`, flashes `<ViolationBorder>` (red for hard, amber for soft) + a `sonner` toast naming the violation and, for soft, the count (`"Warning 2 of 3 — please stay in the interview window"`).
- On `{ terminated: true }` (or on **any hard** violation, fail-safe, even if the POST fails) → disconnect the room and call `onTerminated(reason)`.
- Exposes an **"armed"** boolean (see Correctness guards) that the detector hooks respect.

### Detector hooks (one responsibility each, independently testable)

| Hook | Watches | Emits |
|---|---|---|
| `useFullscreenGuard` | requests fullscreen when armed; `fullscreenchange` | on unexpected exit → show `<FullscreenGraceOverlay>` + start countdown. Re-enter within grace → `report('fullscreen_exit')` (soft) + resume. Countdown expiry → `report('fullscreen_abandoned')` (hard). |
| `useVisibilityGuard` | `document.visibilitychange` | `hidden` → `report('tab_switch')` (hard) |
| `useFocusGuard` | `window` `blur`/`focus` | `blur` → `report('focus_loss')` (hard), **deduped** against a `tab_switch` fired in the same tick |
| `useKeyboardGuard` | `keydown`, `contextmenu` | meaningful keypress → `report('keyboard')` (soft, **debounced** per burst); devtools-open combos (F12, Ctrl/Cmd+Shift+I/J/C) + Ctrl/Cmd+S/P/F → `preventDefault` + `report('keyboard')`; `contextmenu` → `preventDefault` only (deterrent, **not** a recorded violation) |
| `useDevtoolsGuard` | `resize` size-delta heuristic + periodic `debugger;` timing trap | either fires → `report('devtools')` (hard) |

`useKeyboardGuard` does **not** block Tab / Shift+Tab / Enter / Space / Escape so the **End interview** button stays keyboard-operable (accessibility rule in `frontend/session/CLAUDE.md`). Escape's fullscreen-drop is handled by `useFullscreenGuard`, not blocked here.

### Presentational components

- `<ViolationBorder>` — a fixed, `pointer-events-none` overlay: an inset glowing border that **pulses red** (hard) or **amber** (soft) for ~2.5s on each violation, then fades. Uses px tokens (`--px-danger`, `--px-caution`), Tailwind v4 utilities, and **respects `prefers-reduced-motion`** (static border, no pulse). `aria-live="assertive"` companion text for screen readers.
- `<FullscreenGraceOverlay>` — a full-cover frosted-glass layer (reusing the `px-glass-strong` language from the redesign) that **blurs/hides the interview content**, shows "Return to fullscreen to continue your interview", a prominent **Return to fullscreen** button (the required user gesture to re-enter), and a live countdown (`config.fullscreen_grace_seconds`). Returning calls `requestFullscreen()` from the click handler.

### Disclosure notice (pre-join)

When `preCheck.proctoring_enabled`, the Welcome / pre-join screen shows a compact, non-blocking **"This interview is monitored"** panel listing the rules in plain language: stay in fullscreen, don't switch tabs or leave the window, keyboard use and developer tools are detected, and repeated or serious violations end the interview. Static copy gated on the flag; no extra checkbox (Decision #6).

### Terminal screen + outcome plumbing

A proctoring termination must NOT route to the success `CompletionScreen`. Plumbing:
- `onTerminated(reason)` bubbles to `app.tsx`, which sets a new top-level terminal state `proctoringTermination` (reason string). `ViewController` checks it **before** the disconnect→completed routing in `OutcomeWatcher`, so the local proctoring outcome wins the race against the room disconnect that `cancel_room` will also cause.
- New `<ProctoringEndedScreen reason={...}>` — calm but clear: "Your interview was ended because our monitoring detected <human-readable reason>." No transcript, no retry (the token is consumed; rejoining a `terminated` session is rejected by the backend state gate).
- `components/interview/lib/session-outcome.ts` gains a frontend-only `'proctoring_terminated'` concept for this route. (It is **not** part of the agent's `session_outcome` LK attribute — the frontend knows locally why it ended.)

### API client (`lib/api/candidate-session.ts`)

Add the `proctoring` field to `StartSessionResponse`, `proctoring_enabled` to `PreCheckResponse`, and a `proctoringEvent` method:

```ts
export interface ProctoringConfig {
  enabled: boolean
  soft_violation_limit: number
  fullscreen_grace_seconds: number
}
export interface ProctoringEventBody { kind: ProctoringKind; occurred_at: string }
export interface ProctoringEventResult {
  terminated: boolean
  violation_count: number
  soft_violation_count: number
}
// candidateSessionApi.proctoringEvent(token, body) → POST .../proctoring/event
```

This file is **Human-Review-Required** (sole candidate API surface) and carries a **100% branch coverage** gate — the new method + its error branches must be fully covered.

---

## Correctness guards (do not weaken enforcement)

Because `blur` / `visibilitychange` are noisy and our own fullscreen/getUserMedia transitions emit them, three guards prevent self-inflicted terminations without softening a genuine switch:

1. **Armed gate.** Detectors enforce only after the session is fully live (agent has spoken / `hasSpoken` from `AgentUIWithLoader`) **and** fullscreen is established. The initial fullscreen request, the LiveKit connect, and `useEnsureMediaPublished` all settle *before* arming.
2. **Self-induced-transition suppression.** A short ignore-window is opened around our own `requestFullscreen()` / overlay interactions so the resulting `blur`/`fullscreenchange` doesn't count.
3. **Event dedupe.** One physical action (a tab switch fires *both* `blur` and `visibilitychange`) records as **one** violation — the controller coalesces events within the same tick, preferring `tab_switch`.

### Fail-safe

Proctoring must not become a way to *avoid* ending a flagged session: on any **hard** violation the frontend tears down locally (disconnect + `ProctoringEndedScreen`) **even if the `proctoringEvent` POST fails** (offline, blocked). The backend record is best-effort-but-authoritative-when-reached; the local end is unconditional. Conversely, if proctoring `enabled=false`, none of this mounts.

### Mobile & capability degradation

`useFullscreenGuard` feature-detects `requestFullscreen`. Where the Fullscreen API is unavailable/*partial* (notably iOS Safari), the fullscreen guard **disables itself** rather than firing `fullscreen_abandoned` falsely; the visibility/focus/keyboard guards still run where supported. The disclosure copy is unchanged. (Tightening mobile proctoring is out of scope; we don't fail a candidate for a capability their browser lacks.)

---

## Threat model (honest limitations)

To be added to `docs/security/threat-model.md` (candidate-facing surface change + new auth surface → mandatory update per root CLAUDE.md).

**What this stops:** casual tab-switching / window-leaving, the reflexive F12 / right-click, and a docked or already-open devtools panel in the common case.

**What it cannot stop (documented, not hidden):**
- A **tampered client** (the candidate controls the browser): they can patch out the listeners, block the `proctoringEvent` POST, or never call it. We optimise for the un-tampered common case; the backend records what it receives.
- **DevTools is not truly undetectable-proof:** opening it *before* navigation, disabling breakpoints (defeats the `debugger` trap with one click), or attaching a **remote debugger** evades detection.
- **Second device / camera-off-screen reading:** entirely out of band of any browser API.
- **Size-delta false positives:** browser zoom, OS accessibility zoom, or a docked translate/password panel can shrink the viewport. Mitigation: require a *sustained* delta beyond a generous threshold and prefer the `debugger` trap as the higher-confidence signal; accept a small false-positive rate as the cost of catching the common case. (This is why every termination is *recorded with its reason* — a recruiter can review a borderline `devtools` flag.)

The feature is a **deterrent + evidence-recorder**, not a guarantee. Marketing/recruiter-facing language should reflect that.

---

## Testing obligations

**Backend (`backend/nexus`):**
- `record_proctoring_event`: hard kind → terminated; soft below limit → not terminated; soft over limit → `soft_threshold_exceeded`; post-termination call → idempotent success; cross-tenant session id → 404 opacity.
- State machine: `active → terminated` legal; `terminated → *` rejected; `terminated` excluded from the engine's `record_session_result` (`active`-gated no-op).
- Migration: `terminated` accepted by the new CHECK; new columns default correctly; **cross-tenant read returns 0 rows** for the new columns (RLS inheritance check on `sessions`).
- `tenant_settings`: defaults surface via `get_tenant_settings` with no row; bounds validators reject out-of-range thresholds.
- `/start` + `/rejoin`: response carries the `proctoring` block sourced from `tenant_settings`.

**Frontend (`frontend/session`):**
- `lib/api/candidate-session.ts` — **100% branch** including the new `proctoringEvent` method + error narrowing.
- Each detector hook in isolation (jsdom): visibility/focus/keyboard event → correct `report` call; dedupe; debounce; armed-gate suppression of startup transitions.
- `useProctoringController`: hard → local end even when POST rejects (fail-safe); soft border/toast vs hard; `enabled=false` mounts no listeners.
- Composition test: `<ProctoringGuard><LiveInterview/>` — a hard violation routes to `<ProctoringEndedScreen>`, not `CompletionScreen` (negative control: with proctoring off, normal End → CompletionScreen still works).
- `prefers-reduced-motion`: `<ViolationBorder>` renders static.

**Two-app drift:** none of the synced files (`lib/utils.ts`, `lib/api/errors.ts`, `components/px/*`, logo, token mapping) are touched. The `candidate-session.ts` change is candidate-only and has no `frontend/app` twin.

---

## Open decision for review

**Optional sub-second debounce on hard `blur`/`tab_switch`.** As specified, a genuine tab/app switch ends the session immediately (Decision #4). I can optionally add a very short (~300–500ms) confirmation window where an *instantly-returned* focus (a transient OS popup that auto-dismisses) is downgraded to a recorded soft violation instead of a hard end. It reduces false-positive terminations from system interruptions at the cost of a tiny enforcement delay. **Default in this spec: NOT included** (honours your "hard = immediate" intent). Flag at review if you want it in.

---

## Build sequence (for the implementation plan)

1. **Migration `0042`** — `sessions` columns + CHECK alter + `tenant_settings` columns (+ rollback).
2. **Backend schemas/state machine** — `SessionState.TERMINATED`, transition graph, `ProctoringConfig`, `TenantSettings` fields, `PreCheckResponse.proctoring_enabled`.
3. **Backend service + endpoint** — `record_proctoring_event`, audit constants, `/start`+`/rejoin` config embed, router with declared rate limit. *(TDD: tests first.)*
4. **Frontend API client** — types + `proctoringEvent` (with branch tests).
5. **Frontend detector hooks** — five guards + controller, each test-first.
6. **Frontend presentational + plumbing** — `<ViolationBorder>`, `<FullscreenGraceOverlay>`, `<ProctoringGuard>`, `<ProctoringEndedScreen>`, `app.tsx`/`view-controller.tsx` outcome routing, pre-join disclosure.
7. **Threat-model doc update** + composition/negative-control tests.
```
