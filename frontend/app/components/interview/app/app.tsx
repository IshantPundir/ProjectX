'use client'

import { ThemeProvider } from 'next-themes'
import { TokenSource } from 'livekit-client'
import { RoomEvent } from 'livekit-client'
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

import { useSessionOutcome } from './hooks/use-session-outcome'
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
    [token, mode, setError],
  )

  const session = useSession(tokenSource)

  // Engine-published session_outcome attribute. Held in a ref so it survives
  // the agent participant's removal at the moment of disconnect.
  const lastOutcome = useSessionOutcome()

  // Listen for disconnects on the underlying room and route to the right
  // outcome. SessionEvent doesn't expose Disconnected directly; the room does.
  const lastOutcomeRef = useRef(lastOutcome)
  lastOutcomeRef.current = lastOutcome

  useEffect(() => {
    const room = session.room
    if (!room) return

    const onDisconnected = (reason?: DisconnectReason) => {
      const o = lastOutcomeRef.current
      if (o === 'completed') {
        setOutcome('completed')
        return
      }
      if (o === 'error') {
        setError('ENGINE_ERROR')
        return
      }
      // No engine-published outcome — fall back to the disconnect reason.
      const reasonName = reasonToName(reason)
      if (reasonName === 'CLIENT_INITIATED') {
        setOutcome('completed')
      } else if (reasonName === 'DUPLICATE_IDENTITY') {
        setError('DUPLICATE_SESSION')
      } else {
        setError('UNEXPECTED_DISCONNECT')
      }
    }

    room.on(RoomEvent.Disconnected, onDisconnected)
    return () => {
      room.off(RoomEvent.Disconnected, onDisconnected)
    }
  }, [session.room, setError])

  const onStart = useCallback(() => {
    void session.start().catch(() => {
      // Already routed in TokenSource.custom; nothing else to do here.
    })
  }, [session])

  return (
    <ThemeProvider attribute="class" forcedTheme="light">
      <AgentSessionProvider session={session}>
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
    </ThemeProvider>
  )
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
