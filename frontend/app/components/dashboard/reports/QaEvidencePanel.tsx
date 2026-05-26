'use client'

import { useState } from 'react'

import type { QuestionScorecard } from '@/lib/api/reports'
import { EvidenceQuote } from './EvidenceQuote'

const LEVEL_LABEL: Record<QuestionScorecard['level'], string> = {
  excellent: 'Excellent', meets_bar: 'Meets bar', below_bar: 'Below bar', not_assessed: 'Not assessed',
}

/**
 * The A2 stand-in for a full transcript (no recruiter transcript endpoint
 * exists yet). Built entirely from question_scorecards: the agent's
 * question text + the grounded evidence quotes. Gains a real "Transcript"
 * tab when the backend exposes sessions.transcript (deferred).
 */
export function QaEvidencePanel({ questionScorecards }: { questionScorecards: QuestionScorecard[] }) {
  const [tab, setTab] = useState<'qa' | 'evidence'>('qa')
  const allEvidence = questionScorecards.flatMap((q) => q.evidence)

  return (
    <section className="rounded-xl border bg-white p-3.5" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Questions and evidence">
      <div role="tablist" aria-label="Q&A and evidence" className="mb-2.5 flex gap-1 rounded-lg p-0.5" style={{ background: 'var(--px-bg-2)' }}>
        <button role="tab" aria-selected={tab === 'qa'} onClick={() => setTab('qa')}
          className="flex-1 rounded-md py-1 text-[10.5px] font-medium"
          style={{ background: tab === 'qa' ? 'var(--px-surface)' : 'transparent', color: tab === 'qa' ? 'var(--px-fg)' : 'var(--px-fg-3)' }}>
          Q&amp;A
        </button>
        <button role="tab" aria-selected={tab === 'evidence'} onClick={() => setTab('evidence')}
          className="flex-1 rounded-md py-1 text-[10.5px] font-medium"
          style={{ background: tab === 'evidence' ? 'var(--px-surface)' : 'transparent', color: tab === 'evidence' ? 'var(--px-fg)' : 'var(--px-fg-3)' }}>
          Evidence ({allEvidence.length})
        </button>
      </div>

      {tab === 'qa' ? (
        <div className="space-y-3">
          {questionScorecards.map((q) => (
            <div key={q.question_id}>
              <div className="flex items-start justify-between gap-2">
                <p className="text-[11.5px] font-medium" style={{ color: 'var(--px-fg)' }}>{q.question_text}</p>
                <span className="shrink-0 text-[9px]" style={{ color: 'var(--px-fg-4)' }}>{LEVEL_LABEL[q.level]}</span>
              </div>
              {q.evidence.map((e, i) => <EvidenceQuote key={i} evidence={e} />)}
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-1.5">
          {allEvidence.length === 0
            ? <p className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>No grounded evidence captured.</p>
            : allEvidence.map((e, i) => <EvidenceQuote key={i} evidence={e} />)}
        </div>
      )}
    </section>
  )
}
