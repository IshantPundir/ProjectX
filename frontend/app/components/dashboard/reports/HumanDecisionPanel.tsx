'use client'

import { useState } from 'react'

import { Button, Textarea } from '@/components/px'
import type { HumanDecision, HumanDecisionValue, Verdict } from '@/lib/api/reports'

interface Props {
  verdict: Verdict
  decision: HumanDecision | null
  onSubmit: (decision: HumanDecisionValue, rationale: string) => void
  isSubmitting: boolean
}

const CHOICES: { value: HumanDecisionValue; label: string }[] = [
  { value: 'advance', label: 'Advance' },
  { value: 'reject', label: 'Reject' },
  { value: 'hold', label: 'Hold' },
]

export function HumanDecisionPanel({ verdict, decision, onSubmit, isSubmitting }: Props) {
  const [editing, setEditing] = useState(false)
  const [choice, setChoice] = useState<HumanDecisionValue | null>(null)
  const [rationale, setRationale] = useState('')

  // Reset the form back to the decided view when a new decision lands
  // (React's recommended "adjust state during render on prop change" pattern —
  // avoids react-hooks/set-state-in-effect).
  const [prevDecision, setPrevDecision] = useState(decision)
  if (decision !== prevDecision) {
    setPrevDecision(decision)
    setEditing(false)
  }

  const showForm = editing || decision === null
  const canSubmit = choice !== null && rationale.trim().length > 0 && !isSubmitting

  if (!showForm && decision) {
    return (
      <section className="rounded-xl border p-3.5" style={{ borderColor: 'var(--px-accent-line)', background: 'var(--px-accent-tint)' }} aria-label="Human decision">
        <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-accent)' }}>Your decision</h2>
        <p className="text-[12px]" style={{ color: 'var(--px-fg)' }}>
          <b>Decision recorded:</b> {decision.decision.toUpperCase()}
        </p>
        <p className="mt-1 text-[11px]" style={{ color: 'var(--px-fg-2)' }}>{decision.rationale}</p>
        <p className="mt-1 text-[9.5px]" style={{ color: 'var(--px-fg-4)' }}>
          {new Date(decision.decided_at).toLocaleString()}
        </p>
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => { setEditing(true); setChoice(decision.decision); setRationale(decision.rationale) }}>
          Change decision
        </Button>
      </section>
    )
  }

  return (
    <section className="rounded-xl border p-3.5" style={{ borderColor: 'var(--px-accent-line)', background: 'var(--px-accent-tint)' }} aria-label="Human decision">
      <h2 className="mb-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-accent)' }}>
        Your decision — required, logged
      </h2>

      {verdict === 'borderline' && (
        <div className="mb-2 rounded-md px-2.5 py-2 text-[10.5px]" style={{ background: 'var(--px-human-bg)', color: 'var(--px-human)' }}>
          This candidate is <b>Borderline</b> and requires a human decision. It cannot be auto-resolved — record your call with a written rationale below.
        </div>
      )}

      <p className="mb-2 text-[10.5px]" style={{ color: 'var(--px-fg-2)' }}>
        AI recommends <b>{verdict}</b>. You decide.
      </p>

      <div className="flex gap-1.5" role="group" aria-label="Decision">
        {CHOICES.map((c) => (
          <button
            key={c.value}
            type="button"
            aria-pressed={choice === c.value}
            onClick={() => setChoice(c.value)}
            className="rounded-md border px-3 py-1 text-[11px] font-semibold"
            style={{
              borderColor: choice === c.value ? 'var(--px-accent)' : 'var(--px-hairline-strong)',
              background: choice === c.value ? 'var(--px-accent)' : 'var(--px-surface)',
              color: choice === c.value ? 'var(--px-accent-ink)' : 'var(--px-fg-2)',
            }}
          >
            {c.label}
          </button>
        ))}
      </div>

      <label htmlFor="decision-rationale" className="mt-2.5 block text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--px-fg-4)' }}>
        Rationale (required)
      </label>
      <Textarea
        id="decision-rationale"
        value={rationale}
        onChange={(e) => setRationale(e.target.value)}
        rows={3}
        className="mt-1 w-full"
        placeholder="Why this decision, with reference to the evidence above."
      />

      <Button
        type="button"
        variant="primary"
        size="sm"
        className="mt-2"
        disabled={!canSubmit}
        loading={isSubmitting}
        onClick={() => { if (choice) onSubmit(choice, rationale.trim()) }}
      >
        Record decision
      </Button>
    </section>
  )
}
