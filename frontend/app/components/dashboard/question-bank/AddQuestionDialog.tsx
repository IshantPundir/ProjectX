'use client'

import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/px'

type Proposal = {
  proposed_text: string
  proposed_signal_probed: string
  proposed_mandatory: boolean
  proposed_position: number
  rationale?: string
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDraft: (body: { instruction: string }) => Promise<Proposal>
  onAccept: (body: {
    text: string
    signal_probed: string
    mandatory: boolean
    position: number
  }) => void
}

export function AddQuestionDialog({
  open,
  onOpenChange,
  onDraft,
  onAccept,
}: Props) {
  const [instruction, setInstruction] = useState('')
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await onDraft({ instruction })
      setProposal(res)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const accept = () => {
    if (!proposal) return
    onAccept({
      text: proposal.proposed_text,
      signal_probed: proposal.proposed_signal_probed,
      mandatory: proposal.proposed_mandatory,
      position: proposal.proposed_position,
    })
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent widthClass="sm:max-w-lg">
        <DialogTitle>Add a question</DialogTitle>
        <div className="space-y-3 pt-2">
          {!proposal && (
            <>
              <label
                htmlFor="draft-instruction"
                className="block text-sm font-medium text-zinc-700"
              >
                Describe the question you want to add
              </label>
              <textarea
                id="draft-instruction"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={3}
                className="w-full rounded border border-zinc-200 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400"
              />
              <button
                type="button"
                disabled={loading || instruction.trim().length < 1}
                onClick={submit}
                className="rounded bg-zinc-900 px-4 py-2 text-sm text-white disabled:opacity-40"
              >
                {loading ? 'Drafting…' : 'Draft'}
              </button>
            </>
          )}

          {proposal && (
            <>
              <div className="rounded border border-emerald-200 bg-emerald-50 p-3">
                <div className="text-xs font-medium uppercase tracking-wide text-emerald-700">
                  Proposed
                </div>
                <div className="mt-1 text-sm text-zinc-800">
                  {proposal.proposed_text}
                </div>
                {proposal.rationale && (
                  <div className="mt-2 text-xs text-zinc-600">
                    {proposal.rationale}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={accept}
                  className="rounded bg-zinc-900 px-4 py-2 text-sm text-white"
                >
                  Accept
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProposal(null)
                    setInstruction('')
                  }}
                  className="rounded border border-zinc-200 px-4 py-2 text-sm"
                >
                  Refine again
                </button>
                <button
                  type="button"
                  onClick={() => onOpenChange(false)}
                  className="rounded border border-zinc-200 px-4 py-2 text-sm"
                >
                  Cancel
                </button>
              </div>
            </>
          )}

          {error && (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
