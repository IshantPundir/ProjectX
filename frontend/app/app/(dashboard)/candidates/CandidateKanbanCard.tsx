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

// Stable avatar color derived from the candidate's display name.
const AVATAR_COLORS = [
  '#C97B5E',
  '#7A8DB8',
  '#8B9E7E',
  '#B89064',
  '#9B7AB0',
  '#6FA3A1',
]
function avatarColor(name: string | null | undefined): string {
  const n = (name || '?').trim()
  const hash = n.charCodeAt(0) + (n.charCodeAt(1) || 0)
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

function initials(name: string | null | undefined): string {
  if (!name) return '?'
  return name
    .trim()
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

export default function CandidateKanbanCard({
  card,
  jobPostingId,
  stages,
  jobTitle,
  stageName,
}: Props) {
  const [inviteOpen, setInviteOpen] = useState(false)
  const [hover, setHover] = useState(false)
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
    zIndex: isDragging ? 50 : undefined,
    opacity: isDragging ? 0.85 : undefined,
    padding: 10,
    background: 'var(--px-surface)',
    border: `1px solid ${
      isDragging ? 'var(--px-accent-line)' : 'var(--px-hairline)'
    }`,
    borderRadius: 7,
    cursor: isDragging ? 'grabbing' : 'grab',
    boxShadow: isDragging
      ? 'var(--px-shadow-md)'
      : hover
        ? '0 2px 6px rgba(0,0,0,0.05)'
        : 'none',
    transition: 'box-shadow 120ms, border-color 120ms',
    position: 'relative',
  }

  const bg = avatarColor(card.name)

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Header: avatar + name + email */}
      <div className="mb-1.5 flex items-center gap-2">
        <div
          className="flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
          style={{ width: 22, height: 22, background: bg, fontSize: 9.5 }}
          aria-hidden="true"
        >
          {initials(card.name)}
        </div>
        <div className="min-w-0 flex-1">
          <Link
            href={`/candidates/${card.candidate_id}`}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            className="block truncate text-[12.5px] font-medium hover:underline"
            style={{ color: 'var(--px-fg)' }}
          >
            {card.name ?? 'Unnamed candidate'}
          </Link>
          <p
            className="mt-0.5 truncate text-[10.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            {card.email ?? 'No email'}
          </p>
        </div>
      </div>

      {/* Source + Ceipal badges. Prefer assignment_source (more specific
          than candidate_source — a manually-entered candidate can still
          have an ATS-imported submission to this particular job). */}
      {(card.assignment_source.startsWith('ats_') ||
        card.candidate_source.startsWith('ats_') ||
        typeof (card.assignment_source_metadata as { submission_status?: unknown } | null)
          ?.submission_status === 'string') && (
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          {(() => {
            const atsSource = card.assignment_source.startsWith('ats_')
              ? card.assignment_source
              : card.candidate_source.startsWith('ats_')
                ? card.candidate_source
                : null
            if (!atsSource) return null
            return (
              <span
                className="inline-flex items-center rounded-full border px-1.5 text-[9px] font-medium uppercase"
                style={{
                  height: 15,
                  letterSpacing: '0.4px',
                  color: 'var(--px-fg-3)',
                  background: 'var(--px-surface-2)',
                  borderColor: 'var(--px-hairline)',
                }}
                title={`Imported from ${atsSource.replace('ats_', '')}`}
              >
                From {atsSource.replace('ats_', '')}
              </span>
            )
          })()}
          {(() => {
            const status =
              (card.assignment_source_metadata as
                | { submission_status?: unknown }
                | null
              )?.submission_status
            if (typeof status !== 'string' || !status) return null
            return (
              <span
                className="inline-flex items-center rounded-full border px-1.5 text-[9px] font-medium"
                style={{
                  height: 15,
                  color: 'var(--px-fg-3)',
                  background: 'var(--px-surface)',
                  borderColor: 'var(--px-hairline)',
                }}
                title="Ceipal's pipeline status for this submission."
              >
                Ceipal: {status}
              </span>
            )
          })()}
        </div>
      )}

      {/* Status chips */}
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={card.status} />
        <SessionStatusBadge state={card.latest_session_state} />
      </div>

      {/* Footer: invite + transition */}
      <div
        className="mt-2 flex items-center justify-between gap-2 border-t pt-2"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            setInviteOpen(true)
          }}
          onPointerDown={(e) => e.stopPropagation()}
          className="px-btn outline xs"
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
