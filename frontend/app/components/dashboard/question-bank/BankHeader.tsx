'use client'

import { AlertCircle, Check, Loader2, RefreshCcw } from 'lucide-react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { Button } from '@/components/px'
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
    <div
      className="flex items-start justify-between gap-4 border-b pb-4"
      style={{ borderColor: 'var(--px-hairline)' }}
    >
      <div>
        <div className="mb-1 flex items-center gap-2">
          <h2
            className="px-serif m-0 text-[22px] font-normal"
            style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}
          >
            {bank.questions.length > 0
              ? `${bank.questions.length} questions · ${bank.total_minutes.toFixed(0)} min`
              : 'No questions yet'}
          </h2>
          <BankStatusBadge status={bank.status} />
        </div>
        {bank.generation_error && (
          <div
            className="mt-1 text-[11.5px]"
            style={{ color: 'var(--px-danger)' }}
          >
            {bank.generation_error}
          </div>
        )}
        {bank.is_stale && (
          <div
            className="mt-2 rounded-md border px-2 py-1 text-[11.5px]"
            style={{
              color: 'var(--px-caution)',
              background: 'var(--px-caution-bg)',
              borderColor: 'var(--px-caution-line)',
            }}
          >
            Signals have changed since this bank was generated. Click
            Regenerate to pick up the latest.
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5 text-[11.5px]" aria-live="polite">
          {saveFailed ? (
            <>
              <AlertCircle
                className="h-3.5 w-3.5"
                style={{ color: 'var(--px-danger)' }}
                aria-hidden="true"
              />
              <span style={{ color: 'var(--px-danger)' }}>Save failed</span>
            </>
          ) : isSaving ? (
            <>
              <Loader2
                className="h-3.5 w-3.5 animate-spin"
                style={{ color: 'var(--px-fg-4)' }}
                aria-hidden="true"
              />
              <span style={{ color: 'var(--px-fg-3)' }}>Saving…</span>
            </>
          ) : hasQuestions ? (
            <>
              <Check
                className="h-3.5 w-3.5"
                style={{ color: 'var(--px-ok)' }}
                aria-hidden="true"
              />
              <span style={{ color: 'var(--px-fg-3)' }}>All changes saved</span>
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
