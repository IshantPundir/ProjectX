'use client'

import { useRef } from 'react'
import { ChevronDown, Plus, Users } from 'lucide-react'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import {
  restrictToVerticalAxis,
  restrictToParentElement,
} from '@dnd-kit/modifiers'

import { Button } from '@/components/px'
import { SortableStageCard } from './SortableStageCard'
import type { PipelineStageUpdateInput } from '@/lib/api/pipelines'
import type { BankResponse } from '@/lib/api/question-banks'

type Props = {
  stages: PipelineStageUpdateInput[]
  selectedStageId: string | null
  banks: BankResponse[]
  onStageClick: (stageId: string) => void
  onStageDelete?: (index: number) => void
  onAddStage: () => void
  onReorder: (nextStages: PipelineStageUpdateInput[]) => void
  onDragStateChange: (dragging: boolean) => void
}

export function PipelineFlowColumn({
  stages,
  selectedStageId,
  banks,
  onStageClick,
  onStageDelete,
  onAddStage,
  onReorder,
  onDragStateChange,
}: Props) {
  const columnRef = useRef<HTMLDivElement>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  // Items for the SortableContext. Only stages with an id participate.
  // Stages without an id are rendered but aren't sortable (they're "Saving…").
  const sortableItems = stages
    .filter((s) => s.id !== undefined)
    .map((s) => s.id as string)

  function handleDragStart() {
    onDragStateChange(true)
  }

  function handleDragEnd(event: DragEndEvent) {
    onDragStateChange(false)
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = stages.findIndex((s) => s.id === active.id)
    const newIndex = stages.findIndex((s) => s.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    const reordered = arrayMove(stages, oldIndex, newIndex)
    onReorder(reordered)
  }

  function handleDragCancel() {
    onDragStateChange(false)
  }

  return (
    <div
      ref={columnRef}
      className="w-[400px] flex-shrink-0 border-r rounded-l-xl overflow-y-auto"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      {/* TODO(design-review): no px-token equivalent for bg-white/95 translucent sticky overlay */}
      <div
        className="px-5 py-4 border-b sticky top-0 bg-white/95 backdrop-blur z-10"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div
          className="text-[11px] font-semibold uppercase tracking-wider"
          style={{ color: 'var(--px-fg-3)' }}
        >
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
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" /> // TODO(design-review): no px-token equivalent for text-zinc-300
        )}

        {/* Stage cards — wrapped in DndContext for drag-to-reorder */}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          modifiers={[restrictToVerticalAxis, restrictToParentElement]}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={handleDragCancel}
        >
          <SortableContext
            items={sortableItems}
            strategy={verticalListSortingStrategy}
          >
            {stages.map((stage, i) => {
              const stageId = stage.id ?? null
              const sortableId = stageId ?? `temp-${i}`
              const bank = stageId
                ? banks.find((b) => b.stage_id === stageId)
                : null
              const selected = stageId !== null && stageId === selectedStageId
              return (
                <div
                  key={sortableId}
                  className="w-full flex flex-col items-center"
                >
                  <SortableStageCard
                    stage={stage}
                    sortableId={sortableId}
                    position={i + 1}
                    selected={selected}
                    bankStatus={bank?.status ?? null}
                    onClick={() => stageId && onStageClick(stageId)}
                    onDelete={
                      onStageDelete ? () => onStageDelete(i) : undefined
                    }
                    draggable={!!stageId}
                  />
                  {i < stages.length - 1 && (
                    // TODO(design-review): no px-token equivalent for text-zinc-300
                    <ChevronDown
                      className="w-4 h-4 text-zinc-300 my-1.5"
                      aria-hidden="true"
                    />
                  )}
                </div>
              )
            })}
          </SortableContext>
        </DndContext>

        {stages.length > 0 && (
          <ChevronDown className="w-4 h-4 text-zinc-300" aria-hidden="true" /> // TODO(design-review): no px-token equivalent for text-zinc-300
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
        <Button
          variant="outline"
          size="sm"
          onClick={onAddStage}
          className="mt-4"
        >
          <Plus className="w-3.5 h-3.5 mr-1" />
          Add stage
        </Button>
      </div>
    </div>
  )
}
