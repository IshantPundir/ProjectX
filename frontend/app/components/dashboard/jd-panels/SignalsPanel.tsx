'use client'

import type { SignalItem, SignalSnapshot } from '@/lib/api/jobs'

type Props = {
  snapshot: SignalSnapshot
}

/**
 * v4 SignalsPanel — matches JDReview.jsx layout:
 * - Grouped by priority (Must-haves / Nice-to-haves)
 * - Each row: source badge + MUST knockout chip + label + detail + 10-tick
 *   confidence bar + more menu
 * - Weight normalization: backend stores `1|2|3`; design renders `0..1`.
 *   We map weight→confidence as weight/3.
 */

function normalizeConfidence(weight: number): number {
  // Weight 1/2/3 → 0.33/0.66/1.0
  return Math.max(0, Math.min(1, weight / 3))
}

const SOURCE_COPY: Record<
  SignalItem['source'],
  { label: string; className: string; tip: string }
> = {
  ai_extracted: {
    label: 'From JD',
    className: 'px-chip ai',
    tip: 'Pulled directly from the job description.',
  },
  ai_inferred: {
    label: 'Suggested',
    className: 'px-chip caution',
    tip: "Copilot inferred this — worth a quick look.",
  },
  recruiter: {
    label: 'You added',
    className: 'px-chip human',
    tip: 'You added this manually.',
  },
}

function SourceBadge({ source }: { source: SignalItem['source'] }) {
  const m = SOURCE_COPY[source]
  return (
    <span
      className={m.className}
      title={m.tip}
      style={{
        height: 20,
        padding: '0 7px',
        fontSize: 10.5,
        fontWeight: 600,
        letterSpacing: '0.2px',
      }}
    >
      {m.label}
    </span>
  )
}

function Confidence({ value }: { value: number }) {
  const filled = Math.round(value * 10)
  const color =
    value >= 0.75
      ? 'var(--px-ok)'
      : value >= 0.5
        ? 'var(--px-caution)'
        : 'var(--px-danger)'
  return (
    <span className="inline-flex items-center" style={{ gap: 2 }}>
      <span className="inline-flex items-center" style={{ gap: 2, height: 12 }}>
        {Array.from({ length: 10 }).map((_, i) => (
          <span
            key={i}
            style={{
              width: 3,
              height: 3 + (i % 3),
              background: i < filled ? color : 'var(--px-surface-3)',
              borderRadius: 1,
              display: 'inline-block',
            }}
          />
        ))}
      </span>
      <span
        className="px-mono text-[10.5px]"
        style={{
          color: 'var(--px-fg-3)',
          marginLeft: 5,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {Math.round(value * 100)}%
      </span>
    </span>
  )
}

function SignalRow({ item }: { item: SignalItem }) {
  const conf = normalizeConfidence(item.weight)
  // Per our analysis: synthesize `needs_review` from low-weight + ai_inferred.
  const needsReview = item.source === 'ai_inferred' && conf < 0.6

  return (
    <div
      className="grid cursor-pointer items-center gap-3"
      style={{
        gridTemplateColumns: '84px 58px 1fr 140px',
        minHeight: 'var(--px-row-h)',
        padding: 'var(--px-row-py) 14px',
        borderBottom: '1px solid var(--px-hairline)',
      }}
    >
      <SourceBadge source={item.source} />
      {item.knockout ? (
        <span
          className="px-chip danger"
          style={{
            height: 20,
            padding: '0 7px',
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.3px',
          }}
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
          {item.value}
        </span>
        {item.evaluation_hint && (
          <span className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            · {item.evaluation_hint}
          </span>
        )}
        {needsReview && (
          <span
            className="px-chip caution"
            style={{ height: 18, padding: '0 6px', fontSize: 10 }}
          >
            double-check
          </span>
        )}
      </div>
      <div className="flex justify-end">
        <Confidence value={conf} />
      </div>
    </div>
  )
}

function SignalGroup({
  title,
  helper,
  items,
  emphasis,
}: {
  title: string
  helper?: string
  items: SignalItem[]
  emphasis?: boolean
}) {
  if (items.length === 0) return null
  return (
    <section style={{ marginBottom: 'var(--px-group-gap)' }}>
      <div className="flex items-baseline gap-2.5 px-1 pb-2.5">
        <h2
          className="m-0 text-[14px] font-bold"
          style={{ letterSpacing: '-0.1px', color: 'var(--px-fg)' }}
        >
          {title}
        </h2>
        <span
          className="px-mono text-[11px]"
          style={{
            color: 'var(--px-fg-4)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {items.length}
        </span>
        {helper && (
          <span
            className="text-[11.5px] italic"
            style={{ color: 'var(--px-fg-4)' }}
          >
            · {helper}
          </span>
        )}
      </div>
      <div
        className="overflow-hidden rounded-[10px] border"
        style={{
          background: 'var(--px-surface)',
          borderColor: emphasis
            ? 'var(--px-hairline-strong)'
            : 'var(--px-hairline)',
          boxShadow: emphasis ? 'var(--px-shadow-sm)' : 'none',
        }}
      >
        {items.map((s, i) => (
          <SignalRow key={`${title}-${i}-${s.value}`} item={s} />
        ))}
      </div>
    </section>
  )
}

export function SignalsPanel({ snapshot }: Props) {
  // Must-haves = priority required (includes knockouts)
  // Nice-to-haves = priority preferred
  const mustHaves = snapshot.signals.filter((s) => s.priority === 'required')
  const niceHaves = snapshot.signals.filter((s) => s.priority === 'preferred')

  const doubleCheckCount = snapshot.signals.filter(
    (s) => s.source === 'ai_inferred' && normalizeConfidence(s.weight) < 0.6,
  ).length

  return (
    <div className="flex flex-col gap-4">
      {/* Header summary */}
      <div
        className="rounded-[10px] border px-4 py-3"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="mb-1 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Role summary
        </div>
        <p
          className="m-0 text-[13px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.55 }}
        >
          {snapshot.role_summary}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span
            className="px-chip soft"
            style={{ height: 22, padding: '0 9px' }}
          >
            Level · <b className="ml-1 capitalize">{snapshot.seniority_level}</b>
          </span>
          <span className="px-chip ok">
            <span className="px-dot" aria-hidden="true" />
            {snapshot.signals.length} signals
          </span>
          {doubleCheckCount > 0 && (
            <span className="px-chip caution">
              {doubleCheckCount} to double-check
            </span>
          )}
        </div>
      </div>

      {/* Groups */}
      <SignalGroup
        title="Must-haves"
        helper="Block progress in the interview"
        items={mustHaves}
        emphasis
      />
      <SignalGroup
        title="Nice-to-haves"
        helper="Optional follow-ups during the interview"
        items={niceHaves}
      />
    </div>
  )
}
