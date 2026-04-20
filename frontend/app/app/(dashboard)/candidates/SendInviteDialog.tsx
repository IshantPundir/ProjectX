'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useSendInvite } from '@/lib/hooks/use-send-invite'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  candidateId: string
  assignmentId: string
  candidateName: string | null
  jobTitle: string
  stageName: string
  stageOtpDefault?: boolean // optional — if known, prefill
}

/**
 * Read a string field off an unknown error payload without resorting to `any`.
 * apiFetch throws ApiError whose message holds the backend `detail` string.
 * When the error object also carries a structured `code`, prefer it.
 */
function errorField(err: unknown, key: 'code' | 'message'): string | undefined {
  if (err && typeof err === 'object' && key in err) {
    const value = (err as Record<string, unknown>)[key]
    return typeof value === 'string' ? value : undefined
  }
  return undefined
}

export function SendInviteDialog({
  open,
  onOpenChange,
  candidateId,
  assignmentId,
  candidateName,
  jobTitle,
  stageName,
  stageOtpDefault,
}: Props) {
  const [otpRequired, setOtpRequired] = useState<boolean>(
    stageOtpDefault ?? false,
  )
  const sendInvite = useSendInvite(candidateId)

  // Prevent closing the dialog mid-request — the mutation is in flight and
  // discarding the result would leave the UI in an ambiguous state.
  const handleOpenChange = (next: boolean) => {
    if (!next && sendInvite.isPending) return
    onOpenChange(next)
  }

  const onSend = () => {
    sendInvite.mutate(
      { assignment_id: assignmentId, otp_required: otpRequired },
      {
        onSuccess: () => {
          toast.success('Invite sent')
          onOpenChange(false)
        },
        onError: (err) => {
          const code = errorField(err, 'code') ?? errorField(err, 'message')
          if (code === 'INVALID_STAGE_TYPE_FOR_INVITE') {
            toast.error(
              'This stage is not an AI interview stage. Move the candidate to an AI interview stage first.',
            )
          } else if (code === 'ASSIGNMENT_NOT_ACTIVE') {
            toast.error(
              'This assignment is archived / rejected / hired / withdrawn.',
            )
          } else {
            toast.error(err.message)
          }
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send interview invite</DialogTitle>
          <DialogDescription>
            To{' '}
            <strong>{candidateName ?? 'this candidate'}</strong> for{' '}
            <strong>{jobTitle}</strong> · {stageName}.
          </DialogDescription>
        </DialogHeader>
        <label className="flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={otpRequired}
            onChange={(e) => setOtpRequired(e.target.checked)}
          />
          Require one-time code verification during pre-check
        </label>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={sendInvite.isPending}
          >
            Cancel
          </Button>
          <Button onClick={onSend} disabled={sendInvite.isPending}>
            {sendInvite.isPending ? 'Sending…' : 'Send invite'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
