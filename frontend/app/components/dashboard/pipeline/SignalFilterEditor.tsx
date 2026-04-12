'use client'

import type { SignalFilter } from '@/lib/api/pipelines'

const TYPE_OPTIONS: ('competency' | 'experience' | 'credential' | 'behavioral')[] = [
  'competency',
  'experience',
  'credential',
  'behavioral',
]
const STAGE_OPTIONS: ('screen' | 'interview')[] = ['screen', 'interview']
const WEIGHT_OPTIONS: (1 | 2 | 3)[] = [1, 2, 3]
const PRIORITY_OPTIONS: ('required' | 'preferred')[] = ['required', 'preferred']

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
        <div className="text-xs font-medium text-zinc-700 mb-1">Signal types</div>
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
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Signal stages</div>
        <div className="flex flex-wrap gap-2">
          {STAGE_OPTIONS.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={value.include_stages.includes(s)}
              onClick={() =>
                onChange({ ...value, include_stages: toggle(value.include_stages, s) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_stages.includes(s)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Weights</div>
        <div className="flex flex-wrap gap-2">
          {WEIGHT_OPTIONS.map((w) => (
            <button
              key={w}
              type="button"
              aria-pressed={value.include_weights.includes(w)}
              onClick={() =>
                onChange({ ...value, include_weights: toggle(value.include_weights, w) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_weights.includes(w)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              w{w}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Priority</div>
        <div className="flex flex-wrap gap-2">
          {PRIORITY_OPTIONS.map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={value.include_priority.includes(p)}
              onClick={() =>
                onChange({ ...value, include_priority: toggle(value.include_priority, p) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_priority.includes(p)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
