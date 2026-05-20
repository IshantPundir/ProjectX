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

  // Dramatic staggered entrance: the session "reveals" on mount (after the loader
  // hands off) — the aura springs into place first, then the chrome cascades in.
  // Animates opacity + y/scale only (never x), so it never fights the layout's
  // -translate-x-1/2 centering. Frozen under prefers-reduced-motion.
  const reduce = useReducedMotion()
  const enter = (delay: number, from: { y?: number; scale?: number }) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, ...from },
          animate: { opacity: 1, y: 0, scale: 1 },
          transition: { type: 'spring' as const, stiffness: 180, damping: 20, delay },
        }

  return (
    <div
      className="px-cine-bg fixed inset-0 overflow-hidden"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      {/* Aura — hero entrance (springs up to scale first) */}
      <motion.div className="absolute inset-0 z-0 grid place-items-center" {...enter(0, { scale: 0.6 })}>
        <AuraStage state={state} audioTrack={audioTrack} />
      </motion.div>

      {/* Top bar — drops in */}
      <motion.div className="absolute inset-x-0 top-0 z-30 px-4 py-3" {...enter(0.28, { y: -32 })}>
        <SessionTopBar companyName={companyName} jobTitle={jobTitle} logo={logo} onEnd={onEnd} />
      </motion.div>

      {/* Progress chip — drops from the top (centered) */}
      <div className="absolute left-1/2 top-16 z-20 w-fit -translate-x-1/2">
        <motion.div {...enter(0.46, { y: -20 })}>
          <ProgressChip />
        </motion.div>
      </div>

      {/* Interview Session panel — rises in from the bottom-right */}
      <motion.div className="absolute bottom-5 right-4 z-30" {...enter(0.56, { y: 28, scale: 0.96 })}>
        <InterviewSessionPanel messages={rawMessages} className="max-h-[70vh] w-[min(360px,86vw)]" />
      </motion.div>

      {/* Self-view — rises in from the bottom-left */}
      <motion.div className="absolute bottom-5 left-4 z-20 w-fit" {...enter(0.62, { y: 28, scale: 0.9 })}>
        <SelfView />
      </motion.div>

      {/* Spoken caption — rises in (centered) */}
      <div className="absolute bottom-6 left-1/2 z-20 w-fit -translate-x-1/2">
        <motion.div {...enter(0.7, { y: 28 })}>
          <SpokenCaption messages={rawMessages} />
        </motion.div>
      </div>
    </div>
  )
}
