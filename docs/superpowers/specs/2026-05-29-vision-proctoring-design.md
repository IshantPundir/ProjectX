# Vision-Based Proctoring — Design

- **Date:** 2026-05-29
- **Status:** Approved (brainstorm) → pending implementation plan
- **Extends:** `2026-05-21-candidate-session-proctoring-design.md` (the behavioral proctoring layer + migration `0043`)
- **Surfaces touched:** `frontend/session` (live plane), `backend/nexus` (authoritative plane + new module), `frontend/app` report page (review surface)

---

## 1. Context & Goal

ProjectX runs AI-led video interviews with **no human supervising the live session**. The
existing proctoring (`session/proctoring.py` + `frontend/session/.../proctoring/`, migration
`0043`) is **purely behavioral and client-side**: tab-switch, focus-loss, fullscreen-exit,
devtools, keyboard. It has no camera-frame analysis and the JS-only guards have known
bypasses (candidates can escape fullscreen / open devtools).

Cheating in unsupervised AI interviews is easy and growing. This effort adds **vision-based
proctoring**: detect a second person, detect reading from a phone/notes/second screen (with a
gaze heatmap), and detect a synthetic/injected camera feed — robust to poor webcams, dark
rooms, and glasses (target population: Indian candidates on commodity laptops). It must be
enterprise-grade: secure, auditable, cost-controlled, and defensible.

---

## 2. Scope

### In scope (v1)
- **① Someone else present / helping** — multi-face count + off-screen-person gaze.
- **② Reading from phone / notes / second screen** — coarse gaze zones + reading-pattern
  detection + **gaze heatmap** (the headline feature).
- **③ Synthetic / injected feed** — liveness (blink cadence, micro-motion), loop/frozen-frame
  detection, virtual-camera device-label detection (client). High-recall *flag*, not a
  definitive deepfake classifier.
- **④ Tamper reconciliation** — server-vs-client divergence + non-vision integrity signals.
- Harden the existing behavioral guards (fullscreen re-entry, devtools signal).
- Dev-only live **debug visualization** overlay.

### Out of scope (deferred, see §13)
- **Identity fraud / proxy test-taker** (needs reference-photo capture step; most PII-heavy).
- **Audio speaker-diarization** (composite audio is mixed candidate+agent → too noisy).
- **Dedicated anti-spoofing / PAD model** (v1 uses liveness heuristics only).
- **Precise point-of-gaze / calibration** (v1 is calibration-free coarse zones).
- **Per-track candidate egress** (v1 analyzes the existing composite recording).

---

## 3. Decision Log (keystone choices + rationale)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Evidence + flag only.** Vision signals NEVER auto-terminate or auto-reject. They build a reviewable risk score + timeline + heatmap. Live UI may *nudge* but never ends the session. | EEOC/AIVIA "human sign-off", "Borderline → human review always" invariants; gaze false-positives too high to auto-act. |
| D2 | **Server-side post-session re-analysis on the R2 recording is the authoritative source of truth.** | Most robust answer to "detect *any kind* of tampering" — candidate can't tamper with pixels they never had. R2 egress is $0; sparse-frame compute is cheap (~$30–80/mo @ 500/day). |
| D3 | **Client live plane is non-authoritative** — hardened behavioral guards + lightweight MediaPipe advisory nudges + dev debug overlay. | Real-time deterrence + UX without trusting a spoofable client; smallest live attack surface for *evidence*. |
| D4 | **Vision compute = dedicated `vision-worker` service (B1).** Own Docker image (mediapipe + opencv + ffmpeg), own Dramatiq queue. | Keeps `nexus` image lean; isolates heavy native deps (existing PyO3/3.13 segfault caution with livekit deps); scales/cost-optimizes independently (spot instances OK). |
| D5 | **Gaze = calibration-free coarse zones**, head-pose-primary / iris-secondary. | Zero candidate friction; robust to poor webcams, dark rooms, drift, **and glasses**; sufficient for human-review flagging. Precise point-of-gaze would be fragile in exactly these conditions. |
| D6 | **Store features only — never raw frames or biometric templates.** Raw video = the already-consented R2 recording; reviewer seeks into it by timestamp. | Privacy-preserving consensus; minimizes biometric-data surface (BIPA/GDPR). |
| D7 | **Vision default-ON in dev** (`proctoring_vision_enabled` flag exists, defaults `true`). **Action item: flip to OFF/opt-in before production.** | Solo dev, dev stage — opt-in gating added pre-prod. See §10 action items. |
| D8 | **Consent-gated**: if vision proctoring is enabled, candidate must consent to the biometric-monitoring disclosure to start. | Cleanest BIPA/GDPR position; mirrors existing recording-consent gate. |
| D9 | **Manual-first testing.** No fixture clip library; debug overlay + self-recorded test sessions are the eval loop. Keep only cheap pure-logic + RLS + idempotency tests. | Solo developer, tight timeline; aligns with "dev tooling not CI eval suites" preference. |

