import type { Verdict } from '@/lib/api/reports'
import { TONE_FILL, TONE_INK, verdictMeta } from './report-format'

/** Large banded verdict word — the report headline. */
export function VerdictBand({ verdict }: { verdict: Verdict }) {
  const meta = verdictMeta(verdict)
  return (
    <div
      className="text-[22px] font-extrabold tracking-tight"
      style={{ color: TONE_INK[meta.tone] }}
    >
      {meta.label}
    </div>
  )
}

/** Compact solid chip for the top bar / dense rows. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const meta = verdictMeta(verdict)
  // High-contrast text on the saturated fill. danger/caution fills are
  // saturated enough for white; ok/human fills are pastel → use ink.
  const onFillWhite = meta.tone === 'danger'
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-0.5 text-[11px] font-bold tracking-wide px-shine"
      style={{
        background: TONE_FILL[meta.tone],
        color: onFillWhite ? '#fff' : TONE_INK[meta.tone],
      }}
    >
      {meta.label.toUpperCase()}
    </span>
  )
}
