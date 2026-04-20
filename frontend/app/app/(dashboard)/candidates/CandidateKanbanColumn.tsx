'use client'

import { useDroppable } from '@dnd-kit/core'

import type { KanbanColumn } from '@/lib/api/candidates'

import CandidateKanbanCard from './CandidateKanbanCard'

interface Props {
  stage: KanbanColumn
  stages: KanbanColumn[]
  jobPostingId: string
}

export default function CandidateKanbanColumn({
  stage,
  stages,
  jobPostingId,
}: Props) {
  const { setNodeRef, isOver } = useDroppable({
    id: stage.stage_id,
    data: { stageId: stage.stage_id },
  })

  return (
    <div
      ref={setNodeRef}
      className={`flex w-80 flex-shrink-0 flex-col rounded-xl border bg-zinc-50/60 transition-colors ${
        isOver ? 'border-blue-400 bg-blue-50/60' : 'border-zinc-200'
      }`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-zinc-200 px-4 py-3">
        <h3 className="truncate text-sm font-semibold text-zinc-900">
          {stage.stage_name}
        </h3>
        <span
          className="inline-flex min-w-[1.5rem] items-center justify-center rounded-full bg-zinc-200 px-2 py-0.5 text-xs font-medium text-zinc-700"
          aria-label={`${stage.candidates.length} candidates in ${stage.stage_name}`}
        >
          {stage.candidates.length}
        </span>
      </header>

      <div className="flex min-h-[4rem] flex-col gap-2 overflow-y-auto p-3">
        {stage.candidates.length === 0 ? (
          <p className="py-6 text-center text-xs text-zinc-400">
            Drop candidates here
          </p>
        ) : (
          stage.candidates.map((card) => (
            <CandidateKanbanCard
              key={card.assignment_id}
              card={card}
              jobPostingId={jobPostingId}
              stages={stages}
            />
          ))
        )}
      </div>
    </div>
  )
}
