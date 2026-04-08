'use client'

import type { SignalItem, SignalSnapshot } from '@/lib/api/jobs'
import { SignalChip } from './SignalChip'

type Props = {
  snapshot: SignalSnapshot
}

function Section({
  label,
  items,
}: {
  label: string
  items: SignalItem[]
}) {
  if (items.length === 0) return null
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
        {label}
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item, i) => (
          <SignalChip key={`${label}-${i}-${item.value}`} item={item} />
        ))}
      </div>
    </div>
  )
}

export function SignalsPanel({ snapshot }: Props) {
  return (
    <aside className="col-span-1 bg-white rounded-lg border border-zinc-200 p-5 space-y-5 overflow-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 pb-2 border-b border-zinc-100">
        Signals
      </h3>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
          Role Summary
        </h4>
        <p className="text-xs text-zinc-700 leading-relaxed">
          {snapshot.role_summary}
        </p>
      </div>

      <Section label="Required Skills" items={snapshot.required_skills} />
      <Section label="Preferred Skills" items={snapshot.preferred_skills} />
      <Section label="Must Haves" items={snapshot.must_haves} />
      <Section label="Good to Haves" items={snapshot.good_to_haves} />

      <div className="pt-3 border-t border-zinc-100 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-zinc-400 uppercase tracking-wide">
            Min Experience
          </div>
          <div className="text-zinc-900 font-semibold mt-0.5">
            {snapshot.min_experience_years} yrs
          </div>
        </div>
        <div>
          <div className="text-zinc-400 uppercase tracking-wide">Seniority</div>
          <div className="text-zinc-900 font-semibold mt-0.5 capitalize">
            {snapshot.seniority_level}
          </div>
        </div>
      </div>
    </aside>
  )
}
