'use client'

import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable'

import { useJob } from '@/lib/hooks/use-job'
import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { useTransitionCandidate } from '@/lib/hooks/use-transition-candidate'

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

  function handleDragEnd(event: DragEndEvent) {
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
      onDragEnd={handleDragEnd}
    >
      <div
        className="flex gap-2.5 overflow-x-auto pb-4"
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
    </DndContext>
  )
}
