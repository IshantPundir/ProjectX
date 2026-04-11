'use client'

import type { SignalItem, SignalSnapshot, SignalType } from '@/lib/api/jobs'
import { SignalChip } from './SignalChip'

type Props = {
  snapshot: SignalSnapshot
}

const TYPE_LABELS: Record<SignalType, string> = {
  credential: 'Credentials',
  experience: 'Experience',
  competency: 'Competencies',
  behavioral: 'Behavioral',
}

/** Order types are rendered within a stage section */
const SCREEN_TYPE_ORDER: SignalType[] = ['credential', 'experience', 'competency', 'behavioral']
const INTERVIEW_TYPE_ORDER: SignalType[] = ['competency', 'behavioral', 'credential', 'experience']

function ChipGroup({
  label,
  items,
}: {
  label: string
  items: SignalItem[]
}) {
  if (items.length === 0) return null
  return (
    <div>
      <h5 className="text-[10px] font-medium uppercase tracking-wide text-zinc-400 mb-1.5">
        {label}
      </h5>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item, i) => (
          <SignalChip key={`${label}-${i}-${item.value}`} item={item} />
        ))}
      </div>
    </div>
  )
}

function StageSection({
  label,
  signals,
  typeOrder,
}: {
  label: string
  signals: SignalItem[]
  typeOrder: SignalType[]
}) {
  if (signals.length === 0) return null

  // Separate knockouts first
  const knockouts = signals.filter((s) => s.knockout)
  const nonKnockouts = signals.filter((s) => !s.knockout)

  // Group non-knockouts by type
  const byType = new Map<SignalType, SignalItem[]>()
  for (const s of nonKnockouts) {
    const list = byType.get(s.type) ?? []
    list.push(s)
    byType.set(s.type, list)
  }

  // Sort competencies by weight descending within interview
  for (const [type, items] of byType) {
    if (type === 'competency') {
      items.sort((a, b) => b.weight - a.weight)
    }
    byType.set(type, items)
  }

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
        {label}
      </h4>
      {knockouts.length > 0 && (
        <ChipGroup label="Knockouts" items={knockouts} />
      )}
      {typeOrder.map((type) => {
        const items = byType.get(type)
        if (!items || items.length === 0) return null
        return <ChipGroup key={type} label={TYPE_LABELS[type]} items={items} />
      })}
    </div>
  )
}

export function SignalsPanel({ snapshot }: Props) {
  const screenSignals = snapshot.signals.filter((s) => s.stage === 'screen')
  const interviewSignals = snapshot.signals.filter((s) => s.stage === 'interview')

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-zinc-400 uppercase tracking-wide">Seniority</div>
          <div className="text-zinc-900 font-semibold mt-0.5 capitalize">
            {snapshot.seniority_level}
          </div>
        </div>
      </div>

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-2">
          Role Summary
        </h4>
        <p className="text-xs text-zinc-700 leading-relaxed">
          {snapshot.role_summary}
        </p>
      </div>

      <StageSection
        label="Phone Screen"
        signals={screenSignals}
        typeOrder={SCREEN_TYPE_ORDER}
      />
      <StageSection
        label="Deep Interview"
        signals={interviewSignals}
        typeOrder={INTERVIEW_TYPE_ORDER}
      />
    </div>
  )
}
