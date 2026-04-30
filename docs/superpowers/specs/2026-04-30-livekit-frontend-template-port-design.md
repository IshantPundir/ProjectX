# LiveKit Frontend Template Port — Design

**Date:** 2026-04-30
**Author:** Ishant + Claude (Opus 4.7)
**Status:** Design (pre-implementation)
**Branch:** `feat/phase-3c2-interview-engine` (no new branch — iterating in place)

---

## Problem

The candidate live-interview UI at `app/(interview)/interview/[token]/LiveSession/` is intentionally minimal: a coloured dot indicator for the agent, a basic chat list for the transcript, no audio visualizer, no control bar, no welcome→session transition, no animation. Functional, not polished.

LiveKit ships a full reference frontend — `agent-starter-react` — built around the new Agents UI shadcn registry. We want to port that template into ProjectX as a *baseline*, then customize on top. The end state of this port is: the candidate surface looks and feels like the LiveKit reference, ready for the next phase of UI/UX customization.

This is a **dev-branch transplant**, not a customer-facing release. Buttons that don't make product sense in a proctored interview (mute, camera toggle, screen-share) are surfaced for now and trimmed later.

---

## Decisions captured (the four questions)

1. **Approach: A — Full transplant.** Install the `@agents-ui` shadcn registry into `frontend/app/`. Accept that shadcn primitives (`components/ui/`) co-exist with the in-house `components/px/` design system, sealed to the candidate route segment.
2. **Boundary: a — LiveKit owns from cam-mic onward.** Drop `StartStep`. After cam-mic passes, mount the LiveKit shell. The starter's `WelcomeView` becomes the "ready to start" surface; clicking *Start* triggers our `/api/candidate-session/{token}/start` mutation via `TokenSource.custom`.
3. **Controls: a — Ship starter defaults.** All five `AgentControlBar` controls (mute, camera, screen-share, chat, leave) plus chat input render. Trim post-port during UI customization.
4. **Branding: a — Static for now.** ProjectX logo + `--px-accent` are hardcoded. `companyName` flows in dynamically from the existing `/pre-check` response. Tenant logo + tenant accent are deferred to a separate ticket.

---

## Audit findings (why the implementation is smaller than first scoped)

- **The starter has been refactored since the README.** `components/app/` no longer contains `session-view.tsx`, `tile-layout.tsx`, or `chat-transcript.tsx` — those collapsed into a single Agents UI block, `AgentSessionView_01`, installed via `@agents-ui/agent-session-view-01`. The real port is materially smaller than the README implies.
- **`globals.css` is already shadcn-ready.** Lines 327–360 of `frontend/app/app/globals.css` map every shadcn semantic token (`--background`, `--foreground`, `--primary`, `--destructive`, `--muted`, `--accent`, `--ring`, etc.) to v4 px palette values. Installed shadcn primitives inherit the warm-light palette automatically — no theme-token migration needed.
- **`AgentSessionProvider` mounts `RoomAudioRenderer` internally.** Our existing manual `<RoomAudioRenderer />` becomes a double-mount risk and is dropped.
- **`AgentDisconnectButton` calls `useSessionContext().end()`.** Disconnect lifecycle ends through the session, not the room — same context surface either way.
- **`TokenSource.custom(callback)` is the documented integration point** for backends that already mint LiveKit JWTs. The callback receives `{ roomName, participantName, agentName, roomConfig }` and returns `{ serverUrl, participantToken }`. Our existing `/start` endpoint slots in directly.
- **Existing deps already on the project:** `livekit-client@2.18.8`, `@livekit/components-react@2.9.20`, `lucide-react`, `sonner`, `tailwind-merge`, `clsx`, `@tanstack/react-query`, React 19.2, Next 16.2.
- **Missing deps that the port adds:** `motion`, `class-variance-authority`, `@phosphor-icons/react`, `next-themes`, `tw-animate-css`, `ai`, `media-chrome`, `embla-carousel-react`, `cmdk`, `streamdown`, plus the `@radix-ui/*` primitives that shadcn's `components/ui/` requires (`react-{collapsible,dropdown-menu,popover,scroll-area,select,separator,slot,toggle,tooltip}` at minimum).

