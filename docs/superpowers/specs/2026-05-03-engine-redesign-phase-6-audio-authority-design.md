# Engine Redesign — Phase 6: Server-authoritative audio + e2e gate

**Status:** Draft for user review · **Date:** 2026-05-03 · **Phase:** 6 of 6 in the engine-redesign arc (terminal phase)

## Summary

Phase 6 enforces the **server-authoritative audio** invariant locked in
overview-spec Decision #6 across the candidate's entire signal chain, and
ships the **full-arc end-to-end manual checklist** that closes the
6-phase arc. After Phase 6:

- The candidate browser stops applying its built-in echo cancellation,
  noise suppression, and automatic gain control (EC/NS/AGC). Both
  `getUserMedia` (in `CameraMicStep`) and the LiveKit `Room`
  constructor (in `app.tsx`) carry the explicit `false / false / false`
  triplet.
- The engine's noise-cancellation defaults flip from `QUAIL_VF_L` /
  `0.7` to `QUAIL_S` / `0.4`. With browser-side EC/NS/AGC off,
  ai_coustics is the **single source of truth** for noise reduction;
  the gentler model + lower level preserves soft speech that the
  prior config was attenuating below the VAD activation threshold.
- The candidate wizard's noise-floor display recalibrates: with raw
  ambient audio reaching the dBFS sampler, the same physical room
  reads ~10 dBFS higher than pre-Phase-6. Thresholds shift up; new
  copy explains what the candidate is seeing.
- A `track.getSettings()` verification step lands in `CameraMicStep`
  to detect browsers that silently ignore the constraint object
  (notably mobile Safari). Divergence is structured-logged for
  operators; the session continues regardless (per the
  browser-divergence ship decision below).
- A new full-arc e2e checklist
  (`docs/onboarding/engine-redesign-full-arc-e2e.md`) supersedes the
  Phase-2-only checklist. Nine scenarios — including a fairness pair
  (soft-spoken + noisy-environment) — close the terminal acceptance
  gate for the whole arc.
- The threat model gets a Phase 6 section covering the three boundaries
  the audio change moves: bystander-PII exposure, ai_coustics
  availability dependency (no application-level fallback), and
  recording capture-point.

Behavior change in production interviews:

- Soft-spoken candidates whose voices were being attenuated below
  `Silero VAD`'s activation threshold should now register as
  `audio.user.state new_state=speaking` within the first sentence.
- The audit-log envelope's `model_versions` dict (already populated
  by `agent.py:191-202`) automatically records the new
  `noise_cancellation_model="QUAIL_S"` and
  `noise_cancellation_level=0.4` on every session — zero engine code
  change required for audit capture.
- Candidates in noisy environments lose the browser-side noise
  suppression they previously had as a safety net. Fairness exposure
  is verified end-to-end via the paired noisy-environment e2e
  scenario; ai_coustics at QUAIL_S / 0.4 is expected to hold.

This phase consumes 2 of the 21 decisions from the
[overview spec](2026-05-02-interview-engine-redesign-overview-design.md):

- **Decision #6** — server-authoritative audio: browser disables
  EC/NS/AGC; ai_coustics is single source of truth; tuning is
  `QUAIL_S` / `0.4`.
- **Decision #10** — audio-authority placement is Phase 6 (final
  phase, after the engine redesign is shipping).

## 1 — Decisions locked in this phase's brainstorm

