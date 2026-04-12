'use client'

import type { StageDifficulty } from '@/lib/api/pipelines'

type Props = {
  value: StageDifficulty
  onChange: (value: StageDifficulty) => void
  id?: string
}

const STOPS: { value: StageDifficulty; label: string }[] = [
  { value: 'easy', label: 'Easy' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Hard' },
]

// Tailwind classnames must be static literals so JIT picks them up.
const TRACK_FILL: Record<StageDifficulty, string> = {
  easy: 'bg-emerald-500',
  medium: 'bg-amber-500',
  hard: 'bg-red-500',
}

const LABEL_ACTIVE: Record<StageDifficulty, string> = {
  easy: 'text-emerald-600',
  medium: 'text-amber-600',
  hard: 'text-red-600',
}

const DOT_ACTIVE: Record<StageDifficulty, string> = {
  easy: 'bg-emerald-500 border-emerald-500',
  medium: 'bg-amber-500 border-amber-500',
  hard: 'bg-red-500 border-red-500',
}

export function DifficultySlider({ value, onChange, id }: Props) {
  const currentIndex = STOPS.findIndex((s) => s.value === value)
  const fillWidth = currentIndex === 0 ? '8%' : currentIndex === 1 ? '50%' : '100%'

  return (
    <div id={id} role="radiogroup" aria-label="Difficulty" className="space-y-2">
      {/* Track */}
      <div className="relative h-2 bg-zinc-200 rounded-full mx-2">
        {/* Fill */}
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-all duration-200 ease-out ${TRACK_FILL[value]}`}
          style={{ width: fillWidth }}
        />
        {/* Stop dots */}
        {STOPS.map((stop, i) => {
          const leftPct = i === 0 ? 0 : i === 1 ? 50 : 100
          const isFilled = i <= currentIndex
          const isCurrent = i === currentIndex
          return (
            <button
              key={stop.value}
              type="button"
              role="radio"
              aria-checked={isCurrent}
              aria-label={stop.label}
              onClick={() => onChange(stop.value)}
              className={`absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-zinc-400 ${
                isCurrent
                  ? `w-5 h-5 shadow-md ${DOT_ACTIVE[stop.value]}`
                  : isFilled
                  ? `w-4 h-4 ${DOT_ACTIVE[stop.value]}`
                  : 'w-4 h-4 bg-white border-zinc-300'
              }`}
              style={{ left: `${leftPct}%` }}
            />
          )
        })}
      </div>
      {/* Labels row */}
      <div className="flex justify-between text-xs font-medium px-1">
        {STOPS.map((stop) => (
          <button
            key={stop.value}
            type="button"
            onClick={() => onChange(stop.value)}
            className={`transition ${value === stop.value ? LABEL_ACTIVE[stop.value] : 'text-zinc-400 hover:text-zinc-600'}`}
          >
            {stop.label}
          </button>
        ))}
      </div>
    </div>
  )
}