---

## Architecture

### Folder layout (additions and deletions)

```
frontend/app/
├── app-config.ts                                    NEW
├── components.json                                  NEW (shadcn config)
├── lib/
│   └── shadcn/utils.ts                              NEW (cn() helper)
├── components/
│   ├── px/                                          UNCHANGED
│   ├── ui/                                          NEW (shadcn primitives, ~10 files)
│   ├── ai-elements/                                 NEW (conversation, message)
│   ├── agents-ui/                                   NEW (~15 files: control bar, visualizers, transcript, …)
│   │   └── blocks/
│   │       └── agent-session-view-01.tsx            NEW (the central composed block)
│   └── interview/
│       ├── providers.tsx                            UNCHANGED
│       └── app/                                     NEW (replaces LiveSession/)
│           ├── app.tsx
│           ├── view-controller.tsx
│           ├── welcome-view.tsx
│           ├── ProgressBanner.tsx
│           ├── CompletionScreen.tsx
│           ├── DisconnectError.tsx
│           └── hooks/
│               ├── use-agent-grace-timeout.ts
│               └── use-stage-progress.ts
├── hooks/                                           NEW (registry-installed)
│   └── agents-ui/
│       ├── use-agent-control-bar.ts
│       └── …
├── app/(interview)/interview/[token]/
│   ├── WizardShell.tsx                              MODIFIED (drop StartStep + creds state)
│   ├── StartStep.tsx                                DELETED
│   └── LiveSession/                                 DELETED (entire directory)
└── tests/components/interview/
    ├── StartStep.test.tsx                           DELETED
    ├── LiveSessionShell.test.tsx                    REPLACED → app.test.tsx
    ├── ProgressBanner.test.tsx                      MODIFIED (import path)
    └── CompletionScreen.test.tsx                    MODIFIED (import path)
```

### What survives, what dies

| Existing file | Fate | Reason |
|---|---|---|
| `LiveSession/LiveSessionShell.tsx` | **deleted** | replaced by `components/interview/app/{app, view-controller}.tsx` |
| `LiveSession/AgentTile.tsx` | **deleted** | replaced by `AgentAudioVisualizerBar` (and other visualizer variants) inside the Agents UI block |
| `LiveSession/CandidateSelfView.tsx` | **deleted** | replaced by media tiles inside `AgentSessionView_01`; `MEDIA_LOST` detection moves to `AgentControlBar`'s `onDeviceError` callback |
| `LiveSession/TranscriptPane.tsx` | **deleted** | replaced by `AgentChatTranscript` (used inside `AgentSessionView_01`) |
| `LiveSession/ProgressBanner.tsx` | **moved** to `components/interview/app/ProgressBanner.tsx` | engine-published participant attributes (`current_question_index`, `total_questions`, `time_remaining_seconds`) — no LiveKit equivalent |
| `LiveSession/CompletionScreen.tsx` | **moved** | unchanged copy |
| `LiveSession/DisconnectError.tsx` | **moved**, COPY map extended | new error codes: `SESSION_ALREADY_STARTED`, `SESSION_START_FAILED` |
| `LiveSession/hooks/use-agent-state.ts` | **deleted** | superseded by Agents UI's session context |
| `LiveSession/hooks/use-agent-grace-timeout.ts` | **moved** | 30 s no-show is product behaviour |
| `LiveSession/hooks/use-stage-progress.ts` | **moved** | reads engine attributes |
| `app/(interview)/interview/[token]/StartStep.tsx` | **deleted** | `WelcomeView` replaces its role; `TOKEN_ALREADY_USED` 409 routing moves into the `TokenSource.custom` callback |

