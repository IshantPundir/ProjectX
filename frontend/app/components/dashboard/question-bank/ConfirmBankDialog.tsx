'use client'

import { useEffect } from 'react'
import { Check } from 'lucide-react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/ui/button'

type Props = {
  bank: BankWithQuestionsResponse
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmBankDialog({ bank, onConfirm, onCancel }: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onCancel])

  const mandatory_count = bank.questions.filter((q) => q.is_mandatory).length
  const total_minutes = bank.total_minutes

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-bank-heading"
        className="bg-white rounded-xl shadow-2xl w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="confirm-bank-heading" className="text-base font-semibold">
            Confirm bank
          </h3>
          <button
            type="button"
            aria-label="Close dialog"
            onClick={onCancel}
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-3 text-sm">
          <p className="text-zinc-700">
            Confirming this bank locks it for interview sessions. After
            confirmation, editing any question will revert the bank to reviewing
            and require re-confirmation.
          </p>

          <div className="bg-zinc-50 border border-zinc-200 rounded-lg p-3 space-y-1.5 text-xs text-zinc-700">
            <div className="flex items-center gap-2">
              <Check className="w-3.5 h-3.5 text-emerald-500" />
              <span>
                {bank.questions.length} questions ·{' '}
                {total_minutes.toFixed(0)} min total
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Check className="w-3.5 h-3.5 text-emerald-500" />
              <span>{mandatory_count} mandatory questions</span>
            </div>
          </div>

          <p className="text-xs text-zinc-500">
            The server will validate knockout coverage and duration budget
            before confirming. If anything is missing, you&apos;ll see an error
            and can fix it before re-confirming.
          </p>
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-zinc-100">
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onConfirm}>Confirm bank</Button>
        </div>
      </div>
    </div>
  )
}
