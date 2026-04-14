'use client'

import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical } from 'lucide-react'
import { StageFlowCard } from './StageFlowCard'
import type { PipelineStageUpdateInput } from '@/lib/api/pipelines'
import type { BankStatus } from '@/lib/api/question-banks'

type Props = {
  stage: PipelineStageUpdateInput
  sortableId: string
  position: number
  selected: boolean
  bankStatus: BankStatus | null
  onClick: () => void
  onDelete?: () => void
  draggable: boolean
}

export function SortableStageCard({
  stage,
  sortableId,
  position,
  selected,
  bankStatus,
  onClick,
  onDelete,
  draggable,
}: Props) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: sortableId, disabled: !draggable })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition: transition ?? undefined,
    zIndex: isDragging ? 50 : undefined,
    opacity: isDragging ? 0.9 : undefined,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`relative w-full flex items-center gap-1.5 ${
        isDragging ? 'scale-[1.02]' : ''
      } transition-transform`}
    >
      {/* Drag handle — visible + grabbable, only the handle triggers drag */}
      <button
        type="button"
        aria-label="Drag to reorder stage"
        disabled={!draggable}
        className={`flex-shrink-0 p-1 rounded-md text-zinc-300 hover:text-zinc-600 hover:bg-zinc-100 transition ${
          draggable
            ? 'cursor-grab active:cursor-grabbing'
            : 'cursor-not-allowed opacity-40'
        }`}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="w-4 h-4" aria-hidden="true" />
      </button>

      <div className="flex-1 min-w-0">
        <StageFlowCard
          stage={stage}
          position={position}
          selected={selected}
          bankStatus={bankStatus}
          onClick={onClick}
          onDelete={onDelete}
        />
      </div>
    </div>
  )
}
