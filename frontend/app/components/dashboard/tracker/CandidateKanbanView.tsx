'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable'

import { useJob } from '@/lib/hooks/use-job'
import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { useTransitionCandidate } from '@/lib/hooks/use-transition-candidate'

import { CandidateKanbanCardOverlay } from './CandidateKanbanCard'
import CandidateKanbanColumn from './CandidateKanbanColumn'

interface Props {
  jobId: string
}

interface DraggableCardData {
  currentStageId: string
  candidateId: string
}

interface DroppableColumnData {
  stageId: string
}

export default function CandidateKanbanView({ jobId }: Props) {
  const { data, isLoading, error } = useKanbanBoard(jobId)
  // The kanban board response doesn't carry the job title — fetch it so we
  // can surface it in the Send Invite dialog. `useJob` is already shared
  // with the review page so this usually returns a cached hit.
  const jobQuery = useJob(jobId)
  const jobTitle = jobQuery.data?.title ?? ''
  const transition = useTransitionCandidate(jobId)

  // Surface fetch errors via toast (once per error instance).
  const lastErrorRef = useRef<Error | null>(null)
  useEffect(() => {
    if (error && error !== lastErrorRef.current) {
      lastErrorRef.current = error as Error
      toast.error((error as Error).message || 'Failed to load kanban board')
    }
    if (!error) lastErrorRef.current = null
  }, [error])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  // Track which card is currently being dragged so <DragOverlay> can render
  // a portaled copy outside the board's overflow context (otherwise the
  // card visibly clips at column / board edges as the cursor moves).
  const [activeId, setActiveId] = useState<string | null>(null)
  const activeCardCtx = useMemo(() => {
    if (!activeId || !data) return null
    for (const stage of data.stages) {
      const card = stage.candidates.find((c) => c.assignment_id === activeId)
      if (card) return { card, stageName: stage.stage_name }
    }
    return null
  }, [activeId, data])

  function handleDragStart(event: DragStartEvent) {
    setActiveId(String(event.active.id))
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null)
    const { active, over } = event
    if (!over) return

    const activeData = active.data.current as DraggableCardData | undefined
    const overData = over.data.current as DroppableColumnData | undefined

    if (!activeData || !overData) return
    if (overData.stageId === activeData.currentStageId) return

    transition.mutate(
      {
        candidateId: activeData.candidateId,
        assignmentId: String(active.id),
        targetStageId: overData.stageId,
      },
      {
        onError: (err) => {
          toast.error(err.message || 'Failed to move candidate')
        },
      },
    )
  }

  if (isLoading) {
    return (
      <div
        className="rounded-[10px] border p-12 text-center"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Loading board…
        </p>
      </div>
    )
  }

  if (!data || data.stages.length === 0) {
    return (
      <div
        className="rounded-[10px] border p-12 text-center"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
          This role has no pipeline stages. Add stages in the pipeline editor
          first.
        </p>
      </div>
    )
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <div
        className="flex h-full gap-2.5 overflow-x-auto pb-4"
        role="list"
        aria-label="Candidate kanban board"
      >
        {data.stages.map((stage) => (
          <CandidateKanbanColumn
            key={stage.stage_id}
            stage={stage}
            stages={data.stages}
            jobPostingId={jobId}
            jobTitle={jobTitle}
          />
        ))}
      </div>
      {/* Portaled by @dnd-kit to document.body — escapes every ancestor
          overflow:auto/hidden so the dragging card stays visible across
          the entire board, not just within the source column. */}
      <DragOverlay dropAnimation={null}>
        {activeCardCtx ? (
          <CandidateKanbanCardOverlay
            card={activeCardCtx.card}
            jobPostingId={jobId}
            stages={data.stages}
            jobTitle={jobTitle}
            stageName={activeCardCtx.stageName}
          />
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
