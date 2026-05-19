'use client'

import { useSessionContext } from '@livekit/components-react'
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01'
import type { AppConfig } from '@/app-config'
import type { PreCheckResponse } from '@/lib/api/candidate-session'
import { AgentUIWithLoader } from '../agent-ui-with-loader'
import { CompletionScreen } from './CompletionScreen'
import { DisconnectError } from './DisconnectError'
import { ProgressBanner } from './ProgressBanner'
import { ReconnectingOverlay } from './ReconnectingOverlay'
import { WelcomeView } from './welcome-view'
import { useAgentGraceTimeout } from './hooks/use-agent-grace-timeout'

export type Outcome = 'live' | 'completed' | 'error'

interface Props {
  appConfig: AppConfig
  preCheck: PreCheckResponse
  mode: 'start' | 'rejoin'
  outcome: Outcome
  errorCode: string | null
  isStartPending: boolean
  onStart: () => void
  onError: (code: string) => void
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
}: Props) {
  const ctx = useSessionContext() as unknown as { isConnected?: boolean }
  const isConnected = !!ctx?.isConnected

  // 30s no-show timer — fires only after the agent has had a chance to join.
  // Hook always runs; it short-circuits internally if the agent appears in time.
  useAgentGraceTimeout(() => onError('AGENT_NO_SHOW'), { graceMs: 30_000 })

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
      />
    )
  }

  return (
    <AgentUIWithLoader>
      <ProgressBanner />
      <AgentSessionView_01
        supportsChatInput={appConfig.supportsChatInput}
        supportsVideoInput={appConfig.supportsVideoInput}
        supportsScreenShare={appConfig.supportsScreenShare}
        isPreConnectBufferEnabled={appConfig.isPreConnectBufferEnabled}
        audioVisualizerType={appConfig.audioVisualizerType}
        audioVisualizerColor={appConfig.audioVisualizerColor}
        audioVisualizerBarCount={appConfig.audioVisualizerBarCount}
        audioVisualizerGridRowCount={appConfig.audioVisualizerGridRowCount}
        audioVisualizerGridColumnCount={appConfig.audioVisualizerGridColumnCount}
        audioVisualizerRadialBarCount={appConfig.audioVisualizerRadialBarCount}
        audioVisualizerRadialRadius={appConfig.audioVisualizerRadialRadius}
        audioVisualizerWaveLineWidth={appConfig.audioVisualizerWaveLineWidth}
        className="fixed inset-0"
      />
      <ReconnectingOverlay onTimeout={() => onError('RECONNECT_FAILED')} />
    </AgentUIWithLoader>
  )
}
