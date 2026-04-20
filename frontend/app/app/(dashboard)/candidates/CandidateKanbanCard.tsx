'use client'

import Link from 'next/link'
import { useState } from 'react'
import { useDraggable } from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'

import { SessionStatusBadge } from '@/components/dashboard/candidates/SessionStatusBadge'
import { StageTransitionDropdown } from '@/components/dashboard/candidates/StageTransitionDropdown'
import { StatusBadge } from '@/components/dashboard/candidates/StatusBadge'
import type {
  KanbanCandidateCard,
  KanbanColumn,
} from '@/lib/api/candidates'

import { SendInviteDialog } from './SendInviteDialog'

interface Props {
  card: KanbanCandidateCard
  jobPostingId: string
  stages: KanbanColumn[]
  jobTitle: string
  stageName: string
}

export default function CandidateKanbanCard({
  card,
  jobPostingId,
  stages,
  jobTitle,
  stageName,
}: Props) {
  const [inviteOpen, setInviteOpen] = useState(false)
  const { setNodeRef, attributes, listeners, transform, isDragging } =
    useDraggable({
      id: card.assignment_id,
      data: {
        currentStageId: card.current_stage_id,
        candidateId: card.candidate_id,
      },
    })

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    // While dragging lift the card above its column's hover ring and drop
    // shadow so the cursor preview is always on top.
    zIndex: isDragging ? 50 : undefined,
    opacity: isDragging ? 0.85 : undefined,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={`rounded-lg border bg-white p-3 shadow-sm transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${
        isDragging
          ? 'border-blue-300 shadow-lg cursor-grabbing'
          : 'border-zinc-200 cursor-grab'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <Link
            href={`/candidates/${card.candidate_id}`}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            className="block truncate text-sm font-medium text-zinc-900 hover:text-blue-600 hover:underline"
          >
            {card.name ?? 'Unnamed candidate'}
          </Link>
          <p className="mt-0.5 truncate text-xs text-zinc-500">
            {card.email ?? 'No email'}
          </p>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={card.status} />
        <SessionStatusBadge state={card.latest_session_state} />
      </div>

      <div className="mt-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setInviteOpen(true)
          }}
          onPointerDown={(e) => e.stopPropagation()}
          className="rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
        >
          Send invite
        </button>
        <StageTransitionDropdown
          candidateId={card.candidate_id}
          assignmentId={card.assignment_id}
          currentStageId={card.current_stage_id}
          stages={stages}
          status={card.status}
          jobPostingId={jobPostingId}
        />
      </div>

      {inviteOpen && (
        <SendInviteDialog
          open={inviteOpen}
          onOpenChange={setInviteOpen}
          candidateId={card.candidate_id}
          assignmentId={card.assignment_id}
          candidateName={card.name}
          jobTitle={jobTitle}
          stageName={stageName}
        />
      )}
    </div>
  )
}
