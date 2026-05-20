'use client'

import { Room, RoomEvent, TokenSource } from 'livekit-client'
import type { DisconnectReason } from '@livekit/protocol'
import { useSession } from '@livekit/components-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import type { AppConfig } from '@/app-config'
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider'
import { StartAudioButton } from '@/components/agents-ui/start-audio-button'
import {
  candidateSessionApi,
  type CandidateSessionError,
  type PreCheckResponse,
  type ProctoringConfig,
} from '@/lib/api/candidate-session'
import { toAudioCaptureOptions } from '@/lib/api/audio-hints'
import type { ProctoringTermination } from '../proctoring/violation-kinds'

import { useSessionOutcome } from './hooks/use-session-outcome'
import { useSessionStateFallback } from './hooks/use-session-state-fallback'
import { type SessionOutcome } from '../lib/session-outcome'
import { ViewController, type Outcome } from './view-controller'
import { SessionErrorScreen } from './session-error-screen'

interface Props {
  appConfig: AppConfig
  token: string
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
}

/**
 * Candidate-facing live session shell. Mounts after the wizard's cam-mic step
 * (mode='start') or directly when the pre-check returns state='active'
 * (mode='rejoin').
 *
 * Owns the outcome state and the TokenSource.custom callback that mints LiveKit
 * credentials on demand. The mint runs exactly once — credentials are cached in
 * a ref so React Strict Mode double-invoke or hot reload doesn't trigger a second
 * POST (which would 409 because /start is atomically single-use).
 */
export function App({ appConfig, token, preCheck, mode }: Props) {
  const [outcome, setOutcome] = useState<Outcome>('live')
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const [isStartPending, setIsStartPending] = useState(false)
  const credsRef = useRef<{ serverUrl: string; participantToken: string } | null>(null)
  const [proctoring, setProctoring] = useState<ProctoringConfig | null>(null)
  const [proctoringReason, setProctoringReason] = useState<string | null>(null)
  const proctoringTerminatedRef = useRef(false)

  // A single Room instance for the lifetime of this component. audioCaptureDefaults
  // is not set here — it's populated inside the async TokenSource callback below,
  // before returning creds, so the Room picks up the server-provided hints when
  // it publishes the microphone track after room.connect().
  const room = useMemo(() => new Room(), [])

  const setError = useCallback((code: string) => {
    if (proctoringTerminatedRef.current) return
    setErrorCode(code)
    setOutcome('error')
  }, [])

  const tokenSource = useMemo(
    () =>
      TokenSource.custom(async () => {
        if (credsRef.current) return credsRef.current
        try {
          setIsStartPending(true)
          const creds =
            mode === 'rejoin'
              ? await candidateSessionApi.rejoin(token)
              : await candidateSessionApi.start(token)

          // Apply server-provided audio hints before returning creds so the room
          // picks them up when it acquires the microphone track during room.connect().
          // This mutation is intentional: it happens in an async callback (not during
          // render). The server is the source of truth — Cloud mode sets
          // noise_suppression=false so the ML model sees raw audio; EC and AGC stay
          // ON in both modes.
          const hints = creds.audio_processing_hints
          if (!hints) {
            console.warn(
              '[interview] /start response missing audio_processing_hints; falling back to safe defaults',
            )
          }
          // eslint-disable-next-line react-hooks/immutability
          room.options.audioCaptureDefaults = toAudioCaptureOptions(
            hints ?? {
              noise_suppression: true,
              echo_cancellation: true,
              auto_gain_control: true,
            },
          )

          setProctoring(creds.proctoring ?? null)
          credsRef.current = {
            serverUrl: creds.livekit_url,
            participantToken: creds.livekit_token,
          }
          return credsRef.current
        } catch (err) {
          const ce = err as CandidateSessionError
          if (mode === 'start' && (ce?.status === 409 || ce?.code === 'TOKEN_ALREADY_USED')) {
            setError('SESSION_ALREADY_STARTED')
          } else if (mode === 'rejoin' && ce?.status === 409) {
            setError('SESSION_ALREADY_COMPLETED')
          } else if (mode === 'rejoin' && ce?.status === 401) {
            setError('TOKEN_EXPIRED')
          } else if (mode === 'rejoin' && ce?.status === 429) {
            setError('REJOIN_RATE_LIMITED')
          } else if (mode === 'rejoin') {
            setError('REJOIN_REJECTED')
          } else {
            setError('SESSION_START_FAILED')
          }
          throw err
        } finally {
          setIsStartPending(false)
        }
      }),
    [token, mode, room, setError],
  )

  const session = useSession(tokenSource, { room })

  const onCompleted = useCallback(() => {
    if (proctoringTerminatedRef.current) return
    setOutcome('completed')
  }, [])

  const onProctoringTerminated = useCallback((reason: ProctoringTermination) => {
    proctoringTerminatedRef.current = true
    setProctoringReason(reason)
    setOutcome('proctoring_terminated')
  }, [setProctoringReason])

  const onStart = useCallback(() => {
    if (preCheck.proctoring_enabled && document.fullscreenElement == null) {
      void document.documentElement.requestFullscreen?.().catch(() => {})
    }
    void session.start().catch(() => {
      // Already routed in TokenSource.custom; nothing else to do here.
    })
  }, [session, preCheck.proctoring_enabled])

  return (
    <AgentSessionProvider session={session}>
      <OutcomeWatcher
        room={session.room}
        onCompleted={onCompleted}
        onError={setError}
      />
      <OutcomePrecedenceController
        token={token}
        sessionId={preCheck.session_id}
      >
        <ViewController
          appConfig={appConfig}
          preCheck={preCheck}
          mode={mode}
          outcome={outcome}
          errorCode={errorCode}
          isStartPending={isStartPending}
          onStart={onStart}
          onError={setError}
          token={token}
          proctoring={proctoring}
          proctoringReason={proctoringReason}
          onProctoringTerminated={onProctoringTerminated}
        />
      </OutcomePrecedenceController>
      <StartAudioButton label="Start audio" />
    </AgentSessionProvider>
  )
}

