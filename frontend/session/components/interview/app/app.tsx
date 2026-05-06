'use client'

import { Room, RoomEvent, TokenSource } from 'livekit-client'
import type { DisconnectReason } from '@livekit/protocol'
import { useSession } from '@livekit/components-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { AppConfig } from '@/app-config'
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider'
import { StartAudioButton } from '@/components/agents-ui/start-audio-button'
import {
  candidateSessionApi,
  type CandidateSessionError,
  type PreCheckResponse,
} from '@/lib/api/candidate-session'
import { toAudioCaptureOptions } from '@/lib/api/audio-hints'

import { useSessionOutcome } from './hooks/use-session-outcome'
import { type SessionOutcome } from '../lib/session-outcome'
import { ViewController, type Outcome } from './view-controller'

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

  // A single Room instance for the lifetime of this component. audioCaptureDefaults
  // is not set here — it's populated inside the async TokenSource callback below,
  // before returning creds, so the Room picks up the server-provided hints when
  // it publishes the microphone track after room.connect().
  const room = useMemo(() => new Room(), [])

  const setError = useCallback((code: string) => {
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

  const onCompleted = useCallback(() => setOutcome('completed'), [])

  const onStart = useCallback(() => {
    void session.start().catch(() => {
      // Already routed in TokenSource.custom; nothing else to do here.
    })
  }, [session])

  return (
    <AgentSessionProvider session={session}>
      <OutcomeWatcher
        room={session.room}
        onCompleted={onCompleted}
        onError={setError}
      />
      <ViewController
        appConfig={appConfig}
        preCheck={preCheck}
        mode={mode}
        outcome={outcome}
        errorCode={errorCode}
        isStartPending={isStartPending}
        onStart={onStart}
        onError={setError}
      />
      <StartAudioButton label="Start audio" />
    </AgentSessionProvider>
  )
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
