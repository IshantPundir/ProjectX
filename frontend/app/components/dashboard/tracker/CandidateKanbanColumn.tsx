'use client'

import { useDroppable } from '@dnd-kit/core'

import type { KanbanColumn } from '@/lib/api/candidates'

import CandidateKanbanCard from './CandidateKanbanCard'

interface Props {
  stage: KanbanColumn
}

export default function CandidateKanbanColumn({ stage }: Props) {
  const { setNodeRef, isOver } = useDroppable({
    id: stage.stage_id,
    data: { stageId: stage.stage_id },
  })

  return (
    <div
      ref={setNodeRef}
      className="flex h-full w-80 flex-shrink-0 flex-col rounded-lg border transition-colors"
      style={{
        background: 'var(--px-bg-2)',
        borderColor: isOver ? 'var(--px-accent)' : 'var(--px-hairline)',
        boxShadow: isOver ? '0 0 0 1px var(--px-accent-line)' : undefined,
        minHeight: 0,
      }}
    >
      <header
        className="flex items-center gap-2 px-3"
        style={{ padding: '10px 12px 8px' }}
      >
        <h3
          className="m-0 truncate text-[11.5px] font-semibold uppercase"
          style={{ letterSpacing: '0.2px', color: 'var(--px-fg-2)' }}
        >
          {stage.stage_name}
        </h3>
        <span
          className="px-mono text-[11px]"
          style={{
            color: 'var(--px-fg-4)',
            fontVariantNumeric: 'tabular-nums',
          }}
          aria-label={`${stage.candidates.length} candidates in ${stage.stage_name}`}
        >
          {stage.candidates.length}
        </span>
      </header>

      <div
        className="flex min-h-[4rem] flex-1 flex-col gap-1.5 overflow-y-auto"
        style={{ padding: '2px 8px 8px' }}
      >
        {stage.candidates.length === 0 ? (
          <p
            className="py-6 text-center text-[11px]"
            style={{ color: 'var(--px-fg-5)' }}
          >
            Drop candidates here
          </p>
        ) : (
          stage.candidates.map((card) => (
            <CandidateKanbanCard key={card.assignment_id} card={card} />
          ))
        )}
      </div>
    </div>
  )
}
