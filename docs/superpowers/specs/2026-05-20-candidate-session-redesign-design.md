# Candidate Session UI/UX Redesign — "Cinematic Glass"

**Date:** 2026-05-20
**Status:** Design — pending implementation plan
**Owner:** ProjectX team
**Surface:** `frontend/session` (candidate interview surface, port 3002)
**Related:** `2026-05-01-frontend-session-extract-design.md` (the surface this redesigns), `2026-05-17-interview-engine-v2-design.md` (the agent that drives it)

---

## ⚠️ Revision 2026-05-20b — Light glassmorphism (supersedes the dark direction)

The "dark cinematic glass" + bespoke "Liquid aurora" direction below was built and merged, then **reversed after live review**: the dark blue-teal palette read as "generic AI" and felt intimidating to candidates, and the user prefers LiveKit's stock visualizer over the custom one. The redesign is being **re-skinned** — **layout, components, wiring, voice-only/no-toggle invariant, End-interview wiring, the minimized "Interview Session" panel, the split two-pane wizard, terminal screens, per-tenant accent, frontend-only scope, and all constraints below are UNCHANGED.** Only the visual skin + the visualizer change. Validated with the user via a new mockup (cool-light palette picked over warm-light).

**What changes (everything else in this doc still applies):**

| Aspect | Was (dark) | Now (Revision b) |
|---|---|---|
| Theme | `dark-cinematic` (near-black + teal glow) | **`cool-light`** — soft white/lavender; light text; prominent **frosted-white glass** (stronger blur, visible edges + inner highlight) |
| Background | static radial backdrop | **animated ambient background** — large soft-blurred color blobs drifting slowly behind the glass; **frozen under `prefers-reduced-motion`** |
| Hero visualizer | bespoke CSS `LiquidAura` | **LiveKit stock `AgentAudioVisualizerAura`** (WebGL shader, restored from git: also `react-shader-toy.tsx` + `hooks/agents-ui/use-agent-audio-visualizer-aura.ts`) with **`colorShift: 2`**, `themeMode="light"`, `color` from `app-config` (default left multi-hue/cyan). |
| Small avatar mark (panel header) | `LiquidAura size="mark"` | a lightweight **CSS multi-hue gradient circle** — NOT a second WebGL canvas (perf). Only the hero renders the stock shader; at most one shader on screen at a time. |
| Glass prominence | restrained | **more prominent** — `rgba(255,255,255,~.45)` + `blur(22px) saturate` + visible white border + inner top highlight + soft warm/cool shadow. |

**Implementation deltas (vs. the merged dark build):** rewrite the theme block to `cool-light` light tokens + beef up `.px-glass*`; add a reduced-motion-safe `AnimatedBackground` component mounted behind the live surface and the wizard frame; restore the three stock-aura files from git history (deleted in commit `85bebdc`); replace `LiquidAura` usage in `AuraStage`, `WelcomeView`, and `WizardFrame` with the stock aura (hero sizes) and the CSS gradient mark in `InterviewSessionPanel`; set `audioVisualizerColorShift: 2` (+ a chosen `color`) in `app-config`; switch `layout.tsx` to the light theme attribute and pass `themeMode="light"`; **delete `LiquidAura` + its test**; update the aura/theme tests accordingly. The stock shader must degrade gracefully under `prefers-reduced-motion` (static frame / paused) and remain lazy-loaded so the pre-join bundle stays light.

The sections below describe the original dark direction; read them as the layout/behavior contract with the visual skin replaced per the table above.

---

## Summary

Complete visual + UX redesign of the candidate-facing interview surface. The current surface uses a warm-light "px" editorial theme; this replaces the candidate experience with a **dark cinematic glass** language built around a single hero element: a **bespoke "Liquid aurora" audio-reactive visualizer** that represents the AI interviewer's presence.

The redesign covers two surfaces:

1. **Pre-join wizard** — split two-pane layout (reassurance on the left, task on the right) across Welcome → Consent → Verify (OTP) → Camera & mic → Start.
2. **Live interview** — aura as the centered hero, candidate as a small self-view, a floating **"Interview Session"** glass transcript panel (minimized by default), a quiet progress/timer chip, the current spoken line as a caption, and a single control: **End interview**.

