'use client'

import { useRef, useState } from 'react'
import { ChevronDown, MoreVertical, RefreshCcw, Trash2 } from 'lucide-react'
import { DangerConfirmDialog } from '@/components/px'
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
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [confirmingRegenerate, setConfirmingRegenerate] = useState(false)
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
    <div
      className="overflow-visible rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
        boxShadow: 'var(--px-shadow-sm)',
      }}
    >
      <div className="cursor-pointer p-4" onClick={onToggleExpand}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span
                className="px-mono text-[11px] font-semibold"
                style={{ color: 'var(--px-fg-4)' }}
              >
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
            <div
              className="text-[13.5px]"
              style={{ color: 'var(--px-fg)', lineHeight: 1.55 }}
            >
              {question.text}
            </div>
            {!expanded && (
              <div
                className="mt-2 text-[11.5px] italic"
                style={{ color: 'var(--px-fg-3)' }}
              >
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
                      setConfirmingRegenerate(true)
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
                      setConfirmingDelete(true)
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
        <div
          className="space-y-4 border-t p-4"
          style={{
            background: 'var(--px-bg-2)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          {/*
            Force remount on question identity change (e.g. after regeneration
            returns a new question.id). Without this, QuestionEditForm's
            useState initializers never re-run, so stale local text can be
            flushed by the 800ms debounced autosave, overwriting the freshly
            regenerated question text.
          */}
          <QuestionEditForm
            key={question.id}
            jobId={jobId}
            stageId={stageId}
            question={question}
          />
          <QuestionRubricExpanded question={question} />
        </div>
      )}

      <DangerConfirmDialog
        open={confirmingRegenerate}
        title="Regenerate question"
        description="Replace this question with a new AI-generated one? Your edits will be lost."
        confirmLabel="Regenerate"
        pendingLabel="Regenerating…"
        pending={regenMutation.isPending}
        onConfirm={async () => {
          try {
            await regenMutation.mutateAsync({})
            setConfirmingRegenerate(false)
          } catch {
            // hook surfaces error via toast; keep dialog open
          }
        }}
        onClose={() => setConfirmingRegenerate(false)}
      />

      <DangerConfirmDialog
        open={confirmingDelete}
        title="Delete question"
        description="Delete this question? This cannot be undone."
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={deleteMutation.isPending}
        onConfirm={async () => {
          try {
            await deleteMutation.mutateAsync(question.id)
            setConfirmingDelete(false)
          } catch {
            // hook surfaces error via toast; keep dialog open
          }
        }}
        onClose={() => setConfirmingDelete(false)}
      />
    </div>
  )
}
