'use client'

import type { PassCriteria } from '@/lib/api/pipelines'

type Props = {
  value: PassCriteria
  onChange: (value: PassCriteria) => void
}

export function PassCriteriaEditor({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <select
        value={value.type}
        onChange={(e) => {
          const t = e.target.value as PassCriteria['type']
          if (t === 'all_knockouts_pass') onChange({ type: 'all_knockouts_pass' })
          else if (t === 'manual_review') onChange({ type: 'manual_review' })
          else onChange({ type: 'score_threshold', threshold: 70 })
        }}
        className="w-full text-xs border border-zinc-200 rounded px-2 py-1.5"
      >
        <option value="all_knockouts_pass">All knockouts pass</option>
        <option value="score_threshold">Score threshold</option>
        <option value="manual_review">Manual review</option>
      </select>
      {value.type === 'score_threshold' && (
        <input
          type="number"
          min={0}
          max={100}
          value={value.threshold}
          onChange={(e) =>
            onChange({ type: 'score_threshold', threshold: parseInt(e.target.value) || 0 })
          }
          className="w-full text-xs border border-zinc-200 rounded px-2 py-1.5"
          placeholder="Threshold (0-100)"
        />
      )}
    </div>
  )
}