**This is a frontend-only change.** No backend endpoints, schema, auth, or engine changes. The UI consumes data the engine already publishes: the LiveKit room attributes `current_question_index`, `total_questions`, `time_remaining_seconds`, `session_outcome`, and the LiveKit-native agent state (`listening`/`thinking`/`speaking`) + audio track + transcriptions.

### Product goals (why)

- 📉 Reduced drop-offs & churn
- 📈 Higher completion rate
- ❤️ Better candidate experience → retention
- 😌 Reduced candidate stress — a calm, conversational, cinematic environment

Each design decision below is justified against these goals.

---

## Design decisions (validated with the user via visual mockups)

| Decision | Choice | Why |
|---|---|---|
| Overall mood | **Dark cinematic** (cool near-black, teal glow) | A dimmed, focused environment lowers interview anxiety and lets the aura be the hero; a bright editorial surface fights both. |
| Hero element | **Bespoke "Liquid aurora"** audio-reactive visualizer | The AI is voice-only and has no face — the aura *is* its presence. Custom-built (not the stock shader) for a unique, premium, soothing feel. |
| Transcript | **"Interview Session" floating glass panel**, **minimized by default**, expand on tap | Calm/low-clutter by default; full said/heard history one tap away for reassurance. Matches the user's reference image styling. |
| Voice interaction | **Voice-only — no keyboard chat input** | The product is a spoken screen; a text box invites the wrong behavior and adds clutter. |
| Mic & camera | **Always on, no toggle** | Proctoring + presence requirement; removing toggles also removes a class of "am I muted?" anxiety and support tickets. |
| Only control | **End interview** (with confirmation modal) | Minimal surface = calm. Everything else is automatic. |
| Pre-join | **Split two-pane** (aura + reassuring copy ‖ task card) | The left pane does real anti-anxiety work at the highest-drop-off moment; collapses to single column on mobile. |
| Branding | **Per-tenant accent + logo via `app-config.ts`** (default teal `#0E6F63`) | Already plumbed; cheap. A tenant-side theme-selection config page is future work, not in this scope. |

---

## Architecture overview

No new app, no new dependencies. We work within the existing `frontend/session` stack: Next.js 16, React 19, Tailwind v4, `livekit-client` 2.18.8, `@livekit/components-react` 2.9.20, `motion` 12, TanStack Query 5, `zod` 4. LiveKit stays **lazy-loaded** via `next/dynamic` so the pre-join bundle stays light.

The redesign is layered into the existing structure:

- **Theme:** a new `data-px-theme="dark-cinematic"` block added to `app/globals.css` (additive — the warm-light tokens remain for any shared `px/*` primitives). The session root layout sets this theme.
- **Pre-join:** the existing wizard (`app/interview/[token]/*`) is restyled into the split two-pane frame; a Welcome step is added as the entry.
- **Live session:** the `components/agents-ui/blocks/agent-session-view-01` tile layout + `agent-control-bar` are replaced by a purpose-built session view under `components/interview/session/`. LiveKit primitives, hooks, providers, and the API layer are reused unchanged.
- **Aura:** the stock `agent-audio-visualizer-aura.tsx` (GLSL ReactShaderToy) is **not** used; a new `LiquidAura` component is built.

### Data the UI binds to (all existing)

| Source | Mechanism | Drives |
|---|---|---|
| `current_question_index`, `total_questions` | agent participant attributes (`useParticipantAttributes` on the agent participant) | Progress chip "Question X of N" |
| `time_remaining_seconds` | agent participant attribute | Countdown timer |
| `session_outcome` | agent participant attribute (set at close) | Routing to completion/error screen |
| Agent state `listening`/`thinking`/`speaking` | `useVoiceAssistant()` / `agent_state_changed` | Aura state + "Listening…/Thinking…/Speaking…" label |
| Agent audio track | `useVoiceAssistant().audioTrack` + multiband volume | Aura amplitude reactivity |
| Transcriptions (agent said + candidate heard) | LiveKit transcription stream (existing `agent-chat-transcript` data path) | "Interview Session" panel + spoken caption |
| `audio_processing_hints` (from `/start`) | `room.options.audioCaptureDefaults` | `noiseSuppression:false`, `echoCancellation:true`, `autoGainControl:true` |