| # | Open question (overview §12.6) | Decision |
|---|---|---|
| P6-Q1 | `audioCaptureDefaults` injection point in `app.tsx` | **Pre-construct a `Room` in `useMemo` with `audioCaptureDefaults`, pass it via `useSession(tokenSource, { room })`.** Confirmed by reading `useSession.ts` source (`livekit/components-js@main`): `useSession` accepts a `room: Room` field on its options object and explicitly skips its internal `new Room({})` path when a pre-constructed Room is supplied (via `roomFromContext ?? optionsRoom`). The Room's `audioCaptureDefaults` flow through `LocalParticipant.setMicrophoneEnabled(true, undefined, ...)` because the captureOptions slot is undefined → `mergeDefaultOptions` falls back to `roomOptions?.audioCaptureDefaults`. There is **no** `useSession` option that exposes `audioCaptureDefaults` directly; lifting Room construction completely out (manual `tokenSource.fetch()` + `room.connect()` + agent dispatch) would reimplement non-trivial useSession orchestration for zero gain. |
| P6-Q2 | Engine env defaults source-of-truth | **Change BOTH `app/config.py` Settings defaults AND `.env.example` documentation.** In pure dev state, the contract is "what does a fresh `docker compose up` produce" — devs without `.env` overrides fall through to `config.py` defaults. Documentation-only changes leave the effective default as the old values for any dev who hasn't manually updated their `.env`. The `app/ai/realtime.py::build_noise_cancellation` plumbing already reads from `AIConfig` and the engine's `EventCollector.model_versions` already captures the values into every session's audit envelope, so the env flip alone is sufficient — no engine code change needed. The `config.py` docstring also rewrites: drop "best WER" framing, add "best soft-speech preservation when browser-side EC/NS/AGC are off." |
| P6-Q3 | e2e checklist scope | **One full-arc consolidated checklist** (`docs/onboarding/engine-redesign-full-arc-e2e.md`) supersedes the Phase-2-only checklist. Nine scenarios mapping 1:1 to overview-spec §11's nine acceptance gates, organized by scenario rather than by phase. The Phase-2-only doc (`engine-redesign-phase-2-e2e.md`) explicitly anticipated this — it states "this checklist may be run in aggregate with the Phase 3-6 checklists" and "the row stays unchecked here until that aggregate run completes." The user runs the manual e2e ONCE after Phase 6 lands per the working agreement, so the consolidated form matches the workflow. The Phase-2-only doc is hard-deleted; git history preserves it. |
| P6-Q4 | Browser-divergence ship decision | **Ship the session anyway, structured-log the divergence.** Browsers (notably mobile Safari on iOS, sometimes mobile Chrome on Android) silently ignore `MediaTrackConstraints` flags like `echoCancellation: false`. After `getUserMedia` resolves, `CameraMicStep` calls `track.getSettings()`, compares to the requested constraints, and emits `cammic.constraints.diverged` with `{ requested, applied }` if they don't match. The candidate is **not** shown a warning (per C3 — they have no actionable knob). The session continues. Refusing to start would be worse UX than partial mitigation via ai_coustics. Residual risk: a candidate on an ignoring-browser still has stacked browser-NC + ai_coustics-NC, which may over-suppress soft speech. Operators monitor the divergence log; if a high false-rate is observed in production, a future phase can add per-browser handling. |
| P6-Q5 | Noise-floor display UX side effect | **Recalibrate thresholds AND update copy in the same PR as the constraint change.** The `sampleNoiseFloorDbfs` reading in `CameraMicStep` (line 98) reflects raw ambient audio post-Phase-6 (was post-EC/NS/AGC pre-Phase-6). The same physical room will read ~10 dBFS higher (worse-looking). Without recalibration, candidates see a user-visible regression on identical setups. Threshold pushed up by ~10 dBFS; explanatory copy added ("This measures your raw room noise. Anything below -25 dBFS is fine — our audio processing handles the rest.") The recalibration ships in the same commit as the verification log because they touch the same file and form a coherent UX change. |
| P6-Q6 | ai_coustics runtime fallback | **Document the gap in the threat model; do not add an application-level fallback.** Reading `app/ai/realtime.py::build_noise_cancellation` confirms there is no runtime fallback today — the function raises `ValueError` at boot for an unknown model name (worker exits, container restarts) but has no wrapper around mid-session plugin failure. Adding one would require wrapping the audio input pipeline, which is larger scope than Phase 6. The honest disclosure is in the threat model; future phase can add resilience if a real-world failure pattern emerges. |
| P6-Q7 | Recording capture-point | **LiveKit Cloud Insights recording (if enabled) captures post-ai_coustics audio**, per `https://docs.livekit.io/deploy/observability/insights/`: "If noise cancellation is enabled, user audio recording is collected after noise cancellation is applied. The recording reflects what the STT or realtime model receives." We do not use LiveKit Egress today (already documented in `docs/security/threat-model.md`'s Phase 3C.2 "out of scope" list). Phase 6's threat-model entry carries this as a known fact; future Egress wiring will need its own threat-model row. |

## 2 — Scope

### 2.1 In scope

| Surface | Change |
|---|---|
| `backend/nexus/app/config.py:303-304` | Defaults flip: `interview_noise_cancellation_model: str = "QUAIL_S"` and `interview_noise_cancellation_level: float \| None = 0.4`. Docstring rewrite: drop "best WER for agent pipelines per LiveKit's published numbers" framing, replace with "best soft-speech preservation when browser-side EC/NS/AGC are off; trades absolute WER for fewer false-silence cuts on quiet candidates." Cross-reference the threat-model Phase 6 section. |
| `backend/nexus/.env.example:128-129` | `INTERVIEW_NOISE_CANCELLATION_MODEL=QUAIL_S` and `INTERVIEW_NOISE_CANCELLATION_LEVEL=0.4`. Comment block rewritten to match the new docstring. |
| `frontend/session/app/interview/[token]/CameraMicStep.tsx` (constraint) | Line :88 `getUserMedia({ video: true, audio: true })` becomes `getUserMedia({ video: true, audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } })`. |
| `frontend/session/app/interview/[token]/CameraMicStep.tsx` (verification) | After `getUserMedia` resolves and the audio track is obtained: read `track.getSettings()`, compare each of `echoCancellation` / `noiseSuppression` / `autoGainControl` to the requested `false`, and if any diverged emit a structured log `cammic.constraints.diverged` with payload `{ requested: { ec: false, ns: false, agc: false }, applied: { ec, ns, agc } }`. **No candidate-facing warning** (per P6-Q4). The session proceeds normally. |
| `frontend/session/app/interview/[token]/CameraMicStep.tsx` (noise-floor recalibration + copy) | Push the "good / borderline / poor" threshold up by ~10 dBFS to reflect raw ambient. Add explanatory copy under the dBFS reading: "This measures your raw room noise. Anything below -25 dBFS is fine — our audio processing handles the rest." (Exact threshold values to be finalized during implementation; the rule is "match what a typical room reads now that EC/NS/AGC are off.") |
| `frontend/session/components/interview/app/app.tsx` | Add `const room = useMemo(() => new Room({ audioCaptureDefaults: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } }), [])` before the `useSession` call at line :87. Pass `{ room }` as the second argument: `const session = useSession(tokenSource, { room })`. The pre-constructed Room flows through `useSession`'s `roomFromContext ?? optionsRoom` path (see P6-Q1). |
| `frontend/session/tests/components/interview/app/app.test.tsx` (new or extended) | Vitest assertion: `useSession` invoked with options containing a `Room` whose `audioCaptureDefaults` has all three flags `false`. Mock `livekit-client` `Room` to inspect the constructor arg. |
| `frontend/session/tests/app/interview/[token]/CameraMicStep.test.tsx` (new or extended) | Three cases: (a) `getUserMedia` mock called with `audio` having all three flags `false`; (b) when the mocked track's `getSettings()` returns `{ echoCancellation: true }`, the structured-log path fires with the divergence payload AND the Continue button stays enabled; (c) noise-floor threshold recalibration: a -28 dBFS reading registers as "fine" (would have been "borderline" pre-Phase-6), -22 dBFS registers as "borderline", -15 dBFS registers as "poor." |
| `docs/security/threat-model.md` (new section) | Append "Phase 6 — Server-authoritative audio (2026-05-03)" with three trust-boundary rows + ai_coustics availability gap + recording capture-point + bandwidth/CPU operational note + browser-divergence decision. See §6 for the full content. |
| `docs/onboarding/engine-redesign-full-arc-e2e.md` (new) | Nine-scenario consolidated checklist; full content in §7. |
| `docs/onboarding/engine-redesign-phase-2-e2e.md` | **Hard delete.** Git history preserves it. The new full-arc doc supersedes. |
| `docs/security/prompt-fairness-signoffs.md` | Append one-line entry: "Phase 6 (2026-05-03) audio tuning: not a prompt change; fairness validation deferred to `engine-redesign-full-arc-e2e.md` scenarios 9a + 9b (soft-spoken + noisy-environment paired)." |
| `docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md` | Phase status index row for Phase 6: `⚪ not started → ✅ shipped`; link the new spec + plan filenames. **§11 acceptance gate #6** wording update: currently says "Soft-spoken candidate at default mic level…"; expand to reference scenarios 9a + 9b paired (soft-spoken + noisy-environment) per the fairness coverage rule. |
| `CLAUDE.md` (root) | Add a one-line "Audio invariant" rule under "Hard Rules": "**Audio invariant:** Browser-side EC/NS/AGC are OFF on the candidate surface; ai_coustics is the sole noise filter. See `docs/security/threat-model.md` Phase 6 section." |
| `backend/nexus/CLAUDE.md` | Add a `Phase 3D.engine-redesign-6` status block (~12 lines) following Phase 5's precedent. Note: defaults flipped (`QUAIL_S` / `0.4`), ai_coustics is now the sole noise filter, no application-level runtime fallback, recording capture-point documented in threat-model. **Migration list unchanged** (Phase 6 adds none — head still `0027_tenant_settings`). |
| `frontend/session/CLAUDE.md` | Add new subsection "Audio handling — server-authoritative invariant" (~10 lines): browser EC/NS/AGC OFF, ai_coustics is the single noise filter, `audioCaptureDefaults` flows via the pre-constructed Room passed into `useSession`'s `{ room }` option. Note the noise-floor display reflects raw ambient (recalibrated thresholds + new copy) and the `track.getSettings()` divergence-log path. |

### 2.2 Out of scope

- **No engine code change** beyond config defaults. The env tuning auto-flows
  through `app/ai/realtime.py::build_noise_cancellation` which already
  reads from `AIConfig`. The audit-log envelope's `model_versions` capture
  is already wired (`agent.py:191-202`).
- **No new prompt files**, so no senior-reviewer fairness sign-off (per
  Decision #18 — only prompt changes touch fairness sign-off; the
  `prompt-fairness-signoffs.md` one-line entry is an audit record, not a
  sign-off gate).
- **No DB migration.** Head stays `0027_tenant_settings`.
- **No new Python modules** (so no `tests/test_module_boundaries.py` work).
- **No new frontend npm packages.** `audioCaptureDefaults` is a
  `livekit-client` feature already in the dep tree (pinned `^2.18.8`).
- **No `OutcomeWatcher` / `useSessionOutcome` / `DisconnectError`
  changes.** Phase 5's surface stays stable.
- **No mid-session device-switch or device-picker UI.** Pre-existing gap
  in CameraMicStep; out of Phase 6's scope.
- **No application-level runtime fallback for ai_coustics plugin failure.**
  Documented as a known gap in the threat model (per P6-Q6); future
  phase if a real-world failure pattern emerges.
- **No candidate-facing warning when `track.getSettings()` diverges.**
  Per P6-Q4: candidate has no actionable knob; structured log only.
- **LiveKit Egress / S3 recording wiring.** Future phase.

## 3 — Architecture

The audio path's *shape* doesn't change. Phase 6 only changes WHO does
noise processing (browser → ai_coustics) and HOW aggressively (0.7 → 0.4):

```
Candidate browser
  ├─ navigator.mediaDevices.getUserMedia({
  │      video: true,
  │      audio: { echoCancellation: false,
  │               noiseSuppression: false,
  │               autoGainControl: false }
  │   })                                                          [Phase 6]
  ├─ track.getSettings() → cammic.constraints.diverged log
  │  if any of EC/NS/AGC was silently re-enabled by the browser   [Phase 6]
  └─ noise-floor sample → recalibrated thresholds + new UX copy   [Phase 6]

  └─ LiveKit Room (client-side, constructed in app.tsx)
       new Room({
         audioCaptureDefaults: {
           echoCancellation: false,
           noiseSuppression: false,
           autoGainControl: false,                                [Phase 6]
         }
       })

       └─ useSession(tokenSource, { room })                      [Phase 6]
            └─ session.start() → room.localParticipant
                 .setMicrophoneEnabled(true, undefined, ...)
                 (undefined → mergeDefaultOptions falls back
                 to room.audioCaptureDefaults — set above)

LiveKit room (server-side, post-WebRTC)
  └─ Engine worker subscribes to candidate audio track
       └─ ai_coustics QUAIL_S, level=0.4 applied                  [Phase 6 env]
            (env-driven via AIConfig; agent.py event-log
            collector captures model+level into model_versions
            on every session — already wired)

       └─ Silero VAD → multilingual turn detector → Deepgram STT
       └─ Recording (LiveKit Cloud Insights, if enabled):
          captures post-ai_coustics audio (= what STT sees)
```

## 4 — Frontend `audioCaptureDefaults` injection (load-bearing detail)

The injection point requires care because `useSession` constructs a `Room`
internally by default. Reading `useSession.ts` source
(`livekit/components-js`):

```ts
const room = React.useMemo(() => {
  const preGeneratedRoom = roomFromContext ?? optionsRoom;
  if (preGeneratedRoom) {
    return preGeneratedRoom;
  }
  // ... internal Room construction; only ever sets `encryption` on RoomOptions ...
  const room = new Room(roomOptions);
  ...
}, [roomFromContext, optionsRoom, ...]);
```

When a pre-constructed Room is supplied via `options.room`, the hook
skips its internal `new Room({})` path entirely. `useSession`'s internal
construction NEVER exposes `audioCaptureDefaults` as a hook-level option;
the only audio-related path is `setMicrophoneEnabled(true, undefined, ...)`
inside `start()`, which falls back to `roomOptions?.audioCaptureDefaults`
because `mergeDefaultOptions` is called with `undefined` capture options.

The Phase 6 change is therefore:

```tsx
// frontend/session/components/interview/app/app.tsx
import { Room, RoomEvent, TokenSource } from 'livekit-client'

export function App({ appConfig, token, preCheck, mode }: Props) {
  // ... existing state ...

  const room = useMemo(
    () =>
      new Room({
        audioCaptureDefaults: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      }),
    [],
  )

  const tokenSource = useMemo(/* ... existing TokenSource.custom ... */, [...])

  const session = useSession(tokenSource, { room })  // <-- { room } added

  // ... rest unchanged: AgentSessionProvider, OutcomeWatcher, ViewController ...
}
```

`useSession`'s internal `room.disconnect()` cleanup in `useEffect` handles
unmount. React 19 Strict Mode invokes the `useMemo` factory twice on first
mount in dev only; the first instance is GC'd un-connected (it's never the
return value of `useMemo`'s second invocation). Production builds invoke
the factory once. No application-level mitigation needed.

## 5 — Frontend `CameraMicStep` changes

Three coupled changes ship in the same commit because they touch the same
file and form one coherent UX update.

### 5.1 Constraint object on `getUserMedia`

```tsx
// Before
const stream = await navigator.mediaDevices.getUserMedia({
  video: true,
  audio: true,
})

// After
const stream = await navigator.mediaDevices.getUserMedia({
  video: true,
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
  },
})
```

`video: true` stays. The `audio` field switches from the bare boolean
shorthand (which inherits browser defaults — EC/NS/AGC ON in every major
browser) to the explicit constraint object disabling all three.

### 5.2 `track.getSettings()` verification

After `getUserMedia` resolves, before the noise-floor sample:

```tsx
const audioTrack = stream.getAudioTracks()[0]
if (audioTrack) {
  const applied = audioTrack.getSettings()
  const requested = { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
  const diverged =
    applied.echoCancellation !== false ||
    applied.noiseSuppression !== false ||
    applied.autoGainControl !== false
  if (diverged) {
    // structured log only — no candidate-facing warning per P6-Q4
    console.warn('cammic.constraints.diverged', { requested, applied })
  }
}
```

(`console.warn` is the placeholder; the actual logger may be a structured
logger if the candidate surface adopts one in a future PR. Today the
candidate surface has no third-party logging per `frontend/session/CLAUDE.md`
"No analytics on the candidate surface" — `console.warn` is the only
operator-visible channel until Sentry is wired, and Sentry's `beforeSend`
must scrub `/interview/[^/]+` paths per the same file.)

### 5.3 Noise-floor display recalibration + copy

The `sampleNoiseFloorDbfs(stream)` call (line :98) returns a dBFS reading
of raw ambient audio post-Phase-6 (was post-EC/NS/AGC pre-Phase-6). The
same physical room reads ~10-15 dBFS higher (closer to 0 dBFS = "louder
noise floor").

The display threshold and copy update:

| Reading (dBFS) | Pre-Phase-6 label | Post-Phase-6 label |
|---|---|---|
| < -50 | excellent | (unreachable in practice) |
| -50 to -40 | good | excellent |
| -40 to -30 | borderline | good |
| -30 to -20 | poor | borderline |
| > -20 | very poor | poor |

(Exact values to finalize during implementation; the rule is "match what a
typical quiet room reads now that EC/NS/AGC are off.") Add explanatory
copy under the dBFS reading: "This measures your raw room noise. Anything
below -25 dBFS is fine — our audio processing handles the rest."

The Continue button continues to NOT block on noise-floor (per the
existing `// Sample is best-effort: failure here must not block Continue`
contract at line :97).

## 6 — Threat-model update

A new section is appended to `docs/security/threat-model.md`:

> ## Phase 6 — Server-authoritative audio (2026-05-03)
>
> Trust boundaries that change when browser-side EC/NS/AGC switch from
> ON to OFF and ai_coustics becomes the sole noise filter for candidate
> audio. Configuration tuning: `INTERVIEW_NOISE_CANCELLATION_MODEL=QUAIL_S`,
> `INTERVIEW_NOISE_CANCELLATION_LEVEL=0.4`.
>
> | Boundary | Element | STRIDE | Mitigation |
> |---|---|---|---|
> | Browser mic → LiveKit room | Raw audio (no browser-side EC/NS/AGC) carries any sound the mic captures, including ambient conversations near the candidate (open-plan offices, family members in the next room). | I (info disclosure of bystander PII) | Pre-session consent text already states audio is recorded; reviewers SHOULD verify the consent copy reasonably covers third-party voices for the candidate's locale. ai_coustics QUAIL_S at level 0.4 suppresses non-target voices but is gentler than QUAIL_VF_L; the e2e checklist's noisy-environment scenario verifies the suppression holds in practice. STT transcripts of bystander speech, if produced, fall under the existing event-log redaction policy (`metadata` mode strips transcript content). |
> | ai_coustics plugin → audio path | Single source of truth for noise reduction. **No application-level runtime fallback.** Boot-time misconfigured model name → `ValueError` → worker exits → container restarts → LiveKit `AGENT_DISPATCH_FAILED`. Mid-session plugin failure → undefined application behavior (depends on plugin internals). | A (availability dependency) | Mitigation is LiveKit Cloud-managed plugin reliability. Documented as a known gap; future phase can wrap the audio input pipeline with a fallback if a real-world failure pattern emerges. The audit envelope's `model_versions` dict captures the model+level on every session, providing forensic trace if a session reports degraded quality. |
> | Engine → recording (LiveKit Cloud Insights, if enabled) | Recording captures post-ai_coustics audio per `https://docs.livekit.io/deploy/observability/insights/` ("If noise cancellation is enabled, user audio recording is collected after noise cancellation is applied. The recording reflects what the STT or realtime model receives.") | I (information disclosure via recording) | Insights recording is OFF by default; enabling it is a deliberate operator choice already covered by existing consent gating + S3 recording-bucket policy in root CLAUDE.md ("S3: versioning ON for the recording bucket. MFA-delete ON for the recording bucket."). Future LiveKit Egress wiring will need its own threat-model row when added. |
>
> ### Browser-divergence decision (residual risk accepted)
>
> Browsers (notably mobile Safari on iOS, sometimes mobile Chrome on
> Android) silently ignore `MediaTrackConstraints` flags such as
> `echoCancellation: false`. After `getUserMedia` resolves, `CameraMicStep`
> reads `track.getSettings()` and emits `cammic.constraints.diverged` if
> any flag was silently re-enabled. **The session continues regardless.**
> Refusing to start would be worse UX than partial mitigation via
> ai_coustics. Residual risk: a candidate on an ignoring-browser has
> stacked browser-NC + ai_coustics-NC, which may over-suppress soft
> speech. Operators monitor the divergence log; if a high false-rate is
> observed, a future phase can add per-browser handling.
>
> ### Operational performance note
>
> Raw audio uplink may be marginally larger (more entropy in the Opus
> payload); browser CPU may be marginally lower (no DSP). Net effect on
> low-bandwidth or low-CPU candidate devices is expected to be invisible;
> measurement is deferred to a future analytics phase.
>
> ### When this section needs updating
>
> - LiveKit Egress is wired into the engine (would require its own
>   capture-point row).
> - The ai_coustics model is changed in production (the threat surface
>   changes per-model; e.g., a switch to `QUAIL_BV` would change the
>   broadband-voice-suppression characteristic).
> - A real-world incident demonstrates a mid-session plugin failure path
>   needing application-level handling.
> - The candidate surface adopts a structured logger that ships
>   `cammic.constraints.diverged` to a third-party sink (would change the
>   PII posture of the divergence record).

## 7 — Full-arc e2e checklist

A new file `docs/onboarding/engine-redesign-full-arc-e2e.md` consolidates
the entire arc's manual acceptance gate. The Phase-2-only checklist
(`docs/onboarding/engine-redesign-phase-2-e2e.md`) is hard-deleted in the
same commit (per P6-Q3); git history preserves it.

### Structure

1. **Stack overview** (Docker services, Supabase, frontend dev servers)
   — taken verbatim from the Phase-2-only doc.
2. **Bringup** — same as the Phase-2-only doc; verifies engine worker
   registers and the candidate invite arrives in Inbucket.
3. **Acceptance scenarios** — nine scenarios mapping 1:1 to overview
   spec §11's nine acceptance gates:
   1. **Clean interview** (greet + 6Q + close + audit-log clean +
      `model_versions.noise_cancellation_model=QUAIL_S`).
   2. **Q3 compliance binary completes < 60s** (per-kind hard cap).
   3. **Q0/Q1 spoken forms < 25 words, no verbatim reading**.
   4. **Q2 STAR-shape probe behavior** (probe fires only when missing
      a STAR component).
   5. **Probe count ≤ per-kind cap** on every question. Re-run
      Phase 5's idle-silence scenario under Phase 6 audio conditions:
      verify the idle-nudge still fires at ~30s (VAD now sees more raw
      audio events; this could mask the away state).
   6. **Candidate-end intent** ("I'd like to end") shuts down within 5s,
      `session_outcome=candidate_ended` published.
   7. **Jailbreak refusal**, no rubric leak across the four standard
      jailbreak prompts.
   8. **Signal-disclaim bridge** (Q0 disclaim → Q1 skip with brief bridge).
   9. **Audio fix verification — fairness pair**:
      - **9a Soft-spoken**: Operator sits 3 ft from mic in a quiet room.
        Speaks "I worked on a small Python script last summer" at
        conversational quiet level (similar to whispering across a desk
        to a coworker). **Pass** = `audio.user.state new_state=speaking`
        event fires within 1s of utterance start AND STT-final transcript
        matches within 1-word edit distance. **Fail** = no `speaking`
        event for >2s, OR transcript missing >2 content words.
      - **9b Noisy-environment**: Operator runs HVAC + types on a
        keyboard in the background. Speaks at normal voice volume.
        **Pass** = STT word-error rate doesn't visibly degrade vs. a
        quiet-room baseline; ai_coustics still produces a usable
        transcript. **Fail** = >30% of words mis-transcribed or
        replaced with bystander/keyboard noise tokens.

   Plus a **constraint-verification check** in the bringup section:
   operator opens devtools, confirms either `cammic.constraints.diverged`
   is absent (constraints honored — desktop Chrome, desktop Safari path)
   OR that the divergence is logged and matches the per-browser matrix
   row (mobile Safari on iOS may diverge per P6-Q4).

   Plus a **per-browser matrix row** for the constraint check:
   desktop Chrome, desktop Safari, mobile Chrome on Android, mobile
   Safari on iOS. Document the actual measured `track.getSettings()`
   outcome per browser. **Do not block on divergence** (per P6-Q4).
4. **Knockout flow** — Q3 fail emits `KnockoutFailure` row to
   `sessions.knockout_failures`; `record_only` policy keeps the
   interview running.
5. **Event log replay** — single envelope JSON parses cleanly into
   `EventLogEnvelope`; zero PII in `metadata` mode; `model_versions`
   shows the new noise-cancellation values.
6. **Recording verification** (if Insights recording is enabled in the
   LiveKit Cloud project): confirm the recording reflects post-ai_coustics
   audio (audible noise reduction vs. the raw browser feed), matching
   what STT received per P6-Q7.
7. **Sign-off table** — all nine scenarios + bringup + per-browser
   matrix rows. Signed by operator + date.

### Why one consolidated doc

- The user runs the manual e2e ONCE after Phase 6 lands per the working
  agreement. Five separate per-phase checklists is the wrong shape for
  a single end-to-end pass.
- Overview spec §11 is the contract: nine acceptance gates closing the
  whole arc. A consolidated checklist mirrors that contract one-to-one.
- Drift risk between separate per-phase docs is real; the Phase-2-only
  doc explicitly anticipated being subsumed.
- Audit story: "did the full arc work end-to-end before we shipped real
  candidate sessions?" → one signed file, not "go read five files."

## 8 — Test gates

| Test | File | What it asserts |
|---|---|---|
| `getUserMedia` constraint | `frontend/session/tests/app/interview/[token]/CameraMicStep.test.tsx` | `getUserMedia` mock called with `audio` having all three flags `false`. |
| `track.getSettings()` divergence log | same file | When the mocked track returns `{ echoCancellation: true }`, the divergence-log path fires with the `{ requested, applied }` payload. Continue button stays enabled (does not block). |
| Noise-floor recalibration | same file | A simulated -28 dBFS reading registers as "fine"; -22 dBFS registers as "borderline"; -15 dBFS registers as "poor". Threshold pushed up by ~10 dBFS vs. current. |
| Room with `audioCaptureDefaults` | `frontend/session/tests/components/interview/app/app.test.tsx` | `useSession` invoked with options containing a `Room` instance whose `audioCaptureDefaults` has all three flags `false`. (Mock `livekit-client` `Room` to inspect the constructor arg.) |

**Coverage target.** `app.tsx` and `CameraMicStep.tsx` keep their current
per-file coverage targets — the candidate-session 100% branch gate from
`frontend/session/CLAUDE.md` applies to `lib/api/candidate-session.ts`,
`lib/env.ts`, and `next.config.ts headers()`, NOT to `app.tsx` or
`CameraMicStep`. Phase 6's added branches (the `track.getSettings()`
divergence path) need coverage in the Vitest cases above; that's the
whole gate.

**Test failure tolerance.** Per the user's prompt: "the interview_engine,
frontend session, and any new test files stay green and grow." The new
Vitest cases land green; pre-existing failures on main (auth_login,
auth_service, etc.) stay out of scope. `tests/test_module_boundaries.py`
stays green (Phase 6 adds no Python module).

**No new backend tests required.** Phase 6's backend change is two env
defaults; no behavioral surface added.

## 9 — Build sequencing

Per the working agreement: stay on `main`, per-task commits, update
overview spec status in the same commit that ships the phase artifact.

| Order | Task | Commit shape |
|---|---|---|
| 1 | T1 — Engine env+config flip (`config.py` defaults + `.env.example` + docstring rewrite). | `feat(engine): server-authoritative audio defaults — QUAIL_S / 0.4 (Phase 6)` |
| 2 | T3 — `CameraMicStep.tsx` constraint object on `getUserMedia`. | `feat(session): disable browser EC/NS/AGC in cam/mic step (Phase 6)` |
| 3 | T4 — `CameraMicStep.tsx` `track.getSettings()` divergence log + noise-floor threshold recalibration + UX copy. | `feat(session): cam/mic constraint verification + noise-floor recalibration (Phase 6)` |
| 4 | T5 — `app.tsx` pre-constructed Room + `useSession({ room })`. | `feat(session): pre-construct LiveKit Room with audioCaptureDefaults (Phase 6)` |
| 5 | T6 — Vitest tests for the four frontend changes. | `test(session): cover Phase 6 audio-authority changes` |
| 6 | T7 — `docs/security/threat-model.md` Phase 6 section. | `docs(security): Phase 6 threat-model entry — server-authoritative audio` |
| 7 | T8 — `docs/onboarding/engine-redesign-full-arc-e2e.md` (new); delete `engine-redesign-phase-2-e2e.md`. | `docs(onboarding): full-arc e2e checklist supersedes Phase 2 (Phase 6)` |
| 8 | T9 — Docs flip: overview spec status + §11 acceptance gate #6 wording, root CLAUDE.md "Audio invariant" rule, `backend/nexus/CLAUDE.md` Phase 3D.engine-redesign-6 status block, `frontend/session/CLAUDE.md` audio-handling subsection, `docs/security/prompt-fairness-signoffs.md` one-line entry. | `docs(engine): flip Phase 6 to shipped + CLAUDE.md updates (Phase 6)` |

T1 → T3 → T4 → T5 → T6 → T7 → T8 → T9 is the natural dependency order
(T2 from the original plan dissolved into T7 per the spec self-review).
T6 lands AFTER T3-T5 so tests target real implementation, not stubs.
T9 (overview spec status flip) MUST be the last commit — it's the
"Phase 6 ✅" claim that closes the arc.

T7 + T8 may run in parallel as docs work if helpful, but commit ordering
still matters: T9 last.

## 10 — Migration safety / rollback

There is no schema migration. The rollback shape is plain
`git revert <task-sha>`:

- T1 revert → engine boots with old defaults (`QUAIL_VF_L` / `0.7`);
  other Phase 6 changes still in place. **Audio chain mismatched** but
  not broken (browser EC/NS/AGC OFF, ai_coustics now stronger than
  intended). Risk: over-suppression of soft speech.
- T3-T5 reverts → frontend reverts to bare `audio: true` and stock
  `useSession`; backend keeps Phase 6 defaults. **Inverse asymmetry**:
  backend tuned for raw audio, but browser EC/NS/AGC are back on, so
  ai_coustics has less to work with. Risk: under-suppression in noisy
  environments.
- T6 revert → tests removed; no behavior change.
- T7-T9 reverts → docs revert; no behavior change.

**Rollback rule (carried in the spec):** Phase 6's engine env tuning (T1)
and frontend changes (T3-T5) are coupled. Rollback in dev should always be
all-or-nothing. Reverting a single task creates a chain mismatch.

## 11 — Human review gates

Per `frontend/session/CLAUDE.md` "Human Review Required For":

- **Any change to the camera/mic step flow** — fires for T3 + T4. The
  commit message body (the dev-state arc develops on `main` rather than
  via PR review) MUST:
  1. Call out the constraint change (`audio: true` → constraint object).
  2. Reference the threat-model entry
     (`docs/security/threat-model.md` Phase 6 section).
  3. Note the noise-floor display recalibration is a deliberate
     user-visible change with new copy.

Per `backend/nexus/CLAUDE.md` "Human Review Required For":

- **Candidate scoring or classification thresholds** — does NOT fire.
  Audio tuning affects signal quality but does not change scoring or
  classification logic. The `prompt-fairness-signoffs.md` one-line
  entry is the audit-discoverable record that we considered fairness
  and concluded the e2e is the right validation surface (scenarios
  9a + 9b paired).

Per root `CLAUDE.md` "Human Review Required For":

- **Auth, RLS, candidate scoring, session state machine, billing** —
  none fire. Phase 6 doesn't touch any of these surfaces.

## 12 — Acceptance gates

Phase 6 is "done" when:

1. T1-T9 all committed on `main` in the order above.
2. New Vitest cases under T6 pass green.
3. `tests/test_module_boundaries.py` still green (no regression — Phase 6
   adds no Python module).
4. `pytest tests/interview_engine` still green (Phase 6 doesn't touch
   engine code beyond config defaults).
5. The full-arc e2e checklist
   (`docs/onboarding/engine-redesign-full-arc-e2e.md`) is run end-to-end
   at least once with all nine scenarios + the per-browser matrix
   completed; sign-off table populated.
6. Overview spec Phase status index shows Phase 6 as ✅ shipped.
7. The threat-model entry is human-reviewed (per the camera/mic-flow
   gate above).

The terminal-acceptance check (#5) IS the gate that closes the entire
6-phase arc. Per the working agreement: "the e2e checklist itself is the
terminal acceptance gate for the whole arc."

## 13 — Open questions

None. All seven open questions reserved for the Phase 6 brainstorm
(`overview-design.md` §12.6) are resolved in §1's decision table:

- §12.6 Q1 (browser compat matrix) → resolved by §7's per-browser matrix
  rows + P6-Q4's browser-divergence ship decision.
- §12.6 Q2 (LiveKit `Room` construction site) → resolved by P6-Q1.
- §12.6 Q3 (threat-model entry for `frontend/session/CLAUDE.md`) →
  resolved by §6 (full threat-model entry) + the `frontend/session/CLAUDE.md`
  audio-handling subsection in §2.1.
- §12.6 Q4 (e2e checklist format) → resolved by §7 (full structure +
  scenarios).
- Implicit additional questions surfaced during the brainstorm (engine
  env source-of-truth, noise-floor display UX, ai_coustics fallback,
  recording capture-point) → P6-Q2, Q5, Q6, Q7 respectively.
