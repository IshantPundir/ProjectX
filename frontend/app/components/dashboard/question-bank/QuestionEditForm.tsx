'use client'

import { useEffect, useRef, useState } from 'react'
import type { QuestionResponse } from '@/lib/api/question-banks'
import { useUpdateQuestion } from '@/lib/hooks/use-save-question'

type Props = {
  jobId: string
  stageId: string
  question: QuestionResponse
}

const DEBOUNCE_MS = 800

export function QuestionEditForm({ jobId, stageId, question }: Props) {
  const updateMutation = useUpdateQuestion(jobId, stageId, question.id)
  const [text, setText] = useState(question.text)
  const [hint, setHint] = useState(question.evaluation_hint)
  const saveTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
      }
    }
  }, [])

  function schedule(body: { text?: string; evaluation_hint?: string }) {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      updateMutation.mutate(body)
      saveTimerRef.current = null
    }, DEBOUNCE_MS)
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-[10px] font-semibold uppercase text-zinc-500 mb-1">
          Question text
        </label>
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            schedule({ text: e.target.value })
          }}
          className="w-full text-sm border border-zinc-200 rounded px-3 py-2 resize-none"
          rows={3}
        />
      </div>
      <div>
        <label className="block text-[10px] font-semibold uppercase text-zinc-500 mb-1">
          Evaluation hint
        </label>
        <textarea
          value={hint}
          onChange={(e) => {
            setHint(e.target.value)
            schedule({ evaluation_hint: e.target.value })
          }}
          className="w-full text-xs border border-zinc-200 rounded px-3 py-2 resize-none"
          rows={2}
        />
      </div>
    </div>
  )
}
