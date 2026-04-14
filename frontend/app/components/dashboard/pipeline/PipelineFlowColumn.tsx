'use client'

import { useRef } from 'react'
import { ChevronDown, Plus, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StageFlowCard } from './StageFlowCard'
import type { PipelineStageUpdateInput } from '@/lib/api/pipelines'
import type { BankResponse } from '@/lib/api/question-banks'

type Props = {
  stages: PipelineStageUpdateInput[]
  selectedStageId: string | null
  banks: BankResponse[]
  onStageClick: (stageId: string) => void
  onStageDelete?: (index: number) => void
  onAddStage: () => void
}

export function PipelineFlowColumn({
  stages,
  selectedStageId,
  banks,
  onStageClick,
  onStageDelete,
  onAddStage,
}: Props) {
  const columnRef = useRef<HTMLDivElement>(null)

  return (
    <div
      ref={columnRef}
      className="w-[400px] flex-shrink-0 border-r border-zinc-200 bg-white rounded-l-xl overflow-y-auto"
    >
      <div className="px-5 py-4 border-b border-zinc-100 sticky top-0 bg-white/95 backdrop-blur z-10">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
          Pipeline Flow
        </div>
      </div>

      <div className="px-5 py-6 flex flex-col items-center gap-2">
        {/* Applications pill (top) */}
        <div className="w-full max-w-[340px] bg-gradient-to-b from-blue-50 to-blue-100/50 border border-blue-200 rounded-xl px-4 py-2.5 flex items-center gap-2 shadow-sm">
          <Users
            className="w-4 h-4 text-blue-600 flex-shrink-0"
            aria-hidden="true"
          />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-blue-900">
              Applications
            </div>
            <div className="text-[11px] text-blue-600/70">
              Top of funnel — candidate pool
            </div>
          </div>
        </div>

        {stages.length > 0 && (
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" />
        )}

        {/* Stage cards */}
        {stages.map((stage, i) => {
          const stageId = stage.id ?? null
          const bank = stageId
            ? banks.find((b) => b.stage_id === stageId)
            : null
          const selected = stageId !== null && stageId === selectedStageId
          return (
            <div
              key={`${i}-${stageId ?? 'new'}`}
              className="w-full flex flex-col items-center"
            >
              <StageFlowCard
                stage={stage}
                position={i + 1}
                selected={selected}
                bankStatus={bank?.status ?? null}
                onClick={() => stageId && onStageClick(stageId)}
                onDelete={onStageDelete ? () => onStageDelete(i) : undefined}
              />
              {i < stages.length - 1 && (
                <ChevronDown
                  className="w-4 h-4 text-zinc-300 my-1.5"
                  aria-hidden="true"
                />
              )}
            </div>
          )
        })}

        {stages.length > 0 && (
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" />
        )}

        {/* Offers pill (bottom) */}
        <div className="w-full max-w-[280px] bg-gradient-to-b from-emerald-50 to-emerald-100/50 border border-emerald-200 rounded-xl px-4 py-2.5 flex items-center gap-2 shadow-sm">
          <div className="flex-1 text-center">
            <div className="text-sm font-semibold text-emerald-900">Offers</div>
            <div className="text-[11px] text-emerald-700/70">
              Hired candidates
            </div>
          </div>
        </div>

        {/* Add stage button */}
        <Button variant="outline" size="sm" onClick={onAddStage} className="mt-4">
          <Plus className="w-3.5 h-3.5 mr-1" />
          Add stage
        </Button>
      </div>
    </div>
  )
}