---

## 4. Architecture — Two Planes

```
LIVE PLANE (client, frontend/session)            AUTHORITATIVE PLANE (server, post-session)
─────────────────────────────────────            ──────────────────────────────────────────
behavioral guards (hardened) ─┐                   recording_status='ready' (migration 0050)
MediaPipe Face Landmarker ────┤ nudges (advisory)        │ enqueue
  → gaze zones, face count    │                          ▼
  → blink/EAR, head pose      │                 [vision queue] analyze_session_proctoring
VisionDebugOverlay (dev only) │                          │  (dedicated vision-worker, B1)
                              │                          ▼
  POST .../proctoring/event ──┘── heartbeats +    pull 720p MP4 from R2 (free egress)
       coarse signals  ───────────────────────►  sample ~2–3 fps → detector suite
                                                 ▼
                                          session_proctoring_analysis (1 row/session)
                                                 ▼
                                          report page "Proctoring & Integrity" panel
                                                 (human review; jump-to-timestamp on recording)
```

- The live plane is **deterrence + UX + integrity heartbeats**. Never trusted as evidence.
- The authoritative plane is the **source of truth**. It also reconciles its ground truth
  against the client's live-reported signals to detect tampering (D2, §7④).

---

## 5. Live Plane (client — `frontend/session`)

### 5.1 Harden existing behavioral guards
Audit + fix the known bypasses in `components/interview/proctoring/`:
- **Fullscreen re-entry**: make the grace overlay reliably re-detect exits and re-prompt;
  close the path where a candidate exits fullscreen and stays out.
- **Devtools**: the window-size-delta heuristic is unreliable — harden the signal (keep it
  best-effort; the server recording is the real backstop).
- Framing: **JS-only enforcement is inherently bypassable** — these fixes raise the bar for
  casual cheating; the authoritative integrity guarantee is the server plane (D2).

### 5.2 New `use-vision-guard.ts` hook
- Runs **MediaPipe Face Landmarker** (Tasks API, WASM + GPU delegate), `numFaces > 1`,
  blendshapes + 4×4 transformation matrix enabled, on the candidate's local camera stream.
- Produces per-tick signals: face count, gaze zone (head-pose-primary), blink/EAR,
  landmark confidence, `gaze_signal_quality`.
- Emits **advisory nudges** via the existing `ViolationBorder` + toast:
  `face_not_visible`, `multiple_faces`, `looking_away_sustained`. **Never terminates**
  (D1) — distinct from behavioral hard violations which keep current termination behavior.
- Reports coarse aggregate signals + **signed, sequence-numbered heartbeats** to the backend
  via the existing `POST /api/candidate-session/{token}/proctoring/event` (extended kinds),
  for tamper reconciliation. Also reports **virtual-camera device labels** (`enumerateDevices`).

### 5.3 `VisionDebugOverlay` (dev-only, temporary)
- Canvas overlay on the candidate's local video preview, gated behind `?proctorDebug=1` +
  `NEXT_PUBLIC_PROCTORING_DEBUG`. **Never shown to a real candidate.** Renders the existing
  hook output (no extra inference): face mesh / iris points, per-face bbox + count, current
  gaze zone (grid highlight), head-pose yaw/pitch/roll + axis gizmo, blink/EAR, confidence,
  `gaze_signal_quality` (watch it flip on glasses), inference FPS, current nudge state.
- **Marked dev-only / keep-gated-for-production** — must never ship enabled.

---

## 6. Authoritative Plane (server — `backend/nexus`)

### 6.1 `vision-worker` service (B1)
- New Docker image with `mediapipe` + `opencv` + `ffmpeg`. Own Dramatiq queue (`vision`).
- Scales independently of the LLM-task worker; spot/cheap instances acceptable.

### 6.2 Trigger & actor
- On `recording_status='ready'` (existing migration `0050` lifecycle), enqueue
  `analyze_session_proctoring(session_id)` on the `vision` queue.
- Actor (bypass-RLS session, explicit `tenant_id` filter — same pattern as
  `interview_runtime.service`): pull MP4 from R2, sample ~2–3 fps, run detectors, write
  `session_proctoring_analysis` row. **Idempotent on `session_id`**; retry-safe; permanent
  vs transient error classification per existing actor discipline.