> **End-interview outcome wiring (existing path only — no backend change).** Investigated 2026-05-20: a button press cannot produce `candidate_ended` today — that outcome is only ever set when the Judge decides to end on a candidate utterance (`end_session` / `candidate_initiated`, `state/engine.py`). The agent registers **no RPC methods, no data-message handler, and no HTTP end endpoint**. So the End button uses the only mechanism that exists:
>
> 1. On confirm, the UI disconnects the room (`session.end()` → `room.disconnect()`).
> 2. **The existing `OutcomeWatcher` already routes this correctly.** In `components/interview/app/app.tsx:240–267`, when no engine outcome has been published (`lastOutcome === null`), the `RoomEvent.Disconnected` handler maps `DisconnectReason.CLIENT_INITIATED` (proto value `1`) → `onCompleted()` → the Completion screen. **No new flag or state is needed** — the redesign preserves this branch and adds a regression test that locks it in.
> 3. Server-side, the engine's `participant_disconnected` handler (`agent.py`) records `session_outcome = candidate_disconnected` **and** `record_session_result` persists the full `SessionResult` (transcript, questions asked/skipped, probes) independently — so the recruiter record stays complete and correct.
>
> Net: the candidate sees a graceful completion (via the existing CLIENT_INITIATED branch), the recorded label is `candidate_disconnected` (not `candidate_ended`), and the server-side result is fully persisted. A true `candidate_ended` label would require a small engine-side end-signal handler — explicitly **out of scope** (see below).

---

## Visual language & theme tokens

A restrained glass system — glass as *accents* over a cinematic backdrop, never glass-on-everything (protects text legibility and low-end mobile GPU).

**New `dark-cinematic` tokens (added to `globals.css`):**

- **Backdrop:** layered radial gradient, deep near-black (`#07090d` → `#0d1117` → `#1a2330`) with a soft accent glow centered behind the aura.
- **Glass surface:** `background: rgba(14,18,24,.55)`, `border: 1px solid rgba(255,255,255,.10)`, `backdrop-filter: blur(16–20px)`, soft drop shadow. One reusable `.px-glass` utility + a couple of size variants.
- **Accent:** tenant accent (default teal `#0E6F63` / soft `#4FA99C` / bright `#7FE6D6` for the aura highlight). Drives aura, Live pill, active stepper, primary CTA.
- **Type:** **Fraunces** (italic serif) for titles + the AI's spoken lines (matches the reference's "Interview Session" title and question emphasis); **Inter** for UI; **JetBrains Mono** for the timer + OTP code.
- **Semantic:** reuse existing `--px-ok/--px-danger/--px-caution`; recording indicator red, Live pill green.
- **Motion:** all ambient animation (aura, pulses, ripples) gated behind `@media (prefers-reduced-motion: reduce)` → static, dimmed states.

Per-tenant accent is applied at runtime by setting a CSS custom property from `app-config.accent` on the session root, so a future tenant theme page only needs to feed that value.

---

## Pre-join wizard (split two-pane)

A shared `WizardFrame` renders the two-pane shell on the cinematic backdrop with a faint aura present (the candidate "meets" the AI before starting):

- **Left pane:** the aura mark, a Fraunces headline, one line of reassuring copy, and the **stepper** (Consent → Verify → Camera & mic).
- **Right pane:** the current step's task card (glass).
- **Mobile:** panes stack — aura + copy on top, card below.

Steps (backend contracts unchanged):

1. **Welcome / landing** *(new step)* — company logo + role, "~X minute conversation," sets expectations ("a calm AI screen; take your time; no trick questions"), single CTA to begin. This is the highest-leverage anti-drop-off screen.
2. **Consent** — AIVIA consent text (whitespace-preserved) + single checkbox; advances on confirm. Timestamped consent event unchanged.
3. **Verify (OTP)** — only when `otp_required`. 6-digit mono input, send cooldown, attempts-remaining, `aria-live` errors. Contract unchanged.
4. **Camera & mic** — live preview, mic-level meter, noise-floor warning if the room is loud (existing `sampleNoiseFloorDbfs`), then the **Start interview** CTA which calls `/start`.

