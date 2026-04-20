'use client'

import Link from 'next/link'
import { useState } from 'react'
import { toast } from 'sonner'

import { JdPicker } from '@/components/dashboard/candidates/JdPicker'
import { StatusBadge } from '@/components/dashboard/candidates/StatusBadge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type {
  AssignmentResponse,
  AssignmentStatus,
} from '@/lib/api/candidates'
import { useCandidate } from '@/lib/hooks/use-candidate'
import { useCandidateAssignments } from '@/lib/hooks/use-candidate-assignments'
import { useCreateAssignment } from '@/lib/hooks/use-create-assignment'
import { useUpdateAssignmentStatus } from '@/lib/hooks/use-update-assignment-status'

import { SendInviteDialog } from '../SendInviteDialog'

interface Props {
  candidateId: string
}

const STATUS_OPTIONS: { value: AssignmentStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'archived', label: 'Archived' },
  { value: 'hired', label: 'Hired' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'withdrawn', label: 'Withdrawn' },
]

export default function CandidateAssignmentsTab({ candidateId }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const createAssignment = useCreateAssignment(candidateId)
  const assignmentsQuery = useCandidateAssignments(candidateId)
  // `useCandidate` is already populated at the page level — this reuses the
  // same cache entry so no additional fetch is triggered.
  const candidateQuery = useCandidate(candidateId)
  const candidateName = candidateQuery.data?.name ?? null

  const handleClose = (next: boolean) => {
    if (!next && createAssignment.isPending) return
    setDialogOpen(next)
    if (!next) setSelectedJobId(null)
  }

  const handleSubmit = () => {
    if (!selectedJobId) return
    createAssignment.mutate(
      { job_posting_id: selectedJobId },
      {
        onSuccess: () => {
          toast.success('Candidate assigned to JD')
          setDialogOpen(false)
          setSelectedJobId(null)
        },
        onError: (err) => {
          toast.error(err.message)
        },
      },
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-zinc-900">Assignments</h2>
        <Button type="button" onClick={() => setDialogOpen(true)}>
          + Assign to JD
        </Button>
      </div>

      <AssignmentsTable
        candidateId={candidateId}
        candidateName={candidateName}
        assignments={assignmentsQuery.data}
        isLoading={assignmentsQuery.isLoading}
        error={assignmentsQuery.error}
      />

      <Dialog open={dialogOpen} onOpenChange={handleClose}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Assign to job description</DialogTitle>
            <DialogDescription>
              Pick a JD to add this candidate to. They&apos;ll start in the
              first stage of that job&apos;s pipeline.
            </DialogDescription>
          </DialogHeader>

          <div className="py-2">
            <JdPicker
              value={selectedJobId}
              onChange={(id) => {
                // JdPicker emits `null` for the "All JDs" sentinel. That
                // sentinel isn't meaningful as an assignment target, so we
                // coerce it to `null` and leave the submit button disabled.
                setSelectedJobId(id)
              }}
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleClose(false)}
              disabled={createAssignment.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSubmit}
              disabled={!selectedJobId || createAssignment.isPending}
            >
              {createAssignment.isPending ? 'Assigning…' : 'Assign'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

interface AssignmentsTableProps {
  candidateId: string
  candidateName: string | null
  assignments: AssignmentResponse[] | undefined
  isLoading: boolean
  error: Error | null
}

function AssignmentsTable({
  candidateId,
  candidateName,
  assignments,
  isLoading,
  error,
}: AssignmentsTableProps) {
  if (isLoading) {
    return (
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">Loading assignments…</p>
      </div>
    )
  }

  if (error) {
    // Surface once; TanStack Query won't re-fire this render path on mount,
    // but a retry will replace `error` with undefined.
    toast.error(error.message)
    return (
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">Failed to load assignments.</p>
      </div>
    )
  }

  if (!assignments || assignments.length === 0) {
    return (
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">
          Assignments for this candidate will be listed here.
        </p>
        <p className="text-xs text-zinc-500 mt-1">
          For now, use the JD Kanban boards to see stage progress for each job.
        </p>
      </div>
    )
  }

  return (
    <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
      <table className="min-w-full divide-y divide-zinc-200">
        <thead className="bg-zinc-50">
          <tr>
            <th
              scope="col"
              className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
            >
              Job title
            </th>
            <th
              scope="col"
              className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
            >
              Current stage
            </th>
            <th
              scope="col"
              className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
            >
              Status
            </th>
            <th
              scope="col"
              className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-zinc-600"
            >
              Assigned
            </th>
            <th
              scope="col"
              className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-zinc-600"
            >
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {assignments.map((a) => (
            <AssignmentRow
              key={a.id}
              candidateId={candidateId}
              candidateName={candidateName}
              assignment={a}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

interface AssignmentRowProps {
  candidateId: string
  candidateName: string | null
  assignment: AssignmentResponse
}

function AssignmentRow({
  candidateId,
  candidateName,
  assignment,
}: AssignmentRowProps) {
  const [inviteOpen, setInviteOpen] = useState(false)
  const updateStatus = useUpdateAssignmentStatus(candidateId)
  const canInvite = assignment.status === 'active'

  const handleStatusChange = (next: AssignmentStatus) => {
    if (next === assignment.status) return
    updateStatus.mutate(
      {
        assignmentId: assignment.id,
        status: next,
        jobPostingId: assignment.job_posting_id,
      },
      {
        onSuccess: () => {
          toast.success(`Status changed to ${next}`)
        },
        onError: (err) => {
          toast.error(err.message || 'Failed to update status')
        },
      },
    )
  }

  return (
    <tr className="hover:bg-zinc-50">
      <td className="px-4 py-2 text-sm text-zinc-900">
        <Link
          href={`/candidates?jd=${assignment.job_posting_id}&view=kanban`}
          className="text-zinc-900 hover:text-zinc-700 hover:underline"
        >
          {assignment.job_title || 'Untitled job'}
        </Link>
      </td>
      <td className="px-4 py-2 text-sm text-zinc-700">
        {assignment.current_stage_name || '—'}
      </td>
      <td className="px-4 py-2 text-sm text-zinc-700">
        <div className="flex items-center gap-2">
          <StatusBadge status={assignment.status} />
          <select
            aria-label="Change assignment status"
            value={assignment.status}
            disabled={updateStatus.isPending}
            onChange={(e) =>
              handleStatusChange(e.target.value as AssignmentStatus)
            }
            className="h-7 rounded-md border border-zinc-200 bg-white px-1.5 text-xs text-zinc-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </td>
      <td className="px-4 py-2 text-sm text-zinc-700">
        {new Date(assignment.assigned_at).toLocaleDateString()}
      </td>
      <td className="px-4 py-2 text-right text-sm">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canInvite}
          onClick={() => setInviteOpen(true)}
        >
          Send invite
        </Button>
        {inviteOpen && (
          <SendInviteDialog
            open={inviteOpen}
            onOpenChange={setInviteOpen}
            candidateId={candidateId}
            assignmentId={assignment.id}
            candidateName={candidateName}
            jobTitle={assignment.job_title}
            stageName={assignment.current_stage_name}
          />
        )}
      </td>
    </tr>
  )
}
