'use client'

import { formatTimestamp } from '../report-format'
import { gamma, type FlagMarker } from './timeline-model'
import './theater.css'

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

const DOWN_KIND = 'down_glance'

function bucketAlpha(v: number): number {
  return 0.12 + gamma(v) * 0.85
}

function Lane({
  label,
  color,
  buckets,
  flags,
  onSelectFlag,
}: {
  label: string
  color: string
  buckets: number[]
  flags: FlagMarker[]
  onSelectFlag: (f: FlagMarker) => void
}) {
  return (
    <div>
      <div
        className="mb-0.5 text-[8.5px] font-bold uppercase tracking-wide"
        style={{ color: 'var(--px-fg-4)' }}
      >
        {label}
      </div>
      <div
        className="relative flex h-[14px] overflow-visible rounded"
        style={{ background: 'rgba(255,255,255,0.06)' }}
      >
        {buckets.map((v, i) => (
          <div key={i} style={{ flex: 1, height: '100%', background: color, opacity: bucketAlpha(v) }} />
        ))}
        {flags.map((f, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onSelectFlag(f)}
            aria-label={`${KIND_LABEL[f.kind] ?? f.kind} at ${formatTimestamp(f.startMs)}`}
            className="theater-flagtick"
            style={{ left: `${f.positionPct}%`, background: color }}
          >
            {f.thumbnailUrl && (
              <span className="theater-flagtip">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={f.thumbnailUrl} alt="" className="block w-full" />
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

export function IntegrityLane({
  downBuckets,
  offBuckets,
  flags,
  caption,
  onSelectFlag,
}: {
  downBuckets: number[]
  offBuckets: number[]
  flags: FlagMarker[]
  caption: string
  onSelectFlag: (flag: FlagMarker) => void
}) {
  if (downBuckets.length === 0 && offBuckets.length === 0 && flags.length === 0) return null
  const downFlags = flags.filter((f) => f.kind === DOWN_KIND)
  const offFlags = flags.filter((f) => f.kind !== DOWN_KIND)
  return (
    <div className="mt-2 space-y-1.5">
      <Lane
        label="Down-glances"
        color="var(--px-caution-fill)"
        buckets={downBuckets}
        flags={downFlags}
        onSelectFlag={onSelectFlag}
      />
      <Lane
        label="Off-screen"
        color="var(--px-danger-fill)"
        buckets={offBuckets}
        flags={offFlags}
        onSelectFlag={onSelectFlag}
      />
      {caption && (
        <div className="pt-0.5 text-[10px] font-bold" style={{ color: 'var(--px-danger)' }}>
          {caption}
        </div>
      )}
    </div>
  )
}
