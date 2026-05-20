'use client'

import { motion, useReducedMotion } from 'motion/react'
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

  // Staggered entrance: each section fades in on mount, so the session "reveals"
  // after the loader hands off. Opacity-only so it never fights the absolute /
  // translate positioning of the layers. Frozen under prefers-reduced-motion.
  const reduce = useReducedMotion()
  const reveal = (delay: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0 },
          animate: { opacity: 1 },
          transition: { duration: 0.5, delay, ease: 'easeOut' as const },
        }

  return (
    <div
      className="px-cine-bg fixed inset-0 overflow-hidden"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <motion.div className="absolute inset-0 z-0 grid place-items-center" {...reveal(0)}>
        <AuraStage state={state} audioTrack={audioTrack} />
      </motion.div>

      <motion.div className="absolute inset-x-0 top-0 z-30 px-4 py-3" {...reveal(0.12)}>
        <SessionTopBar companyName={companyName} jobTitle={jobTitle} logo={logo} onEnd={onEnd} />
      </motion.div>

      <motion.div className="absolute left-1/2 top-16 z-20 w-fit -translate-x-1/2" {...reveal(0.24)}>
        <ProgressChip />
      </motion.div>

      <motion.div className="absolute right-4 top-16 z-30" {...reveal(0.3)}>
        <InterviewSessionPanel messages={rawMessages} className="max-h-[70vh] w-[min(360px,86vw)]" />
      </motion.div>

      <motion.div className="absolute bottom-5 left-4 z-20 w-fit" {...reveal(0.36)}>
        <SelfView />
      </motion.div>

      <motion.div className="absolute bottom-6 left-1/2 z-20 w-fit -translate-x-1/2" {...reveal(0.42)}>
        <SpokenCaption messages={rawMessages} />
      </motion.div>
    </div>
  )
}
