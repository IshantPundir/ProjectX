'use client'

import * as React from 'react'
import { toast } from 'sonner'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
} from '@/components/px'
import { useShareReport } from '@/lib/hooks/use-share-report'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface Props {
  sessionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ShareReportDialog({ sessionId, open, onOpenChange }: Props) {
  const [email, setEmail] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const share = useShareReport(sessionId)

  async function handleSend() {
    const trimmed = email.trim()
    if (!EMAIL_RE.test(trimmed)) {
      setError('Enter a valid email address')
      return
    }
    setError(null)
    try {
      await share.mutateAsync(trimmed)
      toast.success(`Report is being sent to ${trimmed}`)
      setEmail('')
      onOpenChange(false)
    } catch {
      setError('Could not send the report. Please try again.')
    }
  }

  // Prevent closing mid-request — the mutation is in flight and discarding the
  // result would leave the UI in an ambiguous state.
  const handleOpenChange = (next: boolean) => {
    if (!next && share.isPending) return
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Share report</DialogTitle>
          <DialogDescription>
            Email a PDF of this evaluation to a client. They receive the verdict,
            scores, rationale, and question-by-question summary.
          </DialogDescription>
        </DialogHeader>
        <div className="mt-2">
          <Label htmlFor="share-email">Recipient email</Label>
          <Input
            id="share-email"
            type="email"
            placeholder="client@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleSend()
            }}
          />
          {error && (
            <p className="mt-1.5 text-[12px]" style={{ color: 'var(--px-danger)' }}>
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={share.isPending}
          >
            Cancel
          </Button>
          <Button type="button" onClick={() => void handleSend()} disabled={share.isPending}>
            {share.isPending ? 'Sending…' : 'Send'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
