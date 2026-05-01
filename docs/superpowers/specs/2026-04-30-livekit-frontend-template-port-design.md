# LiveKit Frontend Template Port — Design

**Date:** 2026-04-30
**Author:** Ishant + Claude (Opus 4.7)
**Status:** Design (pre-implementation)
**Branch:** `feat/phase-3c2-interview-engine` (no new branch — iterating in place)

---

## Problem

The candidate live-interview UI at `app/(interview)/interview/[token]/LiveSession/` is intentionally minimal: a coloured dot indicator for the agent, a basic chat list for the transcript, no audio visualizer, no control bar, no welcome→session transition, no animation. Functional, not polished.

LiveKit ships a full reference frontend — `agent-starter-react` — built around the new Agents UI shadcn registry. We want to port that template into ProjectX as a *baseline*, then customize on top. The end state of this port is: the candidate surface looks and feels like the LiveKit reference, ready for the next phase of UI/UX customization.

This is a **dev-branch transplant** running alongside two correctness improvements that the current shell papers over:

1. **Graceful-vs-error disconnect signalling.** Today every disconnect routes to `CompletionScreen` because the engine sends no structured signal. Half the time that's wrong.
2. **Mid-session rejoin.** Today `<AlreadyStartedPanel>` is a dead end ("rejoin will be available in the next release"). LiveKit's SDK already handles transient drops natively; hard rejoin (page refresh, tab close) needs a new backend endpoint.

Buttons that don't make product sense in a proctored interview (mute, camera toggle, screen-share) are surfaced for now and trimmed later.

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
| Engine reaches `Action.CLOSE` and publishes `session_outcome='completed'` before shutdown | `agent.attributes['session_outcome']` read on `Disconnected` event | `'completed'` | — |
| Candidate clicks End Call | `AgentDisconnectButton` → `session.end()` → `Disconnected` with reason `CLIENT_INITIATED` | `'completed'` | — |
| Engine publishes `session_outcome='error'` (engine-side error before shutdown) | `agent.attributes['session_outcome']` read on `Disconnected` event | `'error'` | `ENGINE_ERROR` |
| Camera/mic dies mid-session | `AgentControlBar`'s `onDeviceError` | `'error'` | `MEDIA_LOST` |
| 30 s grace timeout, no agent participant | `useAgentGraceTimeout` (overlay hook in `ViewController`) | `'error'` | `AGENT_NO_SHOW` |
| `TokenSource.custom` callback rejects (`/start` 409, `/rejoin` 4xx, network) | caught in `useSession`'s start failure | `'error'` | `SESSION_ALREADY_STARTED` (409 from `/start`) / `SESSION_START_FAILED` (other) / `REJOIN_REJECTED` (4xx from `/rejoin`) |
| Hard disconnect with no `session_outcome` attribute and non-clean reason (`JOIN_FAILURE`, etc.) | `Disconnected` event with reason ≠ `CLIENT_INITIATED` and no outcome attribute | `'error'` | `UNEXPECTED_DISCONNECT` |
| Transient drop (ICE restart or signaling reconnect) | `Reconnecting` event from session lifecycle | **outcome stays `'live'`**, overlay banner renders | — (banner only) |

### Domain overlays — what survives moving

| Overlay | After | Why kept |
|---|---|---|
| `ProgressBanner` | `components/interview/app/ProgressBanner.tsx`, mounted *inside* `ViewController` above `AgentSessionView_01` (sticky top, z-index above the block) | engine-published `set_attributes`, no LiveKit equivalent |
| `useAgentGraceTimeout` | side-effect hook in `ViewController` | 30 s no-show is product behaviour |
| `useStageProgress` | `components/interview/app/hooks/use-stage-progress.ts` | reads engine attributes |
| `CompletionScreen` | `components/interview/app/CompletionScreen.tsx` | unchanged copy |
| `DisconnectError` | `components/interview/app/DisconnectError.tsx` | extend `COPY` map |
| `useSessionOutcome` (NEW) | `components/interview/app/hooks/use-session-outcome.ts` | reads agent participant's `session_outcome` attribute on disconnect; this is the new graceful-vs-error router |
| `ReconnectingOverlay` (NEW) | `components/interview/app/ReconnectingOverlay.tsx` | renders during the SDK's transient `Reconnecting` state — outcome stays `'live'`, just a UI hint |

