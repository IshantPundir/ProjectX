import type { SignalItem } from '@/lib/api/jobs'

export type SignalWithIndex = SignalItem & { _i: number }

export function groupSignals(signals: SignalItem[]): {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
} {
  const must: SignalWithIndex[] = []
  const nice: SignalWithIndex[] = []
  signals.forEach((s, i) => {
    const withIdx = { ...s, _i: i }
    if (s.knockout || s.priority === 'required') must.push(withIdx)
    else nice.push(withIdx)
  })
  return { must, nice }
}
