'use client'

import type { PipelineStageInput } from '@/lib/api/pipelines'
import { StageSlab } from './StageSlab'

type Props = {
  stages: PipelineStageInput[]
  onStageClick?: (index: number) => void
  selectedIndex?: number
}

export function PipelineFunnel({ stages, onStageClick, selectedIndex }: Props) {
  return (
    <div className="flex flex-col items-center gap-3 py-4">
      {stages.map((stage, i) => {
        // Funnel width narrows from top to bottom: 100% at top, 60% at bottom
        const width = 100 - (i * (40 / Math.max(stages.length - 1, 1)))
        return (
          <div
            key={`${i}-${stage.name}`}
            style={{ width: `${width}%`, maxWidth: '600px' }}
            className="relative"
          >
            <StageSlab
              stage={stage}
              selected={selectedIndex === i}
              onClick={() => onStageClick?.(i)}
            />
            {i < stages.length - 1 && (
              <div className="flex justify-center mt-1 text-zinc-400 text-lg">↓</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
