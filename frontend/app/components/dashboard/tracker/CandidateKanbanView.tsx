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
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { useTransitionCandidate } from '@/lib/hooks/use-transition-candidate'
import { schedulerApi, type InviteResponse } from '@/lib/api/scheduler'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

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
  const qc = useQueryClient()
  const { data, isLoading, error } = useKanbanBoard(jobId)
  // Pipeline carries `stage_type` per stage, which the kanban response
  // doesn't. Needed to identify the AI Screening drop target so we can
  // auto-fire the candidate invite.
  const pipeline = useJobPipeline(jobId)
  const transition = useTransitionCandidate(jobId)

  // Generic invite mutation (the per-candidate `useSendInvite` hook binds
  // candidateId at hook time, which doesn't fit our DnD-driven flow where
  // the candidate varies per drop). Same backend endpoint, same cache
  // invalidations.
  const autoInvite = useMutation<
    InviteResponse,
    Error,
    { candidateId: string; assignmentId: string }
  >({
    mutationFn: async ({ assignmentId }) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.sendInvite(token, {
        assignment_id: assignmentId,
        otp_required: true,
      })
    },
    onSuccess: (_, { candidateId }) => {
      void qc.invalidateQueries({
        queryKey: ['candidates', candidateId, 'assignments'],
      })
      void qc.invalidateQueries({ queryKey: ['candidates-kanban'] })
      void qc.invalidateQueries({ queryKey: ['assignment-sessions'] })
    },
  })

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

    // Capture stage type + the dragged card BEFORE invalidation —
    // post-success the kanban refetches and these references would go stale.
    const targetStageType = pipeline.data?.stages.find(
      (s) => s.id === overData.stageId,
    )?.stage_type
    const draggedCard = data?.stages
      .flatMap((s) => s.candidates)
      .find((c) => c.assignment_id === String(active.id))

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
        onSuccess: () => {
          // Auto-send the OTP-gated invite when a candidate enters the AI
          // Screening stage. Guard on `latest_session_state == null` so we
          // don't supersede an already-issued invite or interrupt a session
          // in flight if the recruiter happens to drop them back into the
          // stage. (This is a frontend-only guard for now — a backend hook
          // on stage transitions would be the durable place for this rule
          // since it'd cover ATS-driven moves too. Tracked as follow-up.)
          if (
            targetStageType === 'ai_screening' &&
            draggedCard &&
            draggedCard.latest_session_state == null
          ) {
            autoInvite.mutate(
              {
                candidateId: draggedCard.candidate_id,
                assignmentId: String(active.id),
              },
              {
                onSuccess: () => {
                  toast.success('Invite sent (OTP enabled)')
                },
                onError: (err) => {
                  toast.error(
                    err.message || 'Moved candidate but failed to send invite',
                  )
                },
              },
            )
          }
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
          <CandidateKanbanColumn key={stage.stage_id} stage={stage} />
        ))}
      </div>
      {/* Portaled by @dnd-kit to document.body — escapes every ancestor
          overflow:auto/hidden so the dragging card stays visible across
          the entire board, not just within the source column. */}
      <DragOverlay dropAnimation={null}>
        {activeCardCtx ? (
          <CandidateKanbanCardOverlay card={activeCardCtx.card} />
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