### Two design systems, sealed at the route boundary

After this port the codebase has two primitive libraries:

- `components/px/` — in-house, on `@base-ui-components/react`, used everywhere except the candidate interview surface.
- `components/ui/` + `components/agents-ui/` + `components/ai-elements/` — shadcn-based, used **only** under `app/(interview)/`.

The seal is enforced by convention (and a CLAUDE.md note). The dashboard never imports from `components/{ui,agents-ui,ai-elements}/`. The candidate surface may import from either. `globals.css` already maps the shadcn token namespace onto the px palette so visual coherence is automatic.

---

## Connection model

The starter's pattern (`useSession(tokenSource)` + `<AgentSessionProvider>`) replaces our `<LiveKitRoom serverUrl token>`. We adapt the starter's `TokenSource.endpoint('/api/token')` (which assumes a generic Next.js token route) by using `TokenSource.custom(callback)` — the documented integration point for backends that already mint JWTs.

```ts
// inside components/interview/app/app.tsx
import { TokenSource } from 'livekit-client'
import { useSession } from '@livekit/components-react'
import { candidateSessionApi } from '@/lib/api/candidate-session'

const cachedCredsRef = useRef<{ url: string; token: string } | null>(null)

const tokenSource = useMemo(() => TokenSource.custom(async () => {
  // Single-use enforcement: cache once, return cached on retry to avoid
  // a second POST that backend returns 409 for. React strict-mode double
  // invoke / hot reload re-mount path.
  if (cachedCredsRef.current) {
    return {
      serverUrl: cachedCredsRef.current.url,
      participantToken: cachedCredsRef.current.token,
    }
  }
  const creds = await candidateSessionApi.start(token)
  cachedCredsRef.current = { url: creds.livekit_url, token: creds.livekit_token }
  return { serverUrl: creds.livekit_url, participantToken: creds.livekit_token }
}), [token])

const session = useSession(tokenSource)
```

**Why `custom` and not `literal`:** `TokenSource.literal({ url, token })` requires the credentials to exist *before* `useSession()` is called. That forces token-minting to happen in `WizardShell` before the LiveKit shell mounts, which means the welcome-view never gets to play the Start-button role. Putting the mint *inside* `TokenSource.custom` keeps the welcome-view as the firing pin — the candidate clicks *Start*, `session.start()` fires, the callback runs, the JWT is minted, the room connects.

**Agent dispatch.** Nexus's `/start` endpoint already encodes `RoomConfiguration.agents = [RoomAgentDispatch(...)]` server-side when minting the LiveKit JWT (see `interview_engine/AGENTS.md` § "How it boots"). The frontend passes no `agentName` — automatic dispatch reads it out of the token. This matches the starter's `app-config.ts` default of `agentName: undefined`.

**Provider hierarchy in the candidate route:**

```tsx
<QueryClientProvider>           {/* components/interview/providers.tsx — already there */}
  <ThemeProvider attribute="class" forcedTheme="light">
    <AgentSessionProvider session={session}>
      <ViewController appConfig={appConfig} />
      <StartAudioButton label="Start audio" />  {/* browser-autoplay unlock */}
    </AgentSessionProvider>
  </ThemeProvider>
</QueryClientProvider>
```

The `<Toaster>` mount stays upstream in `interview/providers.tsx`; we don't remount the starter's because we already have one.

---

## Wizard handoff + the candidate route boundary

### Today

```
app/(interview)/layout.tsx                  ← QueryClientProvider + Toaster
  app/(interview)/interview/[token]/page.tsx     ← server component, awaits params, returns <WizardShell>
    WizardShell.tsx
      ├─ ConsentStep / OtpStep / CameraMicStep / StartStep
      └─ (creds set) → LiveSessionShell
```

### After