Copy across the wizard is warm and plain — no legalese beyond the required consent block, no jargon.

---

## Live interview surface

Layout (desktop):

- **Aura — centered hero**, slightly left when the panel is expanded. Driven by agent audio amplitude **and** agent state:
  - `listening` → calm slow breathing, dim; label "Listening…"
  - `thinking` → inward swirl/shimmer, slightly dimmer; label "Thinking…"
  - `speaking` → brighter, active morph + amplitude-driven ripple; label "Speaking…"
- **Self-view** — small rounded tile, bottom-left. Camera always on; **no toggle**. (If the camera track fails, show a calm placeholder, not an error.)
- **"Interview Session" panel** — floating glass card, right side, **minimized to a pill by default**; the minimize/expand control toggles it. Expanded: header (chat icon · *Interview Session* italic-serif · green **Live** pill · minimize button) + scrollable alternating bubbles:
  - AI turn: aura-mark avatar, neutral-dark glass bubble, the actual question in italic serif.
  - Candidate turn ("heard"): initial avatar, teal-tinted bubble, subtle "heard" treatment.
- **Top bar** — company logo + role (left); **Recording** indicator + **End interview** (right). End is the only interactive control.
- **Progress chip** — top-center, glass: "Question X of N · MM:SS left" from the room attributes. Hidden gracefully if attributes are absent.
- **Spoken caption** — bottom-center, the AI's current line in italic serif, for accessibility and for candidates who prefer reading along.
- **Removed entirely:** mic toggle, camera toggle, screen-share, keyboard chat input.

Mobile:

- Aura centered; small floating self-view; the panel becomes a **bottom sheet** (drag up to expand, pill when minimized); caption above; **End interview** in the top bar.

### Agent-state → aura mapping

The aura is the candidate's primary feedback that the system is alive and whose turn it is. `useVoiceAssistant()` provides both the agent state and audio track; state transitions cross-fade aura parameters (intensity, hue spread, motion speed) over ~300ms for a non-jarring feel.

---

## End & terminal states

- **End interview → confirmation modal** (glass): *"End the interview? You won't be able to rejoin."* — Cancel (default) / End. On confirm: disconnect the room via the session/room API. The existing `OutcomeWatcher` `CLIENT_INITIATED → onCompleted` branch (`app.tsx`) already routes this to the **Completion** screen — preserve it and lock it with a regression test (see the End-interview wiring note above). The engine records `candidate_disconnected` + the full `SessionResult` server-side independently.
- **Completion screen** — warm, positive, Fraunces headline ("Thanks — your interview's complete"), what-happens-next line. One screen handles `completed`, `candidate_ended`, `candidate_disconnected`, `time_expired`, `knockout_closed` with non-alarming copy for all.
- **Reconnecting overlay** — calm "Reconnecting…", aura dimmed, no scary red. Backed by the existing state-fallback poll.
- **Error screen** — graceful, jargon-free, support note. Handles `session_outcome=error` and pre-join token errors (invalid/expired/superseded/used). Never renders the token.

---

## The Liquid aurora visualizer (bespoke)

A self-contained `LiquidAura` component — the one piece worth real craft.

