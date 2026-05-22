'use client'

import { useState } from 'react'
import type { BankWithQuestionsResponse, QuestionResponse } from '@/lib/api/question-banks'
import { QuestionCard } from './QuestionCard'
import { SectionStatus } from './SectionStatus'

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
}

/**
 * Maps each question_kind to the section it belongs to.
 * Mirrors the backend PHASE_QUESTION_KINDS partition.
 */
const PHASE_OF: Record<QuestionResponse['question_kind'], 'behavioral' | 'technical'> = {
  experience_check: 'behavioral',
  behavioral: 'behavioral',
  compliance_binary: 'behavioral',
  technical_scenario: 'technical',
}

const SECTION_LABELS: Record<'behavioral' | 'technical', string> = {
  behavioral: 'Behavioral',
  technical: 'Technical',
}

export function QuestionList({ jobId, stageId, bank }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // Whole-bank empty state — shown when there are zero questions at all.
  if (bank.questions.length === 0) {
    return (
      <div className="bg-white border border-dashed border-zinc-300 rounded-lg p-12 text-center">
        <div className="text-sm text-zinc-500">
          No questions yet. Click &quot;Generate questions&quot; above to start.
        </div>
      </div>
    )
  }

  // Partition questions into phase buckets, preserving existing order.
  const sections: Record<'behavioral' | 'technical', QuestionResponse[]> = {
    behavioral: [],
    technical: [],
  }
  for (const q of bank.questions) {
    const phase = PHASE_OF[q.question_kind]
    sections[phase].push(q)
  }

  const phases: Array<'behavioral' | 'technical'> = ['behavioral', 'technical']

  return (
    <div className="space-y-6">
      {phases.map((phase) => {
        const questions = sections[phase]
        // Only render sections that have questions in them.
        if (questions.length === 0) return null

        const sectionStatus = bank.generation_status_by_kind[phase]

        return (
          <section key={phase} aria-label={SECTION_LABELS[phase]}>
            <div className="mb-3 flex items-center gap-2">
              <h3
                className="text-[11px] font-bold uppercase tracking-widest"
                style={{ color: 'var(--px-fg-4)' }}
              >
                {SECTION_LABELS[phase]}
              </h3>
              <SectionStatus status={sectionStatus} />
            </div>
            <div className="space-y-3">
              {questions.map((question) => (
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
          </section>
        )
      })}
    </div>
  )
}