```
app/(interview)/layout.tsx                  ← unchanged
  app/(interview)/interview/[token]/page.tsx     ← unchanged
    WizardShell.tsx                          ← MODIFIED: drop creds state + StartStep
      ├─ ConsentStep / OtpStep / CameraMicStep
      └─ (camMicPassed) → <App appConfig={resolved} token={token} preCheck={data} />
                              <ThemeProvider forcedTheme="light">
                                <AgentSessionProvider session={useSession(tokenSource)}>
                                  <ViewController appConfig />
                                    ├─ <WelcomeView onStartCall={start}>     when !isConnected, outcome='live'
                                    ├─ <AgentSessionView_01 …>               when isConnected,  outcome='live'
                                    │     <ProgressBanner /> overlaid on top
                                    ├─ <CompletionScreen />                  when outcome='completed'
                                    └─ <DisconnectError code={errorCode} />  when outcome='error'
                                  <StartAudioButton />
                                </AgentSessionProvider>
                              </ThemeProvider>
```

### Concrete diff to `WizardShell.tsx`

- Delete `creds` state and `setCreds`.
- Delete the `if (creds) return <LiveSessionShell .../>` early return.
- Delete the `currentStep === 'start'` import path; `StartStep.tsx` is removed entirely.
- Replace the `currentStep === 'cam-mic' && camMicPassed` branch from `<StartStep>` to `<App appConfig={…} token={token} preCheck={data} />`.
- `StepProgress` array drops the `start` step (Consent → Verify? → Camera & mic — three steps).

### `appConfig` shape

Built per-render from the `data` (pre-check response) inside `WizardShell` and passed to `<App>`:

```ts
const appConfig: AppConfig = {
  companyName: data.company_name,
  pageTitle: `${data.company_name} · Interview`,
  pageDescription: 'AI-led interview',
  startButtonText: 'Start interview',
  logo: '/projectx-logo.svg',                  // static; tenant logo deferred
  accent: 'var(--px-accent)',
  agentName: undefined,                        // automatic dispatch via JWT room_config
  supportsChatInput: true,
  supportsVideoInput: true,
  supportsScreenShare: true,
  isPreConnectBufferEnabled: true,
  audioVisualizerType: 'bar',
}
```

The `AppConfig` interface is ported verbatim from the starter's `app-config.ts`; defaults match the starter's, only the brand fields are ProjectX-specific.

---

## Outcome routing + domain overlays

Two axes are tracked in the new shell:

| Axis | Source | Values |
|---|---|---|
| Connection state | `useSessionContext().isConnected` | `false` (welcome) / `true` (session) |
| Outcome | local `useState` in `app.tsx` | `'live'`, `'completed'`, or `'error'` + `errorCode` |

`ViewController` reads both:

```tsx
if (outcome === 'completed')          return <CompletionScreen />
if (outcome === 'error' && errorCode) return <DisconnectError code={errorCode} />
if (!isConnected)                     return <WelcomeView onStartCall={start} … />
return <AgentSessionView_01 … />
```

### Outcome triggers

| Trigger | Source | Resulting outcome | errorCode |
|---|---|---|---|
| Engine `Action.CLOSE` → engine disconnects → `Disconnected` event | `useSession` lifecycle: `session.on('disconnected', reason)` if reason is normal | `'completed'` | — |
| Candidate clicks End Call | `AgentDisconnectButton` → `session.end()` → same path | `'completed'` | — |
| Camera/mic dies mid-session | `AgentControlBar`'s `onDeviceError` | `'error'` | `MEDIA_LOST` |
| 30 s grace timeout, no agent participant | `useAgentGraceTimeout` (overlay hook in `ViewController`) | `'error'` | `AGENT_NO_SHOW` |
| `TokenSource.custom` callback rejects | caught in `useSession`'s start failure / 409 from `/start` | `'error'` | `SESSION_ALREADY_STARTED` (409) / `SESSION_START_FAILED` (other) |