---

## 7. Detector Suite

All detectors emit a **confidence** and contribute to a combined **risk score** + **risk band**
(`low`/`elevated`/`high`/`insufficient_data`). Never a verdict.

**① Someone else present / helping**
- Multi-face count per frame; **debounced** (flag only sustained ≥2 faces, e.g. >2s) so a
  pass-through doesn't trip it. Output: face-count timeline + flagged intervals.
- Off-screen-person gaze: derived from the gaze detector (②).

**② Reading from phone / notes / second screen (+ heatmap)**
- **Gaze zone per frame**: head-pose (yaw/pitch/roll from the 4×4 matrix) **primary** + iris
  blendshapes (`eyeLookIn/Out/Up/Down`) **secondary refinement**. Zones: center (on-screen),
  left, right, up, **down-away** (phone/notes tell).
- **Reading-pattern detector**: rhythmic horizontal scanning + periodic line-return saccades
  *while off-screen* = reading signal (distinct from idle glancing). Requires **head-pose
  corroboration** when iris is degraded.
- **Heatmap**: zone-occupancy grid accumulated over the session + off-screen-% timeline.

**③ Synthetic / injected feed**
- Client: virtual-camera **device-label detection** (reported as integrity signal).
- Server (on recording): **blink cadence** (EAR over time; humans blink irregularly ~10–20/min —
  loops/static show none or perfectly periodic), **head micro-motion** liveness, **loop/frozen
  detection** (repeated frame sequences). High-recall flag for review; **not** a deepfake verdict.

**④ Tamper reconciliation**
- **Server-vs-client divergence**: client claimed "1 face, on-screen" but recording shows 2
  faces / no face / off-screen → tamper flag.
- **Non-vision integrity**: heartbeat gaps & sequence anomalies, virtual-cam label, WebRTC
  track-stat anomalies (mid-session resolution/fps swap), recording gaps (recording stopped
  while session active), client proctoring disabled/absent.

### Cross-cutting robustness (poor webcam / dark room / **glasses**)
- **Confidence gating**: low landmark confidence → frame marked **`unscorable`** (track
  `unscorable_pct`); never false-flag on frames we can't see. Optional CLAHE/gamma preprocessing
  to recover landmarks in dark frames.
- **Glasses**: lens glare / refraction / frame occlusion / tinted lenses degrade iris.
  Mitigations: head-pose-primary gaze (largely glasses-immune); **auto-downweight iris** under
  specular-glare / low eye-region confidence → fall back to head-pose-only zones, mark frames
  **reduced-precision** (not unscorable — gross direction still valid); reading-pattern requires
  head-pose corroboration; per-session **`gaze_signal_quality`** indicator
  (good / glasses-degraded / low-light / unscorable) surfaced to the reviewer.
- Risk band carries an explicit **`insufficient_data`** state so a reviewer never sees a
  confident flag built on unseeable frames.

---

## 8. Data Model

New tenant-scoped table **`session_proctoring_analysis`** (one row per session):
- `session_id` (FK, ON DELETE CASCADE), `tenant_id`, `status`
  (`pending`/`analyzing`/`ready`/`failed`/`unscorable`)
- `risk_score` (int), `risk_band` (`low`/`elevated`/`high`/`insufficient_data`)
- `findings` (JSONB) — per-detector `{detector, confidence, flagged_intervals:[{start_s,end_s,…}]}`
- `gaze_heatmap` (JSONB) — zone-occupancy grid + off-screen-% timeline
- `gaze_signal_quality` (text), `unscorable_pct` (numeric)
- `tamper_findings` (JSONB)
- `model_versions` (JSONB) + `analysis_completed_at` — **auditability**: reproducible/defensible
  flags (EEOC).

**RLS**: canonical pair (`tenant_isolation` USING + WITH CHECK with `NULLIF(...)::uuid`,
`service_bypass`). Add to `_TENANT_SCOPED_TABLES` + the `_assert_rls_completeness` startup check.
Migration carries a rollback script.

**Never stored**: raw frames, face crops, biometric templates (D6).

**Tenant config** (extend `tenant_settings`): `proctoring_vision_enabled BOOLEAN` (default `true`
in dev per D7; flip pre-prod). Behavioral proctoring flags from `0043` unchanged.

---

## 9. Report Surfacing (`frontend/app` report page)

