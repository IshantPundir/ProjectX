'use client'

import { Confidence } from './Confidence'
import { SourceBadge } from './SourceBadge'
import { needsReview } from '../helpers/needsReview'
import { weightToConfidence } from '../helpers/weightToConfidence'
import type { SignalWithIndex } from '../helpers/groupSignals'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this row gets its own minimal copy + just the icon path it needs.
function I({
  d,
  size = 14,
  stroke = 1.6,
}: {
  d: string | readonly string[]
  size?: number
  stroke?: number
}) {
  const paths = Array.isArray(d) ? d : [d as string]
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  )
}

const WARN_ICON =
  'M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01'
const MORE_ICON = ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'] as const

export function SignalRow({
  s,
  rowId,
  focused,
  onClick,
}: {
  s: SignalWithIndex
  rowId?: string
  focused: boolean
  onClick: () => void
}) {
  const confidence = weightToConfidence(s.weight)
  const flagReview = needsReview(s)
  return (
    <button
      id={rowId}
      type="button"
      onClick={onClick}
      aria-current={focused ? 'true' : undefined}
      className="grid w-full cursor-pointer scroll-mt-4 items-center gap-3"
      style={{
        // Layout (lifted verbatim from prior <div>)
        gridTemplateColumns: '84px 58px 1fr 120px 30px',
        minHeight: 'var(--px-row-h)',
        padding: 'var(--px-row-py) 14px',
        background: focused ? 'var(--px-accent-tint)' : 'transparent',
        // Reset native button defaults so the visual matches the prior <div>
        appearance: 'none',
        border: 'none', // suppress UA default; per-side borders below win where needed
        borderBottom: '1px solid var(--px-hairline)',
        borderLeft: focused ? '2px solid var(--px-accent)' : '2px solid transparent',
        transition: 'background 120ms',
        font: 'inherit',
        color: 'inherit',
        textAlign: 'left',
      }}
    >
      <SourceBadge kind={s.source} />
      {s.knockout ? (
        <span
          className="px-chip danger"
          style={{ height: 20, padding: '0 7px', fontSize: 10, fontWeight: 700, letterSpacing: 0.3 }}
        >
          MUST
        </span>
      ) : (
        <span />
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        <span
          className="text-[14px] font-medium"
          style={{ color: 'var(--px-fg)' }}
        >
          {s.value}
        </span>
        {s.evaluation_hint && (
          <span
            className="truncate text-[11.5px] italic"
            style={{
              color: 'var(--px-fg-3)',
              paddingLeft: 10,
              marginLeft: 2,
              borderLeft: '2px solid var(--px-surface-3)',
              maxWidth: 280,
            }}
          >
            {s.evaluation_hint}
          </span>
        )}
        {flagReview && (
          <span
            className="px-chip caution"
            style={{ height: 18, padding: '0 6px', fontSize: 10 }}
          >
            <I d={WARN_ICON} size={9} />
            double-check
          </span>
        )}
      </div>
      <div className="flex justify-end">
        <Confidence value={confidence} />
      </div>
      <span
        aria-hidden="true"
        className="px-btn ghost xs"
        style={{ width: 26, padding: 0, justifyContent: 'center' }}
      >
        <I d={MORE_ICON} size={12} />
      </span>
    </button>
  )
}