Graceful-vs-error disconnect signalling from the engine is still a Phase 3D follow-up tracked in `interview_engine/AGENTS.md`. After the port any non-error disconnect routes to `CompletionScreen`, matching current behaviour.

### Domain overlays — what survives moving

| Overlay | After | Why kept |
|---|---|---|
| `ProgressBanner` | `components/interview/app/ProgressBanner.tsx`, mounted *inside* `ViewController` above `AgentSessionView_01` (sticky top, z-index above the block) | engine-published `set_attributes`, no LiveKit equivalent |
| `useAgentGraceTimeout` | side-effect hook in `ViewController` | 30 s no-show is product behaviour |
| `useStageProgress` | `components/interview/app/hooks/use-stage-progress.ts` | reads engine attributes |
| `CompletionScreen` | `components/interview/app/CompletionScreen.tsx` | unchanged copy |
| `DisconnectError` | `components/interview/app/DisconnectError.tsx` | extend `COPY` map |

---

## Build sequence

Each step independently testable; do not skip ordering.

1. **Add the missing deps + scaffolding.** Install `motion`, `class-variance-authority`, `@phosphor-icons/react`, `next-themes`, `tw-animate-css`, `ai`, `media-chrome`, `embla-carousel-react`, `cmdk`, `streamdown`, plus the eight Radix primitives the agents-ui registry pulls in transitively. Add `components.json` (shadcn config matching the starter's), `lib/shadcn/utils.ts` (`cn()` helper). **Verification:** `npm run build` passes, no other route broken.
2. **Run shadcn install.** Mirror the starter's `pnpm shadcn:install`: `npx shadcn@latest add @agents-ui/agent-{audio-visualizer-bar,grid,radial,wave,aura,control-bar,session-provider,track-control,track-toggle,chat-transcript,chat-indicator,disconnect-button,session-view-01} @agents-ui/start-audio-button @ai-elements/{conversation,message}`. Populates `components/agents-ui/`, `components/ai-elements/`, `components/ui/`, `hooks/agents-ui/`. **Verification:** every file written, no manual edits yet.
3. **Build `app-config.ts`** — port the starter's file with our defaults.
4. **Build `components/interview/app/app.tsx`** — `useMemo` builds `TokenSource.custom` wrapping `candidateSessionApi.start(token)`; cache result in a closure ref. Owns the `outcome` state. Wraps `<AgentSessionProvider>`.
5. **Build `view-controller.tsx`** — port the starter's, swap `<AgentSessionView_01>` import path, layer `ProgressBanner` on top, install `useAgentGraceTimeout` here.
6. **Build `welcome-view.tsx`** — port the starter's. Replace static copy with `appConfig.companyName` + `data.duration_minutes`. Keep the start button.
7. **Move + adapt `ProgressBanner`, `CompletionScreen`, `DisconnectError`, hooks** — copy-paste, update imports, extend `DisconnectError` COPY map.
8. **Update `WizardShell.tsx`** — drop `creds`, drop `StartStep` import, render `<App>` after cam-mic.
9. **Delete** `app/(interview)/interview/[token]/StartStep.tsx`, `app/(interview)/interview/[token]/LiveSession/`.
10. **Update tests:** delete `StartStep.test.tsx` and `LiveSessionShell.test.tsx`. Replace the latter with a test on the new `app.tsx` exercising the grace timeout against the new mock surface (`useSessionContext`, `useSession`). Update import paths in `ProgressBanner.test.tsx` and `CompletionScreen.test.tsx`. Add jsdom stubs for `ResizeObserver`, `IntersectionObserver`, `matchMedia` in `tests/setup.ts`.
11. **Force light theme.** Wrap `<App>` in `<ThemeProvider attribute="class" forcedTheme="light">`. No toggle, no dark-mode variants exercised.
12. **End-to-end smoke** locally: real candidate JWT, real engine dispatch. Walk Wizard → Welcome → Connect → Speak → End. Confirm `CompletionScreen` renders.

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Double `RoomAudioRenderer` (`AgentSessionProvider` mounts one; old code mounted another) | Medium | Drop the manual mount; rely on the provider. |
| `session.start()` retry on remount in dev (React strict mode) → second POST → 409 | Medium | Cache resolved `{ url, token }` in a closure ref inside `TokenSource.custom`. Second invocation returns the cached value. |
| `motion/react` and `@radix-ui/*` need `ResizeObserver`, `IntersectionObserver`, `matchMedia` in jsdom | Medium | Add stubs to `tests/setup.ts`. |
| `usePersistentUserChoices` writes device IDs to localStorage — borderline PII | Low | Pass `saveUserChoices={false}` to `AgentSessionView_01` (or via `AgentControlBar` props). |
| `CLAUDE.md` says "no shadcn/ui in this codebase" — after this PR that's untrue for the candidate surface | Documentation | Update `frontend/app/CLAUDE.md` and `frontend/app/AGENTS.md` to describe the shadcn enclave at `components/{ui,agents-ui,ai-elements}/`, sealed to `app/(interview)/`. |
| Bundle size: agents-ui block + Radix + motion ≈ +60–100 KB gzipped | Low (route exempt from JS budget per CLAUDE.md) | Lazy-load `<App>` via `next/dynamic` in `WizardShell` — same pattern as today's `LiveSessionShell`. |
| `AgentSessionView_01` source not browseable on GitHub (registry-generated, not committed in raw form) | Low | Trust the registry. The rest of the agents-ui chain (provider, control bar, transcript) reads cleanly. |
| Engine "graceful close" signal still missing | Low (matches existing behaviour) | Out of scope for this port. |

---

## Documentation updates required in the same PR

- `frontend/app/CLAUDE.md` — note the shadcn enclave at `components/{agents-ui, ai-elements, ui}/` and the route boundary that seals it to `app/(interview)/`.
- `frontend/app/AGENTS.md` — same.
- Root `CLAUDE.md` § "Tech Stack" / Phase 3C.2 — update the LiveKit-integration line to reflect the agents-ui shadcn enclave.

---

## Out of scope

- Tenant-driven branding (logo, accent) — separate ticket; touches DB schema + onboarding + asset upload.
- Trimming `AgentControlBar` to the proctored-interview minimum (drop mute, camera toggle, screen-share, chat input) — happens during the user's UI/UX customization phase, post-port.
- Engine `Action.CLOSE` → distinct "graceful disconnect" signal back to the frontend.
- Mid-session rejoin if the candidate's network drops.
- LiveKit Egress recording pipeline.
- Real-time scoring / probe selection (Phase 3D `analysis` module).
- AI Copilot panel (`components/copilot/`) for human participants.

---

## Acceptance criteria

- The candidate completes the wizard (consent → otp? → cam-mic), sees the LiveKit `WelcomeView` with `companyName` + duration, clicks *Start*, the room connects, the agent joins, the candidate speaks, the agent responds.
- The `AgentSessionView_01` block renders with the bar audio visualizer, transcript, and `AgentControlBar` (all five default controls visible).
- `ProgressBanner` overlays the session view with `Q3 of 9 · 11 min remaining` driven by engine-published participant attributes.
- 30 s with no agent participant → `DisconnectError` renders with `AGENT_NO_SHOW`.
- Camera/mic loss mid-session → `DisconnectError` with `MEDIA_LOST`.
- Backend `/start` 409 (token already used) → `DisconnectError` with `SESSION_ALREADY_STARTED`.
- Engine `Action.CLOSE` → `CompletionScreen`.
- Candidate clicks End Call → `CompletionScreen`.
- `npm run build`, `npm run lint`, `npm run type-check`, `npm run test` all pass.
- No regressions on the dashboard surface (no `components/{ui,agents-ui,ai-elements}/` imports outside `app/(interview)/`).
