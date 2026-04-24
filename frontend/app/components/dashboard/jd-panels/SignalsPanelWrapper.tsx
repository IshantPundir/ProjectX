'use client'

import { useEffect, useState } from 'react'

import type { SignalSnapshot } from '@/lib/api/jobs'
import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'
import { useSaveSignals } from '@/lib/hooks/use-save-signals'
import { useJobEditStore } from '@/stores/job-edit'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from '@/components/px'
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

  // Reset editing state when navigating between jobs. The Zustand store
  // is a global singleton — without this cleanup, editing state from
  // job A bleeds into job B on navigation.
  useEffect(() => {
    return () => useJobEditStore.getState().stopEditing()
  }, [jobId])
  const draft = useJobEditStore((s) => s.draft)
  const startEditing = useJobEditStore((s) => s.startEditing)
  const stopEditing = useJobEditStore((s) => s.stopEditing)
  const markClean = useJobEditStore((s) => s.markClean)

  const saveSignals = useSaveSignals(jobId)
  const confirmSignals = useConfirmSignals(jobId)

  const [discardOpen, setDiscardOpen] = useState(false)

  function handleToggleEdit() {
    if (isEditing) {
      if (isDirty) {
        setDiscardOpen(true)
        return
      }
      stopEditing()
    } else {
      startEditing(snapshot)
    }
  }

  function confirmDiscard() {
    setDiscardOpen(false)
    stopEditing()
  }

  function handleSave() {
    if (!draft) return
    saveSignals.mutate(
      {
        signals: draft.signals,
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
    <aside
      className="sticky top-4 col-span-1 flex max-h-[calc(100vh-6rem)] flex-col self-start overflow-auto rounded-[10px] border"
      style={{
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div
        className="flex items-center justify-between border-b px-4 py-3"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <h3
          className="m-0 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Signals
        </h3>
        {canManage && (
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={handleToggleEdit}
          >
            {isEditing ? 'Done editing' : 'Edit signals'}
          </Button>
        )}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {isEditing ? (
          <EditableSignalsPanel />
        ) : (
          <SignalsPanel snapshot={snapshot} />
        )}
      </div>

      {canManage && (
        <div
          className="border-t px-4 py-3"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <ConfirmBar
            isEditing={isEditing}
            isConfirmed={isConfirmed}
            isSaving={saveSignals.isPending}
            isConfirming={confirmSignals.isPending}
            onSave={handleSave}
            onConfirm={handleConfirm}
          />
        </div>
      )}

      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent>
          <DialogTitle>Discard unsaved changes?</DialogTitle>
          <DialogDescription>
            Your edits to the signals will be lost. This cannot be undone.
          </DialogDescription>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDiscardOpen(false)}>
              Keep editing
            </Button>
            <Button variant="destructive" onClick={confirmDiscard}>
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