---

## Graceful disconnect signal

### Engine side

`backend/interview_engine/agents/interviewer.py` — when the state machine returns `Action.CLOSE`, before calling agent shutdown, publish a structured outcome attribute on the agent's local participant:

```python
# inside the CLOSE handler, before session.shutdown()
await self._room_io.room.local_participant.set_attributes({
    "session_outcome": "completed",
})
# now shut down
```

For engine-side errors (config fetch failure, invalid state machine transition, OpenAI hard error), publish `session_outcome="error"` instead before shutdown.

The `set_attributes` mechanism is already used by the engine for `current_question_index` / `total_questions` / `time_remaining_seconds` — same path, new key. Attribute writes are propagated to remote participants via the LiveKit signaling channel before the participant disconnects, so the candidate's frontend reads it on the same event tick.

### Frontend side

`useSessionOutcome` hook listens to the `Disconnected` event and reads the agent participant's last-seen `session_outcome` attribute (held in a ref so it survives the moment the participant is removed from `useParticipants()`).

Routing logic inside `app.tsx`:

```ts
const onDisconnect = useCallback((reason?: DisconnectReason) => {
  const outcome = agentOutcomeRef.current  // 'completed' | 'error' | undefined

  if (outcome === 'completed') {
    setOutcome('completed')
  } else if (outcome === 'error') {
    setOutcome('error', 'ENGINE_ERROR')
  } else if (reason === DisconnectReason.CLIENT_INITIATED) {
    // Candidate clicked End Call.
    setOutcome('completed')
  } else {
    // No outcome attribute, non-clean reason. Treat as error.
    setOutcome('error', 'UNEXPECTED_DISCONNECT')
  }
}, [])
```

`agentOutcomeRef` is updated by a `useEffect` watching the agent participant's `attributes['session_outcome']` — captured on the way down, before the participant disappears from the participants list.

---

## Mid-session reconnect — two cases

### Case A — Transient reconnect (no backend work)

The LiveKit SDK already handles WiFi switches, brief network loss, and ICE restarts automatically. It reuses the same JWT and emits `Reconnecting` → `Reconnected` events on the session.

Frontend work:

- `<ReconnectingOverlay />` mounts inside `ViewController` and reads `useSessionContext().state` (or equivalent — exact API depends on `useSession` return shape; verified in implementation phase). When the session is reconnecting, the overlay covers the page with: spinner + "Reconnecting…" + a 30 s countdown after which we route to `DisconnectError` with code `RECONNECT_FAILED`.
- The 30 s ceiling matches the existing grace timeout. After that, we treat the connection as dead.

The outcome state stays `'live'` during reconnect — the overlay is just a UI hint. If the SDK fails the reconnect, it will emit `Disconnected` and we route through the normal error path.

### Case B — Hard rejoin (new backend endpoint + WizardShell branch)

Triggered when:
- Candidate refreshes the page mid-session, OR
- Candidate's network drops for longer than the JWT TTL or the SDK's reconnect window, OR
- Candidate closes and reopens the tab.

In all three, the candidate JWT is still valid (72-hour scheduling-link expiry), the session row is in `state='active'`, but the LiveKit JWT was atomically consumed at `/start`. They need a *new* LiveKit JWT for the same room.

#### Backend: `POST /api/candidate-session/{token}/rejoin`

