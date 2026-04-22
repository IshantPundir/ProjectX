'use client'

import { Button } from '@/components/px'

type Props = {
  isEditing: boolean
  isConfirmed: boolean
  isSaving: boolean
  isConfirming: boolean
  onSave: () => void
  onConfirm: () => void
}

export function ConfirmBar({
  isEditing,
  isConfirmed,
  isSaving,
  isConfirming,
  onSave,
  onConfirm,
}: Props) {
  // Edit mode: show Save button
  if (isEditing) {
    return (
      <div className="pt-4 border-t border-zinc-100">
        <Button
          type="button"
          onClick={onSave}
          disabled={isSaving}
          className="w-full bg-blue-600 text-white hover:bg-blue-700"
        >
          {isSaving ? 'Saving...' : 'Save Signals'}
        </Button>
      </div>
    )
  }

  // Already confirmed: show badge
  if (isConfirmed) {
    return (
      <div className="pt-4 border-t border-zinc-100">
        <div className="flex items-center justify-center gap-1.5 rounded-lg bg-emerald-50 border border-emerald-200 py-2 text-sm font-medium text-emerald-700">
          <span aria-hidden="true">&#10003;</span>
          Signals Confirmed
        </div>
      </div>
    )
  }

  // Unconfirmed: show Confirm button
  return (
    <div className="pt-4 border-t border-zinc-100">
      <Button
        type="button"
        onClick={onConfirm}
        disabled={isConfirming}
        className="w-full bg-emerald-600 text-white hover:bg-emerald-700"
      >
        {isConfirming ? 'Confirming...' : 'Confirm Signals'}
      </Button>
    </div>
  )
}