- **Concept:** a morphing orb of flowing light — a blurred core whose shape gently wobbles (organic, not a rigid circle), filled with a slowly rotating teal→cyan→mint gradient, wrapped in a breathing outer glow and a soft specular sheen.
- **Audio reactivity:** core scale + glow intensity + ripple are driven by the agent's audio amplitude (LiveKit multiband volume on `useVoiceAssistant().audioTrack`). Silence → calm; speech → lively.
- **State reactivity:** listening/thinking/speaking modulate base intensity, motion speed, and hue spread (see mapping above).
- **Color:** reads the tenant accent CSS variable; default teal.
- **Implementation direction:** Canvas/WebGL for the fluid core (best quality, GPU-cheap when sized small) with the glow/sheen as composited layers. Must degrade to a static, gently-dimmed gradient orb under `prefers-reduced-motion` and when no audio track is present (pre-connect buffer).
- **Reuse at two sizes:** large (hero) and small (the panel's AI avatar mark + minimized states) from the same component via a `size` prop.
- **Isolation:** no dependency on session state beyond `audioTrack` + `state` + `accent`; independently testable and previewable.

---

## File-level change map

**Theme & config**
- `app/globals.css` — add `[data-px-theme="dark-cinematic"]` token block + `.px-glass` utilities + aura/keyframe helpers (reduced-motion guarded).
- `app/layout.tsx` — set `data-px-theme="dark-cinematic"`; inject tenant accent CSS var.
- `app-config.ts` — default `audioVisualizerType: 'aura'`; ensure `accent`/`logo` consumed; no removed fields.

**Pre-join wizard** (`app/interview/[token]/`)
- New `WizardFrame` (split two-pane shell, stepper, faint aura) — shared by all steps.
- New `WelcomeStep`.
- Restyle `WizardShell.tsx`, `ConsentStep.tsx`, `OtpStep.tsx`, `CameraMicStep.tsx`, `error/page.tsx`. State machine + API calls unchanged.

**Live session** (`components/interview/`)
- New `components/interview/session/` tree: `LiveInterview`, `AuraStage`, `InterviewSessionPanel`, `SelfView`, `SessionTopBar`, `ProgressChip`, `SpokenCaption`, `EndInterviewDialog`.
- Rework `app/view-controller.tsx` to route Welcome→Live→Completion/Error and mount the new session view.
- `app/app.tsx` — **preserve** the `OutcomeWatcher` `CLIENT_INITIATED → onCompleted` branch (no new flag needed) and add a regression test locking it in (see End-wiring note above).
- Restyle `CompletionScreen`, `SessionErrorScreen`, `DisconnectError`, `ReconnectingOverlay`. Retire/replace `ProgressBanner`, `WelcomeView`, and the `blocks/agent-session-view-01` tile layout + `agent-control-bar` for the candidate path.

**Aura**
- New `LiquidAura` component (replaces use of `agent-audio-visualizer-aura.tsx` and the `bar` default).

**Drift discipline:** any change to `components/px/{Button,Input,Toaster}`, `lib/utils.ts`, or `public/projectx-logo.svg` must mirror `frontend/app` (per CLAUDE.md).

---

## Constraints honored

- **No `@supabase/*`** imports (or any forbidden package) on this surface.
- **LiveKit stays lazy-loaded** — pre-join bundle unaffected; live view code-split as today.
- **Security headers / CSP unchanged** (`next.config.ts`, `proxy.ts`). No new external origins.
- **Audio constraints** read from `audio_processing_hints` — never hard-coded.
- **Candidate token never logged / rendered** in any new component or error path.
- **Accessibility:** every wizard step keyboard-navigable; spoken caption + transcript provide a text channel; aura + all motion respect `prefers-reduced-motion`; focus management on the End modal.
- **Mobile-first** down to 320px.

---

## Out of scope / future

- Tenant-side theme-selection config page (recruiter/admin side) — accent is wired via `app-config` now; the editing UI is later.
- Light theme for the candidate surface — single dark-cinematic theme ships.
- New client→engine signals (e.g. an explicit "candidate ended" RPC) — current outcome resolution is reused as-is.
- Any backend, schema, auth, or engine change.
- Live STT confidence/quality indicators in the transcript.

---

## Testing considerations

- **Unit/branch:** `lib/api/candidate-session.ts` (100% branch) and `lib/env.ts` gates remain green — they are not modified, but the redesign must not regress them.
- **Component:** wizard steps render + advance on valid input and block on invalid (consent unchecked, OTP wrong); End modal confirm/cancel; outcome → screen routing for each `session_outcome` value; aura degrades under `prefers-reduced-motion` and with no audio track.
- **Header gate:** `next.config.ts` `headers()` must still emit every required security header (existing `curl -I` assertion).
- **Manual:** the full candidate journey on desktop + a 320px mobile viewport, plus a live agent session to confirm aura state transitions, progress chip, transcript, and graceful completion.

---

## Open questions

None blocking. Aura implementation tech (WebGL vs Canvas2D) is an implementation-plan detail; the component contract above is fixed.