```python
# backend/nexus/app/modules/session/router.py
@router.post("/api/candidate-session/{token}/rejoin", response_model=StartSessionResponse)
async def rejoin(token: str, request: Request, db: AsyncSession = Depends(get_tenant_db)):
    # 1. Verify candidate JWT (same path as /start uses).
    payload = verify_candidate_token(token)

    # 2. Fetch session row.
    session = await session_repo.get_by_id(db, payload.session_id)

    # 3. Gate: must be in 'active' state. 'completed' / 'cancelled' / 'error' → 409.
    if session.state != SessionState.ACTIVE:
        raise HTTPException(409, detail="Session not in active state", code="SESSION_NOT_REJOINABLE")

    # 4. Optional: check engine has not signalled completion.
    #    (We could also rely solely on the session.state column; engine writes that
    #    on graceful close via the internal results endpoint.)

    # 5. Mint a new LiveKit access token for the SAME room, same identity.
    new_lk_token = mint_livekit_token(
        identity=session.candidate_identity,
        room_name=session.room_name,
        agents=[],  # Engine is already in the room — no re-dispatch.
    )

    # 6. Audit log entry.
    await audit.log(
        db,
        action="candidate_session.rejoin",
        actor_id=payload.candidate_id,
        tenant_id=session.tenant_id,
        resource_type="session",
        resource_id=session.id,
        correlation_id=session.correlation_id,
    )

    return StartSessionResponse(
        livekit_url=settings.LIVEKIT_URL,
        livekit_token=new_lk_token,
        room_name=session.room_name,
        session_id=session.id,
    )
```

