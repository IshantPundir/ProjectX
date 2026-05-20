'use client'

import { useSessionContext } from '@livekit/components-react'
import type { AppConfig } from '@/app-config'
import type { PreCheckResponse, ProctoringConfig } from '@/lib/api/candidate-session'
import { AgentUIWithLoader } from '../agent-ui-with-loader'
import { LiveInterview } from '../session/LiveInterview'
import { CompletionScreen } from './CompletionScreen'
import { DisconnectError } from './DisconnectError'
import { ReconnectingOverlay } from './ReconnectingOverlay'
import { WelcomeView } from './welcome-view'
import { useAgentGraceTimeout } from './hooks/use-agent-grace-timeout'
import { ProctoringGuard } from '../proctoring/ProctoringGuard'
import { ProctoringEndedScreen } from './ProctoringEndedScreen'
import type { ProctoringTermination } from '../proctoring/violation-kinds'

export type Outcome = 'live' | 'completed' | 'error' | 'proctoring_terminated'

interface Props {
  appConfig: AppConfig
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
  outcome: Outcome
  errorCode: string | null
  isStartPending: boolean
  onStart: () => void
  onError: (code: string) => void
  token: string
  proctoring: ProctoringConfig | null
  proctoringReason: string | null
  onProctoringTerminated: (reason: ProctoringTermination) => void
}

export function ViewController({
  appConfig,
  preCheck,
  mode,
  outcome,
  errorCode,
  isStartPending,
  onStart,
  onError,
  token,
  proctoring,
  proctoringReason,
  onProctoringTerminated,
}: Props) {
  const ctx = useSessionContext() as unknown as { isConnected?: boolean; end?: () => void }
  const isConnected = !!ctx?.isConnected

  // 30s no-show timer — fires only after the agent has had a chance to join.
  // Hook always runs; it short-circuits internally if the agent appears in time.
  useAgentGraceTimeout(() => onError('AGENT_NO_SHOW'), { graceMs: 30_000 })

  if (outcome === 'proctoring_terminated') {
    return <ProctoringEndedScreen reason={proctoringReason} />
  }
  if (outcome === 'completed') return <CompletionScreen />
  if (outcome === 'error' && errorCode) {
    return <DisconnectError code={errorCode} />
  }

  if (!isConnected) {
    return (
      <WelcomeView
        companyName={appConfig.companyName}
        jobTitle={preCheck.job_title}
        durationMinutes={preCheck.duration_minutes}
        startButtonText={appConfig.startButtonText}
        mode={mode}
        onStartCall={onStart}
        isPending={isStartPending}
        proctored={preCheck.proctoring_enabled}
      />
    )
  }

  return (
    <AgentUIWithLoader>
      <ProctoringGuard token={token} config={proctoring} onTerminated={onProctoringTerminated}>
        <LiveInterview
          companyName={appConfig.companyName}
          jobTitle={preCheck.job_title}
          logo={appConfig.logo}
          accent={appConfig.accent}
          onEnd={() => ctx.end?.()}
        />
      </ProctoringGuard>
      <ReconnectingOverlay onTimeout={() => onError('RECONNECT_FAILED')} />
    </AgentUIWithLoader>
  )
}