New **"Proctoring & Integrity"** panel (the page already has recording playback):
- **Risk band badge**, explicitly *"for reviewer assessment — not a decision."*
- **Detector breakdown** with confidence + plain-English explanation.
- **Gaze heatmap** (zone grid) + off-screen-% timeline strip.
- **Flagged-moments list** — each interval has a **"jump to N:NN"** that seeks the recording
  player to that timestamp (the auditable-evidence experience).
- **Integrity/tamper findings** + **`gaze_signal_quality`** indicator.
- Never shows auto pass/fail. Visual polish handled at build time (frontend-design skill).

---

## 10. Privacy, Consent & Compliance

- **Consent (load-bearing)**: extend the existing timestamped consent + welcome disclosure to
  explicitly state camera-based automated monitoring incl. **facial-geometry / eye analysis**.
  **BIPA**: face-geometry extraction can be a biometric identifier even without identity-matching
  → informed consent + retention/destruction policy + no-sale, logged before recording.
  **Version the consent string.** **D8**: declining blocks start (when vision enabled).
- **AIVIA**: add the AI-monitoring disclosure line.
- **GDPR**: facial data = special-category → explicit consent + a short **DPIA note** under
  `docs/security/`. Lawful basis documented.
- **EEOC / bias**: gaze/face models have demographic performance gaps (skin tone, glasses).
  Mitigations: human-review-only (D1), `insufficient_data` / signal-quality states, and the
  `model_versions` audit trail. Detector thresholds carry a **documented bias-review obligation**.
- **Data minimization**: features only, never frames/templates (D6).
- **Threat-model update**: `docs/security/threat-model.md` must be updated (candidate-facing
  surface changes) — included in the implementation PR.

### Pre-production action items (must not be dropped)
1. **Flip `proctoring_vision_enabled` default → OFF / opt-in** before production deploy (D7).
2. Add the **DPIA note** + bias-review note under `docs/security/`.
3. Remove or hard-gate the `VisionDebugOverlay`.

---

## 11. Testing (manual-first, solo dev)

- **Eval loop = manual**: run real sessions through `VisionDebugOverlay`; self-record test
  sessions simulating each scenario (look away, hold up phone, second person, glasses, dark) and
  eyeball the report. No fixture clip library (D9).
- **Cheap automated tests only** (no video): pure-logic unit tests for risk-scoring /
  severity-policy / tamper-reconciliation (synthetic signal inputs); `analyze_session_proctoring`
  **idempotency**; **mandatory cross-tenant RLS test** on `session_proctoring_analysis`
  (cross-tenant read → 0 rows).
- **Frontend tests**: `use-vision-guard` (mocked MediaPipe), advisory-nudge rendering, debug
  overlay defaults **off**, behavioral-guard hardening (fullscreen re-entry, devtools).
- **Shadow mode**: risk bands ship **advisory** until thresholds are empirically tuned via the
  debug overlay + real sessions.

---

## 12. Module Boundaries & Files

**Backend**
- New `app/modules/proctoring/` — promotes `session/proctoring.py` policy into a first-class
  module; owns post-session analysis orchestration (Dramatiq actor), detector logic,
  risk-scoring, schemas, report assembly. (Separate from `analysis`/`reporting` stubs, which are
  about *interview* scoring.)
- New `session_proctoring_analysis` table + migration (+ `tenant_settings.proctoring_vision_enabled`).
- New `vision-worker` Docker image + compose service + `vision` Dramatiq queue.
- Extend `session/router.py` proctoring-event endpoint (new kinds + heartbeats).

**Frontend (`frontend/session`)**
- `components/interview/proctoring/use-vision-guard.ts`, `VisionDebugOverlay.tsx`; new nudge
  kinds in `violation-kinds.ts`; behavioral-guard hardening; extend
  `lib/api/candidate-session.ts` types.

**Frontend (`frontend/app`)**
- "Proctoring & Integrity" panel on the report page + jump-to-timestamp wiring.

---

## 13. Future / Deferred
- Identity fraud / proxy detection (reference-photo capture + face-match + continuity).
- Audio speaker-diarization (needs per-track or isolated candidate audio).
- Dedicated anti-spoofing / PAD (deepfake) model.
- Precise point-of-gaze with calibration (gaze module designed to allow this mode later).
- Parallel `TrackEgress` of the candidate camera for max-fidelity / isolated-track analysis.

---

## 14. Rollback
- Migration ships with a down-script (drop `session_proctoring_analysis`,
  drop `tenant_settings.proctoring_vision_enabled`).
- `vision-worker` is a separate service — disabling it / draining the `vision` queue stops all
  authoritative analysis without touching the live interview path.
- `proctoring_vision_enabled=false` disables the live vision plane; behavioral proctoring
  (migration `0043`) is untouched and independent.
```