**Critical differences from `/start`:**
- No engine dispatch (engine is already in the room — re-dispatching would crash with duplicate-agent-instance behaviour).
- No `engine_token_uses` row (the engine dispatch token isn't re-issued).
- No state transition (session stays `active`).
- Token is *not* atomically single-use — multiple successful rejoins are allowed within the JWT lifetime, gated by rate limit.

**Rate limiting:** per the root `CLAUDE.md` rate-limit table, this endpoint is in the "all other authenticated" class. We tighten it to 5/hour per token, 3/min per IP — rejoin should be rare.

**Single-tab enforcement at LiveKit level:** if a candidate has two tabs open with two valid rejoin tokens connecting to the same room with the same identity, LiveKit emits `DUPLICATE_IDENTITY` and disconnects the older session. That's the right behaviour — last-rejoin wins.

#### Frontend: WizardShell active-state branch + rejoin TokenSource

```tsx
// inside WizardShell.tsx
if (data.state === 'active') {
  return <App appConfig={resolved} token={token} preCheck={data} mode="rejoin" />
}
```

`<App>` accepts a `mode: 'start' | 'rejoin'` prop. The `TokenSource.custom` callback branches on it:

```ts
const tokenSource = useMemo(() => TokenSource.custom(async () => {
  if (cachedRef.current) return cachedRef.current
  const creds = mode === 'rejoin'
    ? await candidateSessionApi.rejoin(token)
    : await candidateSessionApi.start(token)
  cachedRef.current = { serverUrl: creds.livekit_url, participantToken: creds.livekit_token }
  return cachedRef.current
}), [token, mode])
```

The `WelcomeView` copy adapts to `mode`:
- `mode='start'`: "Start interview" (current copy).
- `mode='rejoin'`: "Rejoin your interview" + "You were disconnected. Click rejoin to continue where you left off." copy.

The interview engine's progress (Q3 of 9 etc) is published via participant attributes, so when the rejoining candidate's session connects, `ProgressBanner` updates immediately to the latest state. Time elapsed during the disconnect counts against the candidate — the engine's clock doesn't pause. This is intentional (no clock-stopping abuse).

#### Edge cases

| Case | Behaviour |
|---|---|
| Candidate rejoins after engine has already CLOSEd the session | `session.state` is `completed`. `/rejoin` returns 409 `SESSION_NOT_REJOINABLE`. Frontend surfaces `DisconnectError` with code `SESSION_ALREADY_COMPLETED`. |
| Candidate opens a second tab while the first is still connected | LiveKit emits `DUPLICATE_IDENTITY` on the older tab. Older tab routes to `DisconnectError` with code `DUPLICATE_SESSION`. |
| Candidate's JWT expires mid-session (72-hour TTL elapsed) | `/rejoin` returns 401. Frontend surfaces `DisconnectError` with code `TOKEN_EXPIRED`. |
| Candidate rejoins and engine has crashed (room exists but no agent participant) | The 30 s grace timeout fires after rejoin, routing to `AGENT_NO_SHOW` as today. |
| Candidate rejoins 5+ times within an hour | Rate limit fires, `/rejoin` returns 429. Frontend surfaces `DisconnectError` with code `REJOIN_RATE_LIMITED`. |

---

## Build sequence

Each step independently testable; do not skip ordering.

### Phase 1 — Frontend port

1. **Add the missing deps + scaffolding.** Install `motion`, `class-variance-authority`, `@phosphor-icons/react`, `next-themes`, `tw-animate-css`, `ai`, `media-chrome`, `embla-carousel-react`, `cmdk`, `streamdown`, plus the eight Radix primitives the agents-ui registry pulls in transitively. Add `components.json` (shadcn config matching the starter's), `lib/shadcn/utils.ts` (`cn()` helper). **Verification:** `npm run build` passes, no other route broken.
2. **Run shadcn install.** Mirror the starter's `pnpm shadcn:install`: `npx shadcn@latest add @agents-ui/agent-{audio-visualizer-bar,grid,radial,wave,aura,control-bar,session-provider,track-control,track-toggle,chat-transcript,chat-indicator,disconnect-button,session-view-01} @agents-ui/start-audio-button @ai-elements/{conversation,message}`. Populates `components/agents-ui/`, `components/ai-elements/`, `components/ui/`, `hooks/agents-ui/`. **Verification:** every file written, no manual edits yet.
3. **Build `app-config.ts`** — port the starter's file with our defaults.
4. **Build `components/interview/app/app.tsx`** — accepts `mode: 'start' | 'rejoin'`. `useMemo` builds `TokenSource.custom` wrapping the matching API call; cache result in a closure ref. Owns the `outcome` state. Wraps `<AgentSessionProvider>`.
5. **Build `view-controller.tsx`** — port the starter's, swap `<AgentSessionView_01>` import path, layer `ProgressBanner` on top, install `useAgentGraceTimeout` here, mount `<ReconnectingOverlay />`.
6. **Build `welcome-view.tsx`** — port the starter's. Replace static copy with `appConfig.companyName` + `data.duration_minutes`. Branch copy on `mode='start'` vs `mode='rejoin'`.
7. **Move + adapt `ProgressBanner`, `CompletionScreen`, `DisconnectError`, hooks** — copy-paste, update imports, extend `DisconnectError` COPY map with all new codes (`ENGINE_ERROR`, `UNEXPECTED_DISCONNECT`, `RECONNECT_FAILED`, `SESSION_ALREADY_COMPLETED`, `DUPLICATE_SESSION`, `TOKEN_EXPIRED`, `REJOIN_RATE_LIMITED`, `REJOIN_REJECTED`).
8. **Build `useSessionOutcome` hook** — listens to agent participant attributes, tracks `session_outcome` in a ref that survives the participant's removal from the participants list.
9. **Build `<ReconnectingOverlay />`** — reads session reconnect state, renders dimming overlay with spinner + 30 s countdown.

### Phase 2 — Backend rejoin endpoint

10. **Add `POST /api/candidate-session/{token}/rejoin`** in `backend/nexus/app/modules/session/router.py`. Verifies candidate JWT (reuses `verify_candidate_token`), checks `session.state == 'active'`, mints a new LiveKit JWT for the same room (no agent dispatch), audit-logs the rejoin.
11. **Add rate limit declaration** at the route: 5/hour per token, 3/min per IP.
12. **Add backend test:** rejoin happy path, `session.state != 'active'` 409, expired JWT 401, rate limit 429.
13. **Update `lib/api/candidate-session.ts`** with `rejoin()` method.

### Phase 3 — Engine graceful close signal

14. **Update `backend/interview_engine/agents/interviewer.py`** — before each shutdown path (CLOSE action; engine-side error handlers), publish `session_outcome` attribute on the agent's local participant. Covers both successful completion and known error states.
15. **Add engine test:** state-machine reaches CLOSE → `set_attributes` called with `session_outcome='completed'` before shutdown.

### Phase 4 — Wiring + cleanup

16. **Update `WizardShell.tsx`** — drop `creds`, drop `StartStep` import, render `<App>` after cam-mic with `mode='start'`. Add `state === 'active'` branch that renders `<App mode='rejoin' />` instead of `<AlreadyStartedPanel>`.
17. **Delete** `app/(interview)/interview/[token]/StartStep.tsx`, `app/(interview)/interview/[token]/LiveSession/`, the `<AlreadyStartedPanel>` function inside `WizardShell.tsx`.
18. **Update tests:** delete `StartStep.test.tsx` and `LiveSessionShell.test.tsx`. Replace with: `app.test.tsx` (grace timeout + outcome routing in start mode), `app-rejoin.test.tsx` (rejoin TokenSource path), `useSessionOutcome.test.tsx`. Update import paths in `ProgressBanner.test.tsx` and `CompletionScreen.test.tsx`. Add jsdom stubs for `ResizeObserver`, `IntersectionObserver`, `matchMedia` in `tests/setup.ts`.
19. **Force light theme.** Wrap `<App>` in `<ThemeProvider attribute="class" forcedTheme="light">`. No toggle, no dark-mode variants exercised.
20. **Update CLAUDE.md / AGENTS.md** — root, `frontend/app/`, and `interview_engine/` to reflect the shadcn enclave, the rejoin endpoint, and the graceful-close attribute contract.
21. **End-to-end smoke** locally: real candidate JWT, real engine dispatch. Walk Wizard → Welcome → Connect → Speak → End. Confirm `CompletionScreen` renders. Refresh mid-session → confirm rejoin flow → confirm progress banner picks up where it left off. Kill the engine container mid-session → confirm `DisconnectError` with `UNEXPECTED_DISCONNECT`.

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
| Engine attribute write race — `set_attributes('session_outcome')` may not propagate before the participant disconnects | Medium | `set_attributes` returns when the signaling write is acknowledged. Engine awaits the write before calling `agent.shutdown()`. Frontend captures attribute in a ref via `useEffect` so it survives participant removal. Verified via test: simulate participant attribute change followed by disconnect — outcome reads correctly. |
| Rejoin endpoint abuse — repeated rejoin to extend session past intended duration | Medium | Engine's clock keeps running during disconnects (no clock-stopping). Rate limit at 5/hour per token, 3/min per IP. Audit log every rejoin so investigation is possible. |
| Multi-tab rejoin → DUPLICATE_IDENTITY race | Low | LiveKit auto-disconnects the older session. Older tab routes to `DisconnectError` with `DUPLICATE_SESSION`. Tested as part of the rejoin test suite. |
| Engine survives candidate disconnect indefinitely (zombie engines) | Low | Out of scope for this PR — engine has its own idle/timeout heuristics already in `state_machine.py`. Note for follow-up: consider engine-side "no candidate for N minutes → CLOSE" guard. |
| Backend `/rejoin` touches the session module — needs human review per root `CLAUDE.md` § "Human Review Required For" (session state machine transitions) | Medium | Rejoin doesn't transition state — session stays `active`. But the audit log entry, rate limit, and security headers are non-trivial. Mark for senior reviewer in PR description. |

---

## Documentation updates required in the same PR

- `frontend/app/CLAUDE.md` — note the shadcn enclave at `components/{agents-ui, ai-elements, ui}/` and the route boundary that seals it to `app/(interview)/`.
- `frontend/app/AGENTS.md` — same.
- Root `CLAUDE.md` § "Tech Stack" / Phase 3C.2 — update the LiveKit-integration line to reflect the agents-ui shadcn enclave.

---

## Out of scope

- Tenant-driven branding (logo, accent) — separate ticket; touches DB schema + onboarding + asset upload.
- Trimming `AgentControlBar` to the proctored-interview minimum (drop mute, camera toggle, screen-share, chat input) — happens during the user's UI/UX customization phase, post-port.
- LiveKit Egress recording pipeline.
- Real-time scoring / probe selection (Phase 3D `analysis` module).
- AI Copilot panel (`components/copilot/`) for human participants.
- Engine-side zombie-cleanup ("no candidate for N minutes → CLOSE") — engine already has timing logic in `state_machine.py`; tighten if needed in a follow-up.

---

## Acceptance criteria

### Frontend port (Phase 1)

- The candidate completes the wizard (consent → otp? → cam-mic), sees the LiveKit `WelcomeView` with `companyName` + duration, clicks *Start*, the room connects, the agent joins, the candidate speaks, the agent responds.
- The `AgentSessionView_01` block renders with the bar audio visualizer, transcript, and `AgentControlBar` (all five default controls visible).
- `ProgressBanner` overlays the session view with `Q3 of 9 · 11 min remaining` driven by engine-published participant attributes.
- 30 s with no agent participant → `DisconnectError` renders with `AGENT_NO_SHOW`.
- Camera/mic loss mid-session → `DisconnectError` with `MEDIA_LOST`.
- Backend `/start` 409 (token already used) → `DisconnectError` with `SESSION_ALREADY_STARTED`.
- Candidate clicks End Call → `CompletionScreen`.

### Graceful close (Phase 3)

- Engine reaches `Action.CLOSE` → publishes `session_outcome='completed'` → frontend reads attribute on `Disconnected` → `CompletionScreen`.
- Engine errors mid-session (config fetch fails, OpenAI hard error) → publishes `session_outcome='error'` → frontend renders `DisconnectError` with `ENGINE_ERROR`.
- Engine container killed without publishing outcome → frontend renders `DisconnectError` with `UNEXPECTED_DISCONNECT` (default for non-clean disconnect with no attribute).

### Reconnect / rejoin (Phase 2 + parts of Phase 1)

- Transient drop (WiFi switch) → `<ReconnectingOverlay />` covers the screen, session reconnects within 30 s, overlay clears, candidate continues from where they left off, outcome stays `'live'`.
- Reconnect fails after 30 s → `DisconnectError` with `RECONNECT_FAILED`.
- Hard refresh mid-session → wizard's pre-check returns `state='active'` → `<App mode='rejoin'>` mounts → `WelcomeView` shows rejoin copy → candidate clicks *Rejoin* → `/rejoin` mints fresh LiveKit JWT → reconnects to same room as same identity → engine still in room → `ProgressBanner` shows current Q index (no reset).
- Rejoin after engine has CLOSEd the session → `/rejoin` returns 409 → `DisconnectError` with `SESSION_ALREADY_COMPLETED`.
- Two tabs both rejoining → older tab gets `DUPLICATE_IDENTITY` → routes to `DisconnectError` with `DUPLICATE_SESSION`.
- `>5` rejoin attempts within an hour → `/rejoin` returns 429 → `DisconnectError` with `REJOIN_RATE_LIMITED`.
- Audit log entry created for every rejoin (action `candidate_session.rejoin`, with `actor_id`, `tenant_id`, `correlation_id`).

### CI / regression

- `npm run build`, `npm run lint`, `npm run type-check`, `npm run test` all pass.
- Backend: `pytest backend/nexus/tests/modules/session/test_rejoin.py` passes.
- Engine: `pytest backend/interview_engine/tests/test_graceful_close.py` passes.
- No regressions on the dashboard surface (no `components/{ui,agents-ui,ai-elements}/` imports outside `app/(interview)/`).