/**
 * Lives inside AgentSessionProvider (LiveKit context required for
 * useSessionOutcome). Applies the engine-failure precedence rule:
 *
 *   1. If the LK room attribute `session_outcome='error'` is observed,
 *      render <SessionErrorScreen errorCode={null}> (no code from LK path).
 *   2. Else if the HTTP /state poll returns state='error', render
 *      <SessionErrorScreen errorCode={fallbackState.error_code}> (full code
 *      from the DB).
 *   3. Otherwise render {children} — the live-session ViewController.
 *
 * Both paths surface the same screen; only the error_code differs (null vs
 * the backend taxonomy string). Prefer the polled code when available because
 * it carries the full taxonomy.
 */
export function OutcomePrecedenceController({
  token,
  sessionId,
  children,
}: {
  token: string
  sessionId: string
  children: ReactNode
}) {
  const lkOutcome = useSessionOutcome()
  const fallbackState = useSessionStateFallback(token, /* enabled */ true)

  if (lkOutcome === 'error') {
    // LK attribute arrived: no error_code on this path (the attribute carries
    // only the outcome string). Prefer the polled code if it has also arrived —
    // that way if both paths surface simultaneously the candidate sees specific
    // copy rather than the generic fallback.
    const errorCode =
      fallbackState?.state === 'error' ? fallbackState.error_code : null
    return <SessionErrorScreen errorCode={errorCode} sessionId={sessionId} />
  }

  if (fallbackState?.state === 'error') {
    // HTTP poll surfaced the error before (or instead of) the LK attribute.
    return (
      <SessionErrorScreen
        errorCode={fallbackState.error_code}
        sessionId={sessionId}
      />
    )
  }

  return <>{children}</>
}

/**
 * Lives inside AgentSessionProvider so the LiveKit hooks (which require Room
 * context) work. Captures the engine-published `session_outcome` attribute and
 * subscribes to room.on(Disconnected) to route between completed and error
 * outcomes. The agent's published outcome takes precedence; falls back to
 * DisconnectReason when the engine didn't publish anything (crash, network drop).
 */
export function OutcomeWatcher({
  room,
  onCompleted,
  onError,
}: {
  room: Room | undefined
  onCompleted: () => void
  onError: (code: string) => void
}) {
  const lastOutcome = useSessionOutcome()
  const lastOutcomeRef = useRef<SessionOutcome | null>(lastOutcome)
  lastOutcomeRef.current = lastOutcome

  // Proactively leave the LiveKit room as soon as the engine publishes
  // a session_outcome attribute. Without this the candidate would stay
  // connected after the agent disconnected, keeping the room non-empty
  // until the agent's TTL or the candidate closes the tab — which keeps
  // the LiveKit dashboard "Active" and the worker process alive longer
  // than necessary. Calling disconnect() here triggers the existing
  // RoomEvent.Disconnected handler below, which routes by outcome.
  useEffect(() => {
    if (!room || !lastOutcome) return
    void room.disconnect()
  }, [room, lastOutcome])

  useEffect(() => {
    if (!room) return

    const onDisconnected = (reason?: DisconnectReason) => {
      const o = lastOutcomeRef.current
      switch (o) {
        case 'completed':
        case 'knockout_closed':
        case 'time_expired':
        case 'candidate_ended':
          return onCompleted()
        case 'candidate_unresponsive':
          return onError('CANDIDATE_UNRESPONSIVE')
        case 'error':
          return onError('ENGINE_ERROR')
        case null:
          break // fall through to DisconnectReason mapping
        default: {
          // Compile-time guard: if a future SessionOutcome is added without a
          // corresponding case, TypeScript will error here because `o` would
          // no longer narrow to `never`.
          const _exhaustive: never = o
          void _exhaustive
        }
      }

      const reasonName = reasonToName(reason)
      if (reasonName === 'CLIENT_INITIATED') return onCompleted()
      if (reasonName === 'DUPLICATE_IDENTITY') return onError('DUPLICATE_SESSION')
      onError('UNEXPECTED_DISCONNECT')
    }

    room.on(RoomEvent.Disconnected, onDisconnected)
    return () => {
      room.off(RoomEvent.Disconnected, onDisconnected)
    }
  }, [room, onCompleted, onError])

  return null
}

/**
 * Map DisconnectReason (proto enum integer) to a name we can branch on.
 * The numeric values come from @livekit/protocol; we name the two we care about
 * and fall back to UNKNOWN for everything else.
 */
function reasonToName(reason?: DisconnectReason): string {
  // DisconnectReason: CLIENT_INITIATED=1, DUPLICATE_IDENTITY=2, ...
  // Reading the numeric values directly avoids importing the enum at runtime.
  switch (reason) {
    case 1:
      return 'CLIENT_INITIATED'
    case 2:
      return 'DUPLICATE_IDENTITY'
    default:
      return 'UNKNOWN'
  }
}
