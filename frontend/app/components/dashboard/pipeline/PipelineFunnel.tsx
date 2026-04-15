'use client'

import { ChevronDown, Users } from 'lucide-react'
import type { PipelineStageInput } from '@/lib/api/pipelines'
import { StageSlab } from './StageSlab'

// Accept either PipelineStageInput (scratch stages with no backend id yet)
// or the saved variant with a stable UUID. `id` is only used for React keys
// below — the render code doesn't depend on it.
type FunnelStage = PipelineStageInput & { id?: string }

type Props = {
  stages: FunnelStage[]
  onStageClick?: (index: number) => void
  onStageDelete?: (index: number) => void
  selectedIndex?: number
}

export function PipelineFunnel({ stages, onStageClick, onStageDelete, selectedIndex }: Props) {
  const hasStages = stages.length > 0
  // Width narrows from 100% at top to 50% at the bottom, linear interpolation.
  // Handles single-stage case via Math.max guard.
  const widthFor = (i: number) => 100 - (i * (50 / Math.max(stages.length - 1, 1)))

  return (
    <div className="flex flex-col items-center gap-2 py-4">
      {/* Top pill — Applications */}
      {hasStages && (
        <>
          <div className="w-full max-w-[600px] bg-gradient-to-b from-blue-50 to-blue-100/50 border border-blue-200 rounded-xl px-5 py-2.5 flex items-center justify-between shadow-sm">
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4 text-blue-600" />
              <div>
                <div className="text-sm font-semibold text-blue-900">Applications</div>
                <div className="text-[11px] text-blue-600/70">Top of funnel — candidate pool</div>
              </div>
            </div>
          </div>
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" />
        </>
      )}

      {stages.map((stage, i) => {
        const width = widthFor(i)
        return (
          <div key={stage.id ?? `scratch-${i}`} className="w-full flex flex-col items-center">
            <div
              style={{ width: `${width}%`, maxWidth: '600px', minWidth: '280px' }}
              className="transition-all duration-300"
            >
              <StageSlab
                stage={stage}
                position={i + 1}
                selected={selectedIndex === i}
                onClick={() => onStageClick?.(i)}
                onDelete={onStageDelete ? () => onStageDelete(i) : undefined}
              />
            </div>
            {i < stages.length - 1 && (
              <ChevronDown className="w-4 h-4 text-zinc-300 my-1.5" aria-hidden="true" />
            )}
          </div>
        )
      })}

      {/* Bottom pill — Offers */}
      {hasStages && (
        <>
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" />
          <div
            className="bg-gradient-to-b from-emerald-50 to-emerald-100/50 border border-emerald-200 rounded-xl px-5 py-2.5 flex items-center gap-2 shadow-sm"
            style={{ width: '50%', maxWidth: '300px', minWidth: '240px' }}
          >
            <div className="flex-1 text-center">
              <div className="text-sm font-semibold text-emerald-900">Offers</div>
              <div className="text-[11px] text-emerald-700/70">Hired candidates</div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
