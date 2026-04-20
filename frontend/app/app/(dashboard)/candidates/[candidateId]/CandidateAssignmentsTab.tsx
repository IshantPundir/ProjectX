'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { JdPicker } from '@/components/dashboard/candidates/JdPicker'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useCreateAssignment } from '@/lib/hooks/use-create-assignment'

interface Props {
  candidateId: string
}

export default function CandidateAssignmentsTab({ candidateId }: Props) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const createAssignment = useCreateAssignment(candidateId)

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

      {/*
        TODO(phase-3b-followup): Render this candidate's assignments here once
        the backend exposes `GET /api/candidates/{id}/assignments` (or the
        detail endpoint is expanded to include them). Task 14 intentionally
        did not add that route, so for 3B we link users back to the JD-level
        Kanban boards to see stage progress.
      */}
      <div className="bg-white border border-zinc-200 rounded-lg p-8 text-center">
        <p className="text-sm text-zinc-600">
          Assignments for this candidate will be listed here.
        </p>
        <p className="text-xs text-zinc-500 mt-1">
          For now, use the JD Kanban boards to see stage progress for each job.
        </p>
      </div>

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
