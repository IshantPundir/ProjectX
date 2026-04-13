'use client'

import { AlertCircle, Check, Loader2, RefreshCcw } from 'lucide-react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/ui/button'
import { BankStatusBadge } from './BankStatusBadge'

type Props = {
  bank: BankWithQuestionsResponse
  isSaving: boolean
  saveFailed: boolean
  onGenerate: () => void
  onRegenerate: () => void
  onConfirm: () => void
  onAddCustom: () => void
}

export function BankHeader({
  bank,
  isSaving,
  saveFailed,
  onGenerate,
  onRegenerate,
  onConfirm,
  onAddCustom,
}: Props) {
  const hasQuestions = bank.questions.length > 0
  const canConfirm = bank.status === 'reviewing'

  return (
    <div className="flex items-start justify-between gap-4 pb-4 border-b border-zinc-200">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <h2 className="text-base font-semibold text-zinc-900">
            {bank.questions.length > 0
              ? `${bank.questions.length} questions · ${bank.total_minutes.toFixed(0)} min`
              : 'No questions yet'}
          </h2>
          <BankStatusBadge status={bank.status} />
        </div>
        {bank.generation_error && (
          <div className="text-xs text-red-600 mt-1">{bank.generation_error}</div>
        )}
        {bank.is_stale && (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-2">
            Signals have changed since this bank was generated. Click Regenerate to pick up the latest.
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5 text-xs" aria-live="polite">
          {saveFailed ? (
            <>
              <AlertCircle className="w-3.5 h-3.5 text-red-500" aria-hidden="true" />
              <span className="text-red-600">Save failed</span>
            </>
          ) : isSaving ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin text-zinc-400" aria-hidden="true" />
              <span className="text-zinc-500">Saving…</span>
            </>
          ) : hasQuestions ? (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-500" aria-hidden="true" />
              <span className="text-zinc-500">All changes saved</span>
            </>
          ) : null}
        </div>

        {!hasQuestions && bank.status === 'draft' && (
          <Button onClick={onGenerate} size="sm">Generate questions</Button>
        )}
        {hasQuestions && (
          <>
            <Button variant="outline" size="sm" onClick={onRegenerate}>
              <RefreshCcw className="w-3.5 h-3.5 mr-1" />
              Regenerate all
            </Button>
            <Button variant="outline" size="sm" onClick={onAddCustom}>
              + Add custom
            </Button>
            {canConfirm && (
              <Button size="sm" onClick={onConfirm}>
                Confirm bank
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
