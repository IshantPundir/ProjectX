'use client'

import { toast } from 'sonner'

import type {
  AssignmentStatus,
  KanbanColumn,
} from '@/lib/api/candidates'
import { useTransitionCandidate } from '@/lib/hooks/use-transition-candidate'
import { useUpdateAssignmentStatus } from '@/lib/hooks/use-update-assignment-status'

// Deviation from spec: the codebase has no `components/ui/dropdown-menu.tsx`
// primitive (per audit). We fall back to a native `<select>` with `<optgroup>`s
// for Phase 3B — functional, keyboard-accessible, and matches the plan's
// escalation note. A polished shadcn `DropdownMenu` can replace this once the
// primitive is added.

interface Props {
  candidateId: string
  assignmentId: string
  currentStageId: string
  stages: KanbanColumn[]
  status: AssignmentStatus
  jobPostingId: string
}

const STATUS_OPTIONS: { value: AssignmentStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'archived', label: 'Archived' },
  { value: 'hired', label: 'Hired' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'withdrawn', label: 'Withdrawn' },
]

// Sentinel prefixes to keep stage-ids and status values in the same <select>
// without collisions.
const STAGE_PREFIX = 'stage:'
const STATUS_PREFIX = 'status:'

export function StageTransitionDropdown({
  candidateId,
  assignmentId,
  currentStageId,
  stages,
  status,
  jobPostingId,
}: Props) {
  const transition = useTransitionCandidate(jobPostingId)
  const updateStatus = useUpdateAssignmentStatus(candidateId)

  const currentValue = `${STAGE_PREFIX}${currentStageId}`

  function handleChange(raw: string) {
    if (!raw) return
    if (raw.startsWith(STAGE_PREFIX)) {
      const targetStageId = raw.slice(STAGE_PREFIX.length)
      if (targetStageId === currentStageId) return
      transition.mutate(
        { candidateId, assignmentId, targetStageId },
        {
          onError: (err) => {
            toast.error(err.message || 'Failed to transition candidate')
          },
        },
      )
    } else if (raw.startsWith(STATUS_PREFIX)) {
      const nextStatus = raw.slice(STATUS_PREFIX.length) as AssignmentStatus
      if (nextStatus === status) return
      updateStatus.mutate(
        { assignmentId, status: nextStatus, jobPostingId },
        {
          onSuccess: () => {
            toast.success(`Status changed to ${nextStatus}`)
          },
          onError: (err) => {
            toast.error(err.message || 'Failed to update status')
          },
        },
      )
    }
  }

  const pending = transition.isPending || updateStatus.isPending

  return (
    <select
      aria-label="Change stage or status"
      value={currentValue}
      disabled={pending}
      onChange={(e) => handleChange(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      className="h-7 rounded-md border border-zinc-200 bg-white px-1.5 text-xs text-zinc-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400"
    >
      <optgroup label="Transition to stage">
        {stages.map((stage) => (
          <option
            key={stage.stage_id}
            value={`${STAGE_PREFIX}${stage.stage_id}`}
          >
            {stage.stage_name}
            {stage.stage_id === currentStageId ? ' (current)' : ''}
          </option>
        ))}
      </optgroup>
      <optgroup label="Change status">
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={`${STATUS_PREFIX}${opt.value}`}>
            {opt.label}
            {opt.value === status ? ' (current)' : ''}
          </option>
        ))}
      </optgroup>
    </select>
  )
}
