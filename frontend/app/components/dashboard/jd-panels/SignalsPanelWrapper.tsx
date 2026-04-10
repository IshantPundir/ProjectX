'use client'

import type { SignalSnapshot } from '@/lib/api/jobs'
import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'
import { useSaveSignals } from '@/lib/hooks/use-save-signals'
import { useJobEditStore } from '@/stores/job-edit'

import { Button } from '@/components/ui/button'
import { ConfirmBar } from './ConfirmBar'
import { EditableSignalsPanel } from './EditableSignalsPanel'
import { SignalsPanel } from './SignalsPanel'

type Props = {
  snapshot: SignalSnapshot
  isConfirmed: boolean
  canManage: boolean
  jobId: string
}

export function SignalsPanelWrapper({
  snapshot,
  isConfirmed,
  canManage,
  jobId,
}: Props) {
  const isEditing = useJobEditStore((s) => s.isEditing)
  const isDirty = useJobEditStore((s) => s.isDirty)
  const draft = useJobEditStore((s) => s.draft)
  const startEditing = useJobEditStore((s) => s.startEditing)
  const stopEditing = useJobEditStore((s) => s.stopEditing)
  const markClean = useJobEditStore((s) => s.markClean)

  const saveSignals = useSaveSignals(jobId)
  const confirmSignals = useConfirmSignals(jobId)

  function handleToggleEdit() {
    if (isEditing) {
      if (isDirty) {
        const confirmed = window.confirm(
          'You have unsaved changes. Discard them?',
        )
        if (!confirmed) return
      }
      stopEditing()
    } else {
      startEditing(snapshot)
    }
  }

  function handleSave() {
    if (!draft) return
    saveSignals.mutate(
      {
        required_skills: draft.required_skills,
        preferred_skills: draft.preferred_skills,
        must_haves: draft.must_haves,
        good_to_haves: draft.good_to_haves,
        min_experience_years: draft.min_experience_years,
        seniority_level: draft.seniority_level,
        role_summary: draft.role_summary,
      },
      {
        onSuccess: () => {
          markClean()
          stopEditing()
        },
      },
    )
  }

  function handleConfirm() {
    confirmSignals.mutate()
  }

  return (
    <aside className="col-span-1 bg-white rounded-lg border border-zinc-200 p-5 space-y-5 overflow-auto flex flex-col">
      <div className="flex items-center justify-between pb-2 border-b border-zinc-100">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Signals
        </h3>
        {canManage && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={handleToggleEdit}
          >
            {isEditing ? 'Done Editing' : 'Edit Signals'}
          </Button>
        )}
      </div>

      <div className="flex-1 overflow-auto">
        {isEditing ? (
          <EditableSignalsPanel />
        ) : (
          <SignalsPanel snapshot={snapshot} />
        )}
      </div>

      {canManage && (
        <ConfirmBar
          isEditing={isEditing}
          isConfirmed={isConfirmed}
          isSaving={saveSignals.isPending}
          isConfirming={confirmSignals.isPending}
          onSave={handleSave}
          onConfirm={handleConfirm}
        />
      )}
    </aside>
  )
}
