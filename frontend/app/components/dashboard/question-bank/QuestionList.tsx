'use client'

import { useState } from 'react'
import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'
import { QuestionCard } from './QuestionCard'

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
}

export function QuestionList({ jobId, stageId, bank }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  if (bank.questions.length === 0) {
    return (
      <div className="bg-white border border-dashed border-zinc-300 rounded-lg p-12 text-center">
        <div className="text-sm text-zinc-500">
          No questions yet. Click &quot;Generate questions&quot; above to start.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {bank.questions.map((question) => (
        <QuestionCard
          key={question.id}
          jobId={jobId}
          stageId={stageId}
          question={question}
          expanded={expandedId === question.id}
          onToggleExpand={() =>
            setExpandedId(expandedId === question.id ? null : question.id)
          }
        />
      ))}
    </div>
  )
}
