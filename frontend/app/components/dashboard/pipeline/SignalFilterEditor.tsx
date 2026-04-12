'use client'

import type { SignalFilter } from '@/lib/api/pipelines'

const TYPE_OPTIONS: ('competency' | 'experience' | 'credential' | 'behavioral')[] = [
  'competency',
  'experience',
  'credential',
  'behavioral',
]

type Props = {
  value: SignalFilter
  onChange: (value: SignalFilter) => void
}

export function SignalFilterEditor({ value, onChange }: Props) {
  function toggle<T>(list: T[], item: T): T[] {
    return list.includes(item) ? list.filter((x) => x !== item) : [...list, item]
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Signal types probed at this stage</div>
        <div className="flex flex-wrap gap-2">
          {TYPE_OPTIONS.map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={value.include_types.includes(t)}
              onClick={() =>
                onChange({ ...value, include_types: toggle(value.include_types, t) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_types.includes(t)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          Credentials are verified via documents, not interviews. Behavioral signals are
          typically probed in human panels. For AI stages, including only competency and
          experience is usually right.
        </p>
      </div>
    </div>
  )
}
