'use client'

import Link from 'next/link'
import { useState } from 'react'
import { useDraggable } from '@dnd-kit/core'

import { SessionStatusBadge } from '@/components/dashboard/candidates/SessionStatusBadge'
import { StageTransitionDropdown } from '@/components/dashboard/candidates/StageTransitionDropdown'
import { StatusBadge } from '@/components/dashboard/candidates/StatusBadge'
import type {
  KanbanCandidateCard,
  KanbanColumn,
} from '@/lib/api/candidates'

import { SendInviteDialog } from '@/app/(dashboard)/candidates/SendInviteDialog'

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

/**
 * Pure card visual — no draggable wiring. Used in two places:
 *   1. Inside the draggable wrapper (`CandidateKanbanCard`).
 *   2. Inside `<DragOverlay>` (`CandidateKanbanCardOverlay`) so the
 *      moving copy renders above ALL ancestor scroll containers.
 *
 * Renders the action footer (Send invite + StageTransitionDropdown) too,
 * but those interactions can't fire during a drag since pointer events
 * are captured.
 */
function CardBody({
  card,
  jobPostingId,
  stages,
  jobTitle,
  stageName,
}: Props) {
  const [inviteOpen, setInviteOpen] = useState(false)
  const bg = avatarColor(card.name)

  return (
    <>
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
    </>
  )
}

const CARD_WRAPPER_STYLE: React.CSSProperties = {
  padding: 10,
  background: 'var(--px-surface)',
  borderRadius: 7,
  position: 'relative',
}

export default function CandidateKanbanCard(props: Props) {
  const [hover, setHover] = useState(false)
  const { setNodeRef, attributes, listeners, isDragging } = useDraggable({
    id: props.card.assignment_id,
    data: {
      currentStageId: props.card.current_stage_id,
      candidateId: props.card.candidate_id,
    },
  })

  // While dragging, the original stays in place as a faded placeholder.
  // The visible moving copy is rendered by <DragOverlay> in the parent —
  // that escapes the column's overflow:auto and the board's overflow-x:auto
  // so the card isn't clipped when dragged across stages.
  const style: React.CSSProperties = {
    ...CARD_WRAPPER_STYLE,
    border: `1px solid var(--px-hairline)`,
    cursor: isDragging ? 'grabbing' : 'grab',
    opacity: isDragging ? 0.35 : undefined,
    boxShadow: hover && !isDragging ? '0 2px 6px rgba(0,0,0,0.05)' : 'none',
    transition: 'box-shadow 120ms, opacity 120ms',
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <CardBody {...props} />
    </div>
  )
}

/**
 * Static visual rendered inside `<DragOverlay>` — no draggable wiring,
 * no cursor styling that depends on drag state. Slight elevation +
 * accent border to read as "lifted" vs. the placeholder it leaves
 * behind in the source column.
 */
export function CandidateKanbanCardOverlay(props: Props) {
  const style: React.CSSProperties = {
    ...CARD_WRAPPER_STYLE,
    width: 304, // matches the column width (w-80 = 320 - 16 padding)
    border: `1px solid var(--px-accent-line)`,
    cursor: 'grabbing',
    boxShadow: 'var(--px-shadow-md)',
    transform: 'rotate(1.5deg)',
  }
  return (
    <div style={style}>
      <CardBody {...props} />
    </div>
  )
}
