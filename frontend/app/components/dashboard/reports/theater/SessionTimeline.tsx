'use client'

import type { RiskBand } from '@/lib/api/reports'
import { Filmstrip } from './Filmstrip'
import { IntegrityLane } from './IntegrityLane'
import { NodeTrack } from './NodeTrack'
import type { FlagMarker, TimelineMarker } from './timeline-model'

export function SessionTimeline({
  markers,
  flags,
  buckets,
  riskBand,
  integrityCaption,
  playheadPct,
  activeQuestionId,
  onSelectQuestion,
  onSeekMs,
  onSelectFlag,
}: {
  markers: TimelineMarker[]
  flags: FlagMarker[]
  buckets: number[]
  riskBand: RiskBand | null
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
        buckets={buckets}
        flags={flags}
        riskBand={riskBand}
        caption={integrityCaption}
        onSelectFlag={onSelectFlag}
      />
    </div>
  )
}
