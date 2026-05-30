'use client'

import type { RiskBand } from '@/lib/api/reports'
import { formatTimestamp } from '../report-format'
import type { FlagMarker } from './timeline-model'
import './theater.css'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export function IntegrityLane({
  buckets,
  flags,
  riskBand,
  caption,
  onSelectFlag,
}: {
  buckets: number[]
  flags: FlagMarker[]
  riskBand: RiskBand | null
  caption: string
  onSelectFlag: (flag: FlagMarker) => void
}) {
  if (buckets.length === 0 && flags.length === 0) return null
  const riskText =
    riskBand === 'high' ? 'high risk' : riskBand === 'medium' ? 'medium risk' : 'integrity'
  return (
    <div className="mx-1 mt-2">
      <div className="relative flex h-[14px] overflow-hidden rounded" style={{ background: 'rgba(20,40,60,0.05)' }}>
        {buckets.map((v, i) => (
          <div key={i} style={{ flex: 1, height: '100%', background: `rgba(229,85,107,${0.12 + v * 0.7})` }} />
        ))}
        {flags.map((f, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onSelectFlag(f)}
            aria-label={`${KIND_LABEL[f.kind] ?? f.kind} at ${formatTimestamp(f.startMs)}`}
            className="absolute top-0 h-full w-[3px] -translate-x-1/2"
            style={{ left: `${f.positionPct}%`, background: 'var(--px-danger)' }}
          />
        ))}
      </div>
      <div className="mt-1 flex items-center justify-between text-[9.5px]">
        <span className="font-bold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
          ⚠ Integrity · {riskText}
        </span>
        {caption && <span className="font-bold" style={{ color: 'var(--px-danger)' }}>{caption}</span>}
      </div>
    </div>
  )
}
