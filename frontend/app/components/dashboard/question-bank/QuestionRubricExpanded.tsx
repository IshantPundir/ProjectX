'use client'

import type { QuestionResponse } from '@/lib/api/question-banks'

type Props = { question: QuestionResponse }

export function QuestionRubricExpanded({ question }: Props) {
  return (
    <div className="space-y-4 text-xs">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="font-semibold text-emerald-700 mb-1">
            ✓ Listen for:
          </div>
          <ul className="list-disc pl-4 space-y-0.5 text-zinc-600">
            {question.positive_evidence.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="font-semibold text-red-700 mb-1">⚠ Red flags:</div>
          <ul className="list-disc pl-4 space-y-0.5 text-zinc-600">
            {question.red_flags.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      </div>

      {question.follow_ups.length > 0 && (
        <div>
          <div className="font-semibold text-zinc-900 mb-1">
            Follow-up probes:
          </div>
          <ul className="space-y-0.5 text-zinc-600">
            {question.follow_ups.map((item, i) => (
              <li key={i}>→ {item}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="font-semibold text-zinc-900 mb-1">Rubric:</div>
        <div className="space-y-1.5">
          <div className="bg-emerald-50 border border-emerald-200 rounded px-2 py-1">
            <span className="font-semibold text-emerald-700">Excellent:</span>{' '}
            <span className="text-zinc-700">{question.rubric.excellent}</span>
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded px-2 py-1">
            <span className="font-semibold text-amber-700">Meets bar:</span>{' '}
            <span className="text-zinc-700">{question.rubric.meets_bar}</span>
          </div>
          <div className="bg-red-50 border border-red-200 rounded px-2 py-1">
            <span className="font-semibold text-red-700">Below bar:</span>{' '}
            <span className="text-zinc-700">{question.rubric.below_bar}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
