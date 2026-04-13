'use client'

import { useRef, useState } from 'react'
import { ChevronDown, MoreVertical, RefreshCcw, Trash2 } from 'lucide-react'
import type { QuestionResponse } from '@/lib/api/question-banks'
import { useDeleteQuestion } from '@/lib/hooks/use-save-question'
import { useRegenerateQuestion } from '@/lib/hooks/use-regenerate-question'
import { QuestionEditForm } from './QuestionEditForm'
import { QuestionRubricExpanded } from './QuestionRubricExpanded'

type Props = {
  jobId: string
  stageId: string
  question: QuestionResponse
  expanded: boolean
  onToggleExpand: () => void
}

export function QuestionCard({
  jobId,
  stageId,
  question,
  expanded,
  onToggleExpand,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const deleteMutation = useDeleteQuestion(jobId, stageId)
  const regenMutation = useRegenerateQuestion(jobId, stageId, question.id)

  const sourceBadge =
    question.source === 'recruiter'
      ? { bg: 'bg-purple-50', text: 'text-purple-700', label: 'CUSTOM' }
      : question.source === 'ai_regenerated'
        ? { bg: 'bg-blue-50', text: 'text-blue-700', label: 'REGENERATED' }
        : null

  return (
    <div className="bg-white border border-zinc-200 rounded-xl shadow-sm overflow-visible">
      <div className="p-4 cursor-pointer" onClick={onToggleExpand}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-xs font-semibold text-zinc-500">
                Q{question.position + 1}
              </span>
              {question.is_mandatory && (
                <span className="bg-red-50 text-red-700 text-[9px] font-bold px-2 py-0.5 rounded">
                  MANDATORY
                </span>
              )}
              {sourceBadge && (
                <span
                  className={`${sourceBadge.bg} ${sourceBadge.text} text-[9px] font-bold px-2 py-0.5 rounded`}
                >
                  {sourceBadge.label}
                </span>
              )}
              {question.edited_by_recruiter &&
                question.source !== 'recruiter' && (
                  <span className="bg-amber-50 text-amber-700 text-[9px] font-bold px-2 py-0.5 rounded">
                    EDITED
                  </span>
                )}
              <span className="text-[10px] text-zinc-400">
                probes: {question.signal_values.join(', ')}
              </span>
              <span className="text-[10px] text-zinc-400">
                · {question.estimated_minutes} min
              </span>
            </div>
            <div className="text-sm text-zinc-900">{question.text}</div>
            {!expanded && (
              <div className="text-xs text-zinc-500 mt-2 italic">
                {question.evaluation_hint}
              </div>
            )}
          </div>

          <div
            className="flex items-center gap-1 flex-shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            <div ref={menuRef} className="relative">
              <button
                type="button"
                aria-label="Question actions"
                onClick={(e) => {
                  e.stopPropagation()
                  setMenuOpen((v) => !v)
                }}
                className="p-1.5 rounded-md hover:bg-zinc-100 text-zinc-500"
              >
                <MoreVertical className="w-4 h-4" />
              </button>
              {menuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full mt-1 w-48 bg-white border border-zinc-200 rounded-lg shadow-lg py-1 z-20"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      if (
                        confirm(
                          'Replace this question with a new AI-generated one? Your edits will be lost.',
                        )
                      ) {
                        regenMutation.mutate({})
                      }
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-700 hover:bg-zinc-50 text-left"
                  >
                    <RefreshCcw className="w-4 h-4" />
                    Regenerate
                  </button>
                  <div className="my-1 border-t border-zinc-100" />
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      if (confirm('Delete this question? Cannot be undone.')) {
                        deleteMutation.mutate(question.id)
                      }
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 text-left"
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              aria-label={expanded ? 'Collapse' : 'Expand'}
              className="p-1.5 rounded-md hover:bg-zinc-100 text-zinc-500"
            >
              <ChevronDown
                className={`w-4 h-4 transition-transform ${
                  expanded ? 'rotate-180' : ''
                }`}
              />
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-zinc-100 p-4 bg-zinc-50 space-y-4">
          <QuestionEditForm
            jobId={jobId}
            stageId={stageId}
            question={question}
          />
          <QuestionRubricExpanded question={question} />
        </div>
      )}
    </div>
  )
}
