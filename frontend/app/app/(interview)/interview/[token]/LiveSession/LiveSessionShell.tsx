'use client'

import '@livekit/components-styles'
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react'
import { useCallback, useState } from 'react'

import { AgentTile } from './AgentTile'
import { CandidateSelfView } from './CandidateSelfView'
import { CompletionScreen } from './CompletionScreen'
import { DisconnectError } from './DisconnectError'
import { ProgressBanner } from './ProgressBanner'
import { TranscriptPane } from './TranscriptPane'
import { useAgentGraceTimeout } from './hooks/use-agent-grace-timeout'

interface Props {
  livekitUrl: string
  livekitToken: string
  roomName: string
}

type Outcome = 'active' | 'completed' | 'error'

export function LiveSessionShell({ livekitUrl, livekitToken, roomName }: Props) {
  const [outcome, setOutcome] = useState<Outcome>('active')
  const [errorCode, setErrorCode] = useState<string | null>(null)

  const handleMediaLost = useCallback(() => {
    setErrorCode('MEDIA_LOST')
    setOutcome('error')
  }, [])

  const handleNoShow = useCallback(() => {
    setErrorCode('AGENT_NO_SHOW')
    setOutcome('error')
  }, [])

  if (outcome === 'completed') return <CompletionScreen />
  if (outcome === 'error' && errorCode) return <DisconnectError code={errorCode} />

  return (
    <LiveKitRoom
      serverUrl={livekitUrl}
      token={livekitToken}
      connect
      audio
      video
      onDisconnected={() => setOutcome('completed')}
      data-room-name={roomName}
    >
      <RoomAudioRenderer />
      <ProgressBanner />
      <main className="grid grid-cols-1 md:grid-cols-2 gap-4 p-6">
        <AgentTile />
        <CandidateSelfView onMediaLost={handleMediaLost} />
      </main>
      <GraceTimeoutBoundary onNoShow={handleNoShow} />
      <TranscriptPane />
    </LiveKitRoom>
  )
}

function GraceTimeoutBoundary({ onNoShow }: { onNoShow: () => void }) {
  useAgentGraceTimeout(onNoShow, { graceMs: 30_000 })
  return null
}
