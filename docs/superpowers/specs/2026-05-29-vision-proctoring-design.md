# Vision-Based Proctoring — Design

- **Date:** 2026-05-29 (live plane); **server plane v1 design 2026-05-30**
- **Status:** Live plane (Plan A) shipped to local `main`. **Server plane (Plan B+C merged) — approved brainstorm 2026-05-30, pending implementation plan.**
- **Extends:** `2026-05-21-candidate-session-proctoring-design.md` (the behavioral proctoring layer + migration `0043`)
- **Surfaces touched:** `frontend/session` (live plane), `backend/nexus` (authoritative plane + new module), `frontend/app` report page (review surface)

> **Reading guide.** §§1–14 are the original 2026-05-29 design; some of it (esp. §7 detector suite, §2 in-scope list) was written for an earlier MediaPipe-on-the-server idea and a broader v1. **§16 is the authoritative spec for the server-plane v1 being built now** (post-session L2CS-Net analysis + report surfacing). Where §16 and the older sections disagree, **§16 wins.** §15 records what already shipped (live plane); §16 records the model-choice research + the locked server-plane v1 decisions.

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

> ⚠️ **Server-plane v1 narrows this list.** The server pass being built now is **gaze (②) + multi-face (① count) + heatmap + report surfacing only**; synthetic-feed (③) and tamper (④) are deferred to pass 2. The live-plane items already shipped. **See §16.1 for the locked server-plane v1 scope.**

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
| D1 | **~~Evidence + flag only; vision never auto-terminates.~~ REVISED 2026-05-29 (see §15):** the **server** plane stays evidence-for-human-review (never auto-rejects a candidate). The **live** plane now treats vision warnings (multiple-faces / face-not-visible / looking-away) as **SOFT violations** that count toward the shared soft-violation limit and **terminate the session on escalation**, exactly like the behavioral soft kinds. | Operator chose real-time enforcement over flag-only after live testing. Termination ends a *session* (then human-reviewed), not an auto-reject — same posture as the existing behavioral proctoring. **Tradeoff:** coarse head-pose gaze has higher false-positives than behavioral signals, so legit candidates can be terminated; soft (counted, not instant) + tunable windows partially mitigate. |
| D2 | **Server-side post-session re-analysis on the R2 recording is the authoritative source of truth.** | Most robust answer to "detect *any kind* of tampering" — candidate can't tamper with pixels they never had. R2 egress is $0; sparse-frame compute is cheap (~$30–80/mo @ 500/day). |
| D3 | **Client live plane is non-authoritative** — hardened behavioral guards + lightweight MediaPipe advisory nudges + dev debug overlay. | Real-time deterrence + UX without trusting a spoofable client; smallest live attack surface for *evidence*. |
| D4 | **Vision compute = dedicated `vision-worker` service (B1).** Own Docker image, own Dramatiq queue (`vision`). **REVISED 2026-05-30 (see §16.9):** image is **torch + l2cs + face-detection + opencv + ffmpeg** (no mediapipe server-side — L2CS's RetinaFace serves both gaze and face-count). | Keeps `nexus` image lean; isolates heavy native deps (existing PyO3/3.13 segfault caution with livekit deps); scales/cost-optimizes independently (spot instances OK). |
| D5 | **Gaze = calibration-free coarse zones. REVISED 2026-05-29 (see §15): live gaze is HEAD-POSE-ONLY** (iris influence tried then reverted — fragile with glasses/noise; sign/mirror bugs). Iris/eye-aware gaze is deferred to the **server** plane. | Zero friction; robust to poor webcams/dark rooms/glasses. The live plane is a coarse deterrent, not the accurate detector — so eye-aware precision isn't needed live and added more fragility than value. |
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

> ⚠️ **STALE for the server plane.** This section was written for a MediaPipe-on-the-server idea with liveness + tamper in v1. The server-plane v1 being built now uses **L2CS-Net self-baseline gaze** and defers liveness (③) + tamper (④). **See §16 (authoritative).** Retained for historical context + the pass-2 backlog.

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

---

## 15. Post-implementation revisions (2026-05-29)

Plan A (live client plane) was implemented and **merged to local `main`** (not pushed). Live testing then drove these revisions to the decisions above:

- **Live gaze is HEAD-POSE-ONLY (revises D5).** An eyes-co-primary *signed eye-gaze* was tried for the dot + zone classifier, then reverted: fragile with glasses, noisy, and sign/mirror bugs. The live plane is a coarse *deterrent*; accurate eye-aware gaze belongs to the server plane.

- **Live vision warnings are SOFT VIOLATIONS that terminate (revises D1).** `multiple_faces`, `face_not_visible`, `looking_away_sustained` are added to the backend `ProctoringKind` (`session/schemas.py`) + `VIOLATION_SEVERITY` (`session/proctoring.py`), all `soft`. They flow through the SAME path as the behavioral soft kinds: same toast ("Warning N of 3: please avoid …") + border flash, the shared backend soft-violation counter, recorded in `sessions.proctoring_violations`, and the backend terminates on escalation (`soft_threshold_exceeded`). The client fires one violation per sustained occurrence (rising edge, re-arm on clear) via `useVisionGuard → controller.report`. Sustain windows (tunable, `nudge-kinds.ts`): multiple_faces 500 ms, face_not_visible 2.5 s, looking_away 1 s. The separate non-blocking `GazeWarningOverlay` banner was removed in favor of the unified toast.
  - **Compliance caveat (must-track):** coarse head-pose gaze has higher false-positives than the behavioral signals, so **legit candidates can be auto-terminated** (especially looking-away at a 1 s window). Soft (counted, not instant) + tunable windows partially mitigate. Revisit the windows — or exclude `looking_away_sustained` from the counter — if false-positives surface in real use. Termination ends a *session* (then human-reviewed), not an auto-reject, so the EEOC/AIVIA "human sign-off on hiring" invariant still holds.

- **The accurate gaze upgrade — forward path (deep research, 2026-05-29).** Calibration-free *pixel* gaze on a commodity webcam is impossible (~4.3° / ~4.7 cm person-independent ceiling; every better figure needs few-shot calibration, a mobile front-cam, or depth hardware). Coarse region-level is sufficient for proctoring. The real accuracy lives **server-side (Plan B-plus):** an appearance-based gaze-direction CNN — **L2CS-Net** (MIT code) or **3DGazeNet** (ECCV 2024, best calibration-free cross-domain) — + **implicit/auto-calibration** (SalGaze saliency / FAZE few-shot from known on-screen targets: the question text, the interviewer tile) + temporal smoothing, run offline on the 720p recording. Direction → screen needs camera-on-top + screen-size geometry (browser screen-geometry acquisition is an unsolved engineering unknown). **Weight-licensing gate:** pretrained gaze weights (Gaze360/MPIIGaze) carry research/non-commercial dataset terms — not auto-cleared for commercial use; verify before shipping.

- **Shipped vs still future.** Shipped: the live client plane only — head-pose gaze + multi-face + **soft-violation termination** + dev debug overlay + candidate consent disclosure. Still future (Plan B/C): the `vision-worker`, the `session_proctoring_analysis` table, the report heatmap surfacing — **now fully specced in §16** (tamper reconciliation + liveness + learned risk-scoring pushed to a pass 2). The §10 pre-production action items still stand.

---

## 15b. Model-choice research (2026-05-30)

Before committing to a server-side gaze model, ran a focused multi-source web research pass (the deep-research workflow itself failed mid-run; redone directly). **Headline: "use a newer model" is the wrong axis — the gating constraint is the training *data*, and it is identical for old and new models.**

- **Datasets are nearly all non-commercial.** Gaze360 (L2CS-Net's weights) is research-only and explicitly forbids "models trained on dataset" in commercial apps; MPIIGaze/MPIIFaceGaze and ETH-XGaze are CC-BY-**NC**-SA; EYEDIAP and GazeCapture are research-only. GazeFollow is CC-BY (commercial-OK) but is a *gaze-following* dataset, not gaze-direction. **Therefore every released gaze-direction weight (L2CS-Net, MobileGaze, 3DGazeNet, MAGE, GazeSymCAT) is NC-tainted — upgrading the model does not clear the licensing gate (resolves Gate #1).**
- **Code licenses:** L2CS-Net MIT, `yakhyo/gaze-estimation` (MobileGaze, a cleaner modern L2CS-style reimpl, ResNet/MobileNet/MobileOne + ONNX) MIT, Gaze-LLE MIT. **3DGazeNet has NO license file → all-rights-reserved, the riskiest of the set.**
- **Gaze-LLE (CVPR 2025, frozen DINOv2/Apache-2.0) is the wrong task.** It does gaze-*following* (where in the *visible frame* a person looks → 64×64 heatmap + in/out-of-frame score). In a webcam interview the screen/phone is off-frame, so everything reads "out of frame" — it cannot distinguish screen vs phone. Not usable for screen-region gaze.
- **Newer direction models only shave ~1–2° of benchmark angular error** (e.g. ETH-XGaze ~3.6°, Gaze360 ~9°). For our **coarse** zone/reading/off-screen target that delta is invisible. **L2CS-Net is therefore NOT meaningfully outdated for coarse proctoring.**
- **Commercially-clean alternative (for later):** MediaPipe Face Landmarker is Apache-2.0 and exposes `eyeLook{Up,Down,In,Out}` blendshapes (eye-direction) + iris + head pose — zero dataset taint, no retrain. Lower accuracy ceiling than an appearance CNN. The other clean path is retraining the MIT MobileGaze/L2CS architecture on synthetic data (UnityEyes / NVGaze / UE-rendered).

**Decision (operator, 2026-05-30): ship L2CS-Net + the pre-fetched Gaze360 `.pkl` weights for v1.** Install `pip install git+https://github.com/edavalosanaya/L2CS-Net.git@main`; `from l2cs import Pipeline, render`; experiment at `tmp/glaze_live.py`; weights at `tmp/L2CSNet-…/Gaze360/L2CSNet_gaze360.pkl`. **Baked-in caveats:** (1) the gaze model sits behind a thin `GazeEstimator` interface so retrained-clean-weights / MediaPipe drop in with no downstream change; (2) **"replace the NC Gaze360 weights before commercial GA" remains an OPEN pre-production action item** — these weights are dev/POC only and are legally unsafe to ship to a paying tenant (see §16.8).

---

## 16. Server-Plane v1 — Post-Session Vision Analysis (AUTHORITATIVE)

This section supersedes the older §6–§9 detail for the work being built now. It merges the former "Plan B" (server analysis pipeline) and "Plan C" (report surfacing) into one feature. Brainstorm approved 2026-05-30.

### 16.1 v1 scope (locked)

**In:** offline L2CS-Net gaze on the R2 recording → **self-baseline** coarse zones + reading-sweep + down-glances + sustained-off-screen; **multi-face count**; **yaw×pitch heatmap** + off-screen-% timeline; a **coarse 3-tier integrity band** from transparent thresholds; **report surfacing** with jump-to-timestamp. The server plane is **evidence for human review — it never auto-rejects a candidate** (the live plane's soft-violation termination is a separate, already-shipped mechanism; see §15).

**Deferred to pass 2 (NOT in v1):** liveness / synthetic-feed detection (former §2③), tamper reconciliation (former §2④, §7④), and any *learned* composite risk-scoring beyond the simple-threshold band. (The live plane already emits virtual-camera device labels; nothing server-side consumes them in v1.)

### 16.2 Gaze model behind a swappable seam (Gate #1 resolved)

- `app/modules/vision/gaze/base.py` — `GazeEstimator` protocol: `estimate(frame_bgr) -> list[FaceGaze]` where `FaceGaze = {bbox, pitch, yaw, score}`. Angles in radians, camera frame, sign convention pinned and documented in the protocol docstring.
- `app/modules/vision/gaze/l2cs.py` — wraps the `l2cs` `Pipeline` (Gaze360 weights, `arch='ResNet50'`, CPU). L2CS's built-in **RetinaFace** detector yields per-face gaze **and** the multi-face count — **one detector serves both gaze and face-count; no MediaPipe server-side** (simplifies the §6.1/D4 image).
- The `.pkl` weights path + model id come from `AIConfig`/env, never hardcoded — consistent with the project's "model swap = env change" rule. **This interface is the clean-weights swap point** (retrained synthetic-data weights, or a MediaPipe estimator, drop in with zero downstream change). NC-weights replacement before GA is tracked in §16.8.

### 16.3 Self-baseline gaze → signals (Gate #2 resolved)

We do **not** map gaze to absolute screen pixels (needs browser-unavailable extrinsics + screen size — deferred indefinitely). Instead, **per session**:

1. **Baseline** = the mode (densest cluster) of (yaw, pitch) over the whole session ≈ "looking at the screen." Robust to where the camera sits relative to the screen and to per-candidate head posture.
2. **Zones** = coarse deviations from baseline: `center` (on-screen) / `left` / `right` / `up` / `down` / `far_off`. `down` beyond a pitch threshold is the phone/notes tell.
3. **Temporal smoothing** over a short rolling window (debounce; suppress single-frame flips); track a per-frame confidence and mark low-confidence frames `unscorable` (carry `unscorable_pct`).
4. **Derived detectors** (pure functions in `detectors.py`, unit-tested on synthetic angle streams):
   - `off_screen_sustained` — outside `center` beyond a threshold for ≥ N s.
   - `reading_sweep` — rhythmic horizontal yaw oscillation (line-scan signature).
   - `down_glances` — repeated brief pitch-down excursions (count + timestamps).
   - `multi_face_intervals` — ≥2 faces sustained > debounce window.
5. **Heatmap** = 5×5 yaw×pitch occupancy grid (relative to baseline) + an off-screen-% timeline.

### 16.4 Pipeline / actor

`@dramatiq.actor(queue_name="vision")` `analyze_session_proctoring(session_id, tenant_id)`:
1. Acquire a session via the bypass helper, then `SET LOCAL app.current_tenant`; **every query also filters by the explicit `tenant_id`** (same belt-and-suspenders pattern as `interview_runtime.service`). Idempotency: skip if a row already exists in a terminal/active state (status column, unique on `session_id`).
2. Presign + download the R2 recording; `ffmpeg` sample frames at **~5 fps**.
3. Per frame → `GazeEstimator.estimate` → primary face (largest bbox) pitch/yaw + face count; accumulate.
4. Post-process (§16.3) → zones, detectors, heatmap, flagged intervals, `gaze_signal_quality`, `unscorable_pct`.
5. Thresholds → 3-tier `risk_band` (§16.5). Persist **features only** (no frames). status → `ready` (or `failed` / `unscorable`).
- CPU inference is fine offline for MVP (a ~20-min session ≈ a few minutes wall-clock). Retry-safe; permanent-vs-transient error classification per existing actor discipline.

### 16.5 Risk band (transparent, not learned)

`risk_band ∈ {low, medium, high, insufficient_data}`, computed by **simple documented thresholds** over the detector summary (e.g. off-screen-% and multi-face dominate; down-glance count and reading-sweep contribute). **Labelled in the UI "for review, not a decision."** `insufficient_data` when `unscorable_pct` is too high — a reviewer never sees a confident band built on unseeable frames. (A *learned* score replaces these thresholds in pass 2.)

### 16.6 Data model

New tenant-scoped table **`session_proctoring_analysis`** — **next free migration number (≥ 0051; 0050 is `session_recording`)**, with a rollback down-script:
- `id`, `tenant_id`, `session_id` (FK `sessions` ON DELETE CASCADE, **UNIQUE**), `status` (`pending`/`running`/`ready`/`failed`/`unscorable`)
- `risk_band` (text)
- `detector_summary` (JSONB) — `{off_screen_pct, down_glance_count, reading_sweep_intervals, max_faces, multi_face_intervals:[…]}`
- `gaze_heatmap` (JSONB) — 5×5 occupancy grid + off-screen-% timeline
- `flagged_intervals` (JSONB) — `[{start_ms, end_ms, kind, confidence}]`
- `gaze_signal_quality` (text: good / glasses-degraded / low-light / unscorable), `unscorable_pct` (numeric)
- `model_versions` (JSONB — gaze model id + weights hash + pipeline version, for EEOC auditability), timestamps
- **RLS:** canonical `tenant_isolation` (USING + WITH CHECK, `NULLIF(...)::uuid`) + `service_bypass` pair; `GRANT … TO nexus_app`; **register in `_TENANT_SCOPED_TABLES`** so the `_assert_rls_completeness` boot check covers it.
- **Never stored:** raw frames, face crops, biometric templates (D6).
- **Tenant config:** `proctoring_vision_enabled` (default flips to OFF/opt-in pre-prod — §16.8).

### 16.7 Trigger & report surfacing

- **Trigger:** in `app/modules/session/recording.py` right after `_reconcile` flips `recording_status → 'ready'` (~`recording.py:144`), enqueue `analyze_session_proctoring(session_id, tenant_id)` if no prior analysis row. Pull-based, matching how recording readiness is already discovered on report-page read.
- **Read API:** sibling endpoint `GET /api/reports/session/{id}/proctoring`; `useSessionProctoring(sessionId)` hook in `frontend/app` mirroring `use-session-recording.ts`.
- **UI:** **right-sidebar** `ProctoringIntegrityPanel` in `ReportView` — compact: band badge + `gaze_signal_quality` + top flagged moments. An **"expand"** opens a dialog/drawer with the full detector breakdown, the heatmap (needs the room), and the full flagged-moments list. **Jump-to-timestamp** anywhere fires an `onSeek(ms)` lifted to `ReportView`, which seeks the existing `SessionPlayback` `videoRef` (scrolls it into view). Never shows auto pass/fail.

### 16.8 Pre-production action items (server plane — kept OPEN)

In addition to the §10 items (flip `proctoring_vision_enabled` → opt-in; DPIA + bias-review under `docs/security/`; BIPA/GDPR biometric consent):
- **Replace the NC Gaze360 weights before commercial GA.** The L2CS Gaze360 `.pkl` is non-commercial; it is the v1 dev/POC estimator only. Swap to clean weights (retrained MIT architecture on synthetic data) or the Apache-2.0 MediaPipe estimator via the `GazeEstimator` seam (§16.2) before shipping to a paying tenant. **Do not close this item by shipping NC weights.**

### 16.9 Module boundaries (server plane)

**Backend — new `app/modules/vision/`:**
- `gaze/base.py` (`GazeEstimator` protocol), `gaze/l2cs.py` (L2CS impl)
- `detectors.py` (pure self-baseline zone/reading/down-glance/off-screen/multi-face logic + band thresholds)
- `analysis.py` (frame sampling + orchestration), `actors.py` (`analyze_session_proctoring`)
- `models.py` (`SessionProctoringAnalysis` ORM), `service.py` (report read), `schemas.py`
- New `nexus-vision-worker` compose service + own image (**torch + l2cs + face-detection + opencv + ffmpeg**; no mediapipe) running `dramatiq app.worker -Q vision`.
- New migration (≥0051) for the table; enqueue hook in `session/recording.py`; report-read endpoint in the reporting router.

**Frontend (`frontend/app`):** `ProctoringIntegrityPanel` + expand dialog, `useSessionProctoring` hook, `onSeek` lift in `ReportView` → `SessionPlayback`.

### 16.10 Testing (manual-first, per D9)

- **Pure-logic unit tests** (no video) on `detectors.py`: baseline estimation, zone classification, reading-sweep / down-glance / off-screen detectors, band thresholds — fed synthetic angle/face-count streams.
- **`analyze_session_proctoring` idempotency** test; **mandatory cross-tenant RLS test** on `session_proctoring_analysis` (cross-tenant read → 0 rows).
- **Frontend:** panel render (mocked hook), band labelling present, jump-to-timestamp seek wiring.
- **Manual eval:** self-recorded sessions (look away, hold phone, second person, glasses, dark) → run the actor → eyeball the report. Bands ship **advisory** until thresholds are tuned on real recordings.

### 16.11 Build order

Backend-first: migration + table + RLS (+ register) → `GazeEstimator` + L2CS wrapper → `detectors.py` (TDD, pure) → `analysis.py` + actor + `vision-worker` service → enqueue trigger → read endpoint → `frontend/app` panel + seek wiring.
