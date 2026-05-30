'use client'

import { Filmstrip } from './Filmstrip'
import { IntegrityLane } from './IntegrityLane'
import { NodeTrack } from './NodeTrack'
import type { FlagMarker, TimelineMarker } from './timeline-model'

export function SessionTimeline({
  markers,
  flags,
  downBuckets,
  offBuckets,
  integrityCaption,
  playheadPct,
  activeQuestionId,
  onSelectQuestion,
  onSeekMs,
  onSelectFlag,
}: {
  markers: TimelineMarker[]
  flags: FlagMarker[]
  downBuckets: number[]
  offBuckets: number[]
  integrityCaption: string
  playheadPct: number
  activeQuestionId: string | null
  onSelectQuestion: (questionId: string) => void
  onSeekMs: (ms: number) => void
  onSelectFlag: (flag: FlagMarker) => void
}) {
  return (
    <div className="theater-glass rounded-2xl p-3">
      <Filmstrip markers={markers} activeQuestionId={activeQuestionId} onSelect={onSelectQuestion} />
      <NodeTrack
        markers={markers}
        playheadPct={playheadPct}
        activeQuestionId={activeQuestionId}
        onSeekMs={onSeekMs}
      />
      <IntegrityLane
        downBuckets={downBuckets}
        offBuckets={offBuckets}
        flags={flags}
        caption={integrityCaption}
        onSelectFlag={onSelectFlag}
      />
    </div>
  )
}
