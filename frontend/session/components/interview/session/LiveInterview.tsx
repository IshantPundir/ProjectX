'use client'

import type { Room } from 'livekit-client'
import { useSessionContext, useSessionMessages, useVoiceAssistant } from '@livekit/components-react'

import { AuraStage } from './AuraStage'
import { InterviewSessionPanel } from './InterviewSessionPanel'
import { ProgressChip } from './ProgressChip'
import { SelfView } from './SelfView'
import { SessionTopBar } from './SessionTopBar'
import { SpokenCaption } from './SpokenCaption'
import { useEnsureMediaPublished } from './useEnsureMediaPublished'
import type { RawMessage } from './transcript-model'

export interface LiveInterviewProps {
  companyName: string
  jobTitle: string
  logo?: string
  /** Tenant accent (CSS color); applied to --px-accent on the surface root. */
  accent?: string
  onEnd: () => void
}

export function LiveInterview({ companyName, jobTitle, logo, accent, onEnd }: LiveInterviewProps) {
  const session = useSessionContext()
  const { messages } = useSessionMessages(session)
  const { state, audioTrack } = useVoiceAssistant()
  useEnsureMediaPublished(session.room as Room | undefined)

  const rawMessages = messages as unknown as RawMessage[]

  return (
    <div
      className="px-cine-bg fixed inset-0 overflow-hidden"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <SessionTopBar
        companyName={companyName}
        jobTitle={jobTitle}
        logo={logo}
        onEnd={onEnd}
        className="absolute inset-x-0 top-0 z-30 px-4 py-3"
      />

      <ProgressChip className="absolute left-1/2 top-16 z-20 -translate-x-1/2" />

      <div className="absolute inset-0 z-0 grid place-items-center">
        <AuraStage state={state} audioTrack={audioTrack} />
      </div>

      <SelfView className="absolute bottom-5 left-4 z-20" />

      <SpokenCaption
        messages={rawMessages}
        className="absolute bottom-6 left-1/2 z-20 -translate-x-1/2"
      />

      <InterviewSessionPanel
        messages={rawMessages}
        agentState={state}
        className="absolute right-4 top-16 z-30 max-h-[70vh] w-[min(360px,86vw)]"
      />
    </div>
  )
}
