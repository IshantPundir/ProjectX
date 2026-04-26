'use client'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/px/Dialog'

export interface EditCategoryWarningModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  category: 'B' | 'C' | null
  inFlightCounts: Record<string, number>
  onConfirm: () => void
  onPause?: () => void
}

export function EditCategoryWarningModal({
  open,
  onOpenChange,
  category,
  inFlightCounts,
  onConfirm,
  onPause,
}: EditCategoryWarningModalProps) {
  if (!open || category === null) return null

  const totalInFlight = Object.values(inFlightCounts).reduce((sum, n) => sum + n, 0)

  let title: string
  let body: string
  let confirmLabel: string
  let confirmHandler: () => void

  if (category === 'B') {
    title = 'Pipeline shape will change'
    body =
      'This changes the pipeline shape. New candidates will see the new shape. Candidates currently in flight stay on their entered shape and will not be re-routed.'
    confirmLabel = 'Confirm'
    confirmHandler = onConfirm
  } else if (category === 'C' && totalInFlight === 0) {
    title = 'Remove this stage?'
    body =
      'This is permanent. The stage will be deleted along with its bank, questions, and participant assignments.'
    confirmLabel = 'Confirm Remove'
    confirmHandler = onConfirm
  } else {
    // category === 'C' && totalInFlight > 0
    const n = totalInFlight
    title = 'Pause this stage first'
    body =
      `${n} candidate${n === 1 ? '' : 's'} ${n === 1 ? 'is' : 'are'} currently in this stage. ` +
      `Pausing this stage will stop new candidates from entering it. ` +
      `You'll need to advance or reject the ${n} in-flight candidate${n === 1 ? '' : 's'} manually before it can be fully removed.`
    confirmLabel = 'Pause Stage'
    confirmHandler = onPause ?? onConfirm
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="px-btn ghost sm"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={confirmHandler}
            className={
              category === 'C' && totalInFlight === 0
                ? 'px-btn destructive sm'
                : 'px-btn primary sm'
            }
          >
            {confirmLabel}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
