'use client'

import type { BankResponse } from '@/lib/api/question-banks'
import { BankStatusBadge } from './BankStatusBadge'

type Props = {
  banks: BankResponse[]
  selectedStageId: string | null
  onSelect: (stageId: string) => void
}

export function QuestionSidebar({ banks, selectedStageId, onSelect }: Props) {
  return (
    <aside className="w-70 border-r border-zinc-200 bg-white overflow-y-auto">
      <div className="px-4 py-4">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 mb-3">
          Pipeline stages
        </div>
        <ul className="space-y-2">
          {banks.map((bank, i) => {
            const isSelected = bank.stage_id === selectedStageId
            return (
              <li key={bank.id}>
                <button
                  type="button"
                  onClick={() => onSelect(bank.stage_id)}
                  className={`w-full text-left rounded-lg border px-3 py-2.5 transition ${
                    isSelected
                      ? 'bg-blue-50 border-blue-200'
                      : 'bg-white border-zinc-200 hover:border-zinc-300'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium text-zinc-900">
                      {i + 1} · Stage
                    </div>
                    <BankStatusBadge status={bank.status} small />
                  </div>
                  <div className="text-[11px] text-zinc-500 mt-1">
                    {bank.question_count > 0
                      ? `${bank.question_count} questions · ${bank.total_minutes.toFixed(0)} min`
                      : 'Not generated yet'}
                  </div>
                  {bank.is_stale && (
                    <div className="text-[10px] text-amber-600 mt-1">
                      Signals changed · regenerate
                    </div>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </div>
    </aside>
  )
}
